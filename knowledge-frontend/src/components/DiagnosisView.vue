<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  ChatDotRound,
  Clock,
  Document,
  FolderOpened,
  Reading,
  Search,
  Tickets,
} from '@element-plus/icons-vue';
import { documentPath, workspacePath } from '../api.js';

const props = defineProps({
  api: { type: Function, required: true },
  workspace: { type: String, required: true },
  documents: { type: Object, required: true },
});

const SAMPLE_QUESTIONS = [
  'Nebula Gateway 3.2 的默认空闲超时是多少？',
  '出现 E_CONN_TIMEOUT 告警时应该怎么排查？',
  'Aurora Auth 2.4 出现 E_TOKEN_SKEW 怎么处理？',
];

const DOC_TYPES = [
  { value: 'release_note', label: '版本说明' },
  { value: 'sop', label: '运维 SOP' },
  { value: 'ticket', label: '历史工单' },
  { value: 'runbook', label: '排障手册' },
  { value: 'guide', label: '技术指南' },
];

const STAGE_LABELS = {
  retrieval_started: '开始检索',
  query_classified: '问题分类',
  query_rewritten: '检索改写',
  documents_retrieved: '召回候选片段',
  answer_generating: '生成回答',
  answer_verification_failed: '引用校验未通过，重试中',
  answer_completed: '引用核验通过',
  answer_rejected: '证据不足，结构化拒答',
};

const mode = ref('ask');
const scope = ref('');
const docFilter = ref('');
const filterDocTypes = ref([]);
const filterProductName = ref('');
const filterProductVersion = ref('');
const query = ref('');
const busy = ref(false);
const error = ref('');
const result = ref(null);
const streaming = ref(false);
const evidence = ref(null);
const stages = ref([]);
const historyDrawer = ref(false);
const history = ref([]);

let activeRequest = null;

const modeOptions = [
  { label: '证据问答', value: 'ask' },
  { label: '文档检索', value: 'search' },
];

function historyStorageKey(ws) {
  return `supportdesk.history.${ws}`;
}

function loadHistory(ws) {
  try {
    const raw = sessionStorage.getItem(historyStorageKey(ws));
    history.value = raw ? JSON.parse(raw) : [];
  } catch {
    history.value = [];
  }
}

function saveHistoryEntry(entry) {
  const next = [entry, ...history.value].slice(0, 20);
  history.value = next;
  try {
    sessionStorage.setItem(historyStorageKey(props.workspace), JSON.stringify(next));
  } catch {
    // Ignore storage quota errors.
  }
}

function clearHistory() {
  history.value = [];
  sessionStorage.removeItem(historyStorageKey(props.workspace));
}

watch(
  () => props.workspace,
  (ws) => {
    scope.value = '';
    result.value = null;
    stages.value = [];
    error.value = '';
    loadHistory(ws);
  },
  { immediate: true },
);

const published = computed(() =>
  props.documents.state.rows
    .filter((doc) => doc.publishedVersion != null)
    .map((doc) =>
      doc.publishedVersion === doc.latestVersion
        ? doc
        : { ...doc, filename: null, contentType: null },
    ),
);

const filteredPublished = computed(() => {
  const kw = docFilter.value.trim().toLowerCase();
  if (!kw) return published.value;
  return published.value.filter(
    (doc) =>
      doc.documentId.toLowerCase().includes(kw) ||
      (doc.filename && doc.filename.toLowerCase().includes(kw)),
  );
});

const activeDocument = computed(() => published.value.find((doc) => doc.documentId === scope.value));

const parsedResponse = computed(() => {
  if (!result.value) return null;
  const body = result.value.data.result;
  const sourceById = new Map((result.value.data.documents ?? []).map((doc) => [doc.indexId, doc]));
  const items = result.value.mode === 'ask' ? (body.citations ?? []) : (body.chunks ?? []);
  const sources = items.map((item) => {
    const source = sourceById.get(item.doc_id);
    return {
      title: source?.filename || source?.documentId || item.source || result.value.data.documentId || '文档引用',
      documentId: source?.documentId ?? result.value.data.documentId,
      version: source?.version ?? result.value.data.version,
      page: item.page_number,
      passage: item.snippet ?? item.text,
      chunk: item.chunk_id,
    };
  });
  const refs = new Map(
    items.map((item, index) => [item.citation_key ?? `${item.doc_id}:${item.chunk_id}`, index]),
  );
  const segments =
    result.value.mode === 'ask' && body.answer
      ? body.answer.split(/(\[[^\]\r\n]+\])/).map((part) => {
          const refIndex = refs.get(part.slice(1, -1));
          return refIndex === undefined
            ? { text: part, refIndex: null }
            : { text: part, refIndex };
        })
      : [];
  return { body, sources, segments };
});

onBeforeUnmount(() => activeRequest?.abort());

async function submit() {
  const cleaned = query.value.trim();
  if (!cleaned || busy.value) return;
  if (scope.value && !activeDocument.value) {
    error.value = '所选文档暂不可查，请重新选择检索范围。';
    return;
  }

  activeRequest?.abort();
  const controller = new AbortController();
  activeRequest = controller;
  busy.value = true;
  error.value = '';
  result.value = null;
  stages.value = [];

  try {
    const root = scope.value
      ? documentPath(props.workspace, scope.value)
      : workspacePath(props.workspace);

    const requestBody = {
      query: cleaned,
      ...(filterDocTypes.value.length ? { documentTypes: filterDocTypes.value } : {}),
      ...(filterProductName.value.trim() ? { productName: filterProductName.value.trim() } : {}),
      ...(filterProductVersion.value.trim() ? { productVersion: filterProductVersion.value.trim() } : {}),
    };

    if (mode.value === 'search') {
      const data = await props.api(`${root}/search`, {
        method: 'POST',
        data: requestBody,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        const next = { data, mode: 'search', query: cleaned, timestamp: Date.now() };
        result.value = next;
        saveHistoryEntry(next);
      }
      return;
    }

    // Streaming SSE for evidence-based Q&A
    let scopeMeta = { documents: [], documentId: scope.value || undefined, version: undefined };
    let liveAnswer = '';
    let liveCitations = [];
    let finalBody = null;
    streaming.value = false;

    await props.api.stream(`${root}/ask/stream`, {
      data: requestBody,
      signal: controller.signal,
      onEvent(eventName, payload) {
        if (eventName === 'scope') {
          scopeMeta = { ...scopeMeta, ...payload };
          return;
        }
        if (eventName === 'answer_sources') {
          liveCitations = payload.citations ?? [];
          return;
        }
        if (eventName === 'answer_delta') {
          streaming.value = true;
          liveAnswer += payload.delta ?? '';
          result.value = {
            data: {
              ...scopeMeta,
              result: { status: 'ok', answer: liveAnswer, citations: liveCitations },
            },
            mode: 'ask',
            query: cleaned,
          };
          return;
        }
        const label = STAGE_LABELS[eventName] || eventName;
        const detail =
          eventName === 'documents_retrieved' && payload.count != null
            ? `命中 ${payload.count} 条候选片段`
            : eventName === 'query_classified' && payload.query_type
              ? `类型：${payload.query_type}`
              : '';
        stages.value.push({
          event: eventName,
          label,
          detail,
          type:
            eventName === 'answer_completed'
              ? 'success'
              : eventName === 'answer_rejected' || eventName === 'answer_verification_failed'
                ? 'warning'
                : 'primary',
        });

        if (eventName === 'answer_completed') {
          streaming.value = false;
          finalBody = {
            status: 'ok',
            answer: payload.answer ?? liveAnswer,
            citations: payload.citations ?? liveCitations,
          };
        } else if (eventName === 'answer_rejected') {
          streaming.value = false;
          finalBody = {
            status: 'rejected',
            answer: payload.answer ?? '证据不足，无法基于当前可访问资料给出可靠结论。',
            citations: [],
          };
        }
      },
    });

    if (!controller.signal.aborted && finalBody) {
      const next = {
        data: { ...scopeMeta, result: finalBody },
        mode: 'ask',
        query: cleaned,
        stages: [...stages.value],
        timestamp: Date.now(),
      };
      result.value = next;
      saveHistoryEntry(next);
    }
  } catch (failure) {
    if (!controller.signal.aborted) error.value = failure.message;
  } finally {
    if (!controller.signal.aborted) {
      streaming.value = false;
      busy.value = false;
    }
  }
}

function openHistoryItem(item) {
  result.value = item;
  mode.value = item.mode;
  query.value = item.query;
  stages.value = item.stages ?? [];
  error.value = '';
  historyDrawer.value = false;
}

function followUp(item) {
  const snippet = (item.data?.result?.answer || '').slice(0, 120).replace(/\s+/g, ' ');
  mode.value = 'ask';
  query.value = snippet
    ? `基于上文结论（${snippet}），请进一步说明：`
    : `关于「${item.query}」，请进一步说明：`;
  historyDrawer.value = false;
}

function handleKeydown(event) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault();
    submit();
  }
}

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
</script>

<template>
  <div class="sd-diag-grid">
    <div class="sd-diag-main">
      <section class="sd-card sd-panel">
        <div class="sd-panel-head">
          <h2 class="sd-panel-title">
            <el-icon color="#2563eb"><ChatDotRound /></el-icon>
            <span>提出故障或技术问题</span>
          </h2>
          <div style="display: flex; align-items: center; gap: 10px">
            <el-button size="small" :icon="Clock" @click="historyDrawer = true">
              诊断历史 ({{ history.length }})
            </el-button>
            <el-segmented v-model="mode" :options="modeOptions" :disabled="busy" />
          </div>
        </div>

        <el-input
          v-model="query"
          type="textarea"
          :rows="4"
          maxlength="4096"
          :disabled="busy"
          placeholder="输入故障现象、错误码或技术问题..."
          @keydown="handleKeydown"
        />

        <div class="sd-composer-footer">
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <el-button
              v-for="sample in SAMPLE_QUESTIONS"
              :key="sample"
              size="small"
              round
              :disabled="busy"
              @click="query = sample"
            >
              {{ sample }}
            </el-button>
          </div>
          <el-button
            type="primary"
            :icon="Search"
            :loading="busy"
            :disabled="!query.trim()"
            @click="submit"
          >
            {{ mode === 'ask' ? '开始诊断' : '执行检索' }}
          </el-button>
        </div>
      </section>

      <section class="sd-card sd-panel">
        <div class="sd-panel-head">
          <h2 class="sd-panel-title">
            <el-icon color="#0f172a"><Reading /></el-icon>
            <span>{{ result?.mode === 'search' ? '检索命中片段' : '诊断结论与证据' }}</span>
          </h2>
          <div style="display: flex; align-items: center; gap: 8px">
            <el-tag v-if="parsedResponse?.sources.length" type="primary" effect="light" round>
              {{ parsedResponse.sources.length }} 条证据
            </el-tag>
            <el-button
              v-if="result && result.mode === 'ask' && !busy"
              size="small"
              plain
              type="primary"
              @click="followUp(result)"
            >
              基于此继续追问
            </el-button>
          </div>
        </div>

        <div
          v-if="stages.length"
          style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px"
        >
          <el-tag
            v-for="(stage, idx) in stages"
            :key="`${stage.event}-${idx}`"
            size="small"
            :type="stage.type"
            effect="light"
            round
          >
            {{ stage.label }}<template v-if="stage.detail"> · {{ stage.detail }}</template>
          </el-tag>
        </div>

        <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" />

        <div v-else-if="busy && !streaming" style="padding: 20px 0">
          <el-skeleton :rows="3" animated />
        </div>

        <template v-else-if="parsedResponse">
          <el-alert
            v-if="result.mode === 'ask' && parsedResponse.body.status === 'rejected'"
            type="warning"
            show-icon
            :closable="false"
            title="证据不足，已触发结构化拒答"
            :description="parsedResponse.body.answer"
          />

          <template v-else>
            <div v-if="result.mode === 'ask'" class="sd-answer-box">
              <template v-for="(seg, idx) in parsedResponse.segments" :key="idx">
                <button
                  v-if="seg.refIndex !== null"
                  type="button"
                  class="sd-cite-pill"
                  @click="evidence = parsedResponse.sources[seg.refIndex]"
                >
                  {{ seg.refIndex + 1 }}
                </button>
                <template v-else>{{ seg.text }}</template>
              </template>
              <span v-if="streaming" class="sd-caret" aria-hidden="true" />
            </div>

            <div v-if="parsedResponse.sources.length" class="sd-evidence-list">
              <button
                v-for="(src, index) in parsedResponse.sources"
                :key="`${src.documentId || src.title}-${src.chunk}-${index}`"
                type="button"
                class="sd-evidence-item"
                @click="evidence = src"
              >
                <span class="sd-file-badge" :class="{ 'is-pdf': src.page != null }">
                  <el-icon><Tickets v-if="src.page != null" /><Document v-else /></el-icon>
                </span>
                <div class="sd-evidence-body">
                  <strong>{{ src.title }}</strong>
                  <p>{{ src.passage }}</p>
                </div>
                <div style="display: flex; gap: 6px; flex-shrink: 0">
                  <el-tag v-if="src.version != null" size="small" effect="plain">v{{ src.version }}</el-tag>
                  <el-tag v-if="src.page != null" size="small" type="primary" effect="light">
                    第 {{ src.page }} 页
                  </el-tag>
                </div>
              </button>
            </div>

            <el-empty v-else-if="result.mode === 'search'" description="当前范围内没有匹配的已发布内容" />
          </template>
        </template>

        <el-empty v-else description="提交问题后在此查看结果与引用" />
      </section>
    </div>

    <aside class="sd-card sd-panel">
      <div class="sd-panel-head">
        <h2 class="sd-panel-title">
          <el-icon color="#2563eb"><FolderOpened /></el-icon>
          <span>检索范围</span>
        </h2>
        <el-tag size="small" type="primary" effect="light" round>
          {{ documents.state.cursor ? `${published.length}+` : published.length }}
        </el-tag>
      </div>

      <el-input
        v-model="docFilter"
        size="small"
        clearable
        placeholder="筛选已发布文档..."
        :prefix-icon="Search"
      />

      <div style="margin-top: 12px; padding-top: 12px; border-top: 1px dashed #e5eaf2; display: flex; flex-direction: column; gap: 8px">
        <div style="display: flex; align-items: center; justify-content: space-between">
          <span style="font-size: 13px; font-weight: 600; color: #334155">标签筛选</span>
          <el-button
            v-if="filterDocTypes.length || filterProductName || filterProductVersion"
            link
            type="primary"
            size="small"
            @click="filterDocTypes = []; filterProductName = ''; filterProductVersion = ''"
          >
            重置
          </el-button>
        </div>
        <el-select
          v-model="filterDocTypes"
          multiple
          collapse-tags
          clearable
          size="small"
          placeholder="文档类型（SOP / 版本说明...）"
          :disabled="busy"
        >
          <el-option v-for="item in DOC_TYPES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <div style="display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 6px">
          <el-input v-model="filterProductName" size="small" clearable placeholder="产品名" :disabled="busy" />
          <el-input v-model="filterProductVersion" size="small" clearable placeholder="版本" :disabled="busy" />
        </div>
      </div>

      <el-alert
        v-if="documents.state.error"
        type="error"
        :title="documents.state.error"
        show-icon
        :closable="false"
        style="margin-top: 12px"
      />

      <div v-else class="sd-scope-list">
        <button
          type="button"
          class="sd-scope-item"
          :class="{ 'is-selected': scope === '' }"
          :disabled="busy"
          @click="scope = ''"
        >
          <span class="sd-file-badge"><el-icon><FolderOpened /></el-icon></span>
          <span class="sd-scope-meta">
            <strong>全部已发布文档</strong>
          </span>
        </button>

        <button
          v-for="doc in filteredPublished"
          :key="doc.documentId"
          type="button"
          class="sd-scope-item"
          :class="{ 'is-selected': scope === doc.documentId }"
          :disabled="busy"
          @click="scope = doc.documentId"
        >
          <span class="sd-file-badge" :class="{ 'is-pdf': doc.contentType === 'application/pdf' }">
            <el-icon><Tickets v-if="doc.contentType === 'application/pdf'" /><Document v-else /></el-icon>
          </span>
          <span class="sd-scope-meta">
            <strong :title="doc.filename || doc.documentId">{{ doc.filename || doc.documentId }}</strong>
            <small>已发布 v{{ doc.publishedVersion }}</small>
          </span>
        </button>

        <div
          v-if="!documents.state.loading && !published.length"
          style="padding: 18px 8px; text-align: center; font-size: 12.5px; color: #64748b"
        >
          当前工作区暂无已发布文档，文档索引完成后会出现在这里。
        </div>
      </div>

      <el-button
        v-if="documents.state.cursor"
        text
        type="primary"
        :loading="documents.state.loading"
        style="width: 100%; margin-top: 8px"
        @click="documents.more"
      >
        加载更多文档
      </el-button>
    </aside>

    <el-drawer
      :model-value="Boolean(evidence)"
      title="引用证据溯源"
      size="min(100%, 540px)"
      @close="evidence = null"
    >
      <template v-if="evidence">
        <h3 style="margin: 0 0 6px; font-size: 18px">{{ evidence.title }}</h3>
        <p v-if="evidence.documentId" style="margin: 0 0 14px; font-size: 12.5px; color: #64748b">
          文档 ID：<span class="sd-code">{{ evidence.documentId }}</span>
        </p>
        <div style="display: flex; gap: 8px; flex-wrap: wrap">
          <el-tag v-if="evidence.version != null" type="primary" effect="light">
            发布版本 v{{ evidence.version }}
          </el-tag>
          <el-tag v-if="evidence.page != null" type="success" effect="light">
            PDF 第 {{ evidence.page }} 页
          </el-tag>
          <el-tag v-if="evidence.chunk" type="info" effect="plain">
            {{ evidence.chunk }}
          </el-tag>
        </div>
        <blockquote class="sd-quote">{{ evidence.passage }}</blockquote>
      </template>
    </el-drawer>

    <el-drawer v-model="historyDrawer" title="本会话诊断历史" size="min(100%, 460px)">
      <div v-if="history.length" style="display: flex; justify-content: flex-end; margin-bottom: 12px">
        <el-button size="small" link type="danger" @click="clearHistory">清空历史</el-button>
      </div>
      <div v-if="history.length" style="display: flex; flex-direction: column; gap: 10px">
        <div
          v-for="(item, idx) in history"
          :key="`${item.timestamp}-${idx}`"
          class="sd-card"
          style="padding: 13px 15px"
        >
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px">
            <el-tag size="small" :type="item.mode === 'ask' ? 'primary' : 'info'" effect="light">
              {{ item.mode === 'ask' ? '证据问答' : '文档检索' }}
            </el-tag>
            <span style="font-size: 12px; color: #64748b">{{ formatTime(item.timestamp) }}</span>
          </div>
          <div style="font-size: 13.5px; font-weight: 600; color: #0f172a; margin-bottom: 10px">
            {{ item.query }}
          </div>
          <div style="display: flex; gap: 8px">
            <el-button size="small" type="primary" plain @click="openHistoryItem(item)">查看结果</el-button>
            <el-button v-if="item.mode === 'ask'" size="small" @click="followUp(item)">继续追问</el-button>
          </div>
        </div>
      </div>
      <el-empty v-else description="当前工作区暂无诊断历史" />
    </el-drawer>
  </div>
</template>
