<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Delete,
  Document,
  Refresh,
  Tickets,
  Upload,
  UploadFilled,
} from '@element-plus/icons-vue';
import { documentPath } from '../api.js';
import { ongoing } from '../composables/useDocuments.js';

const props = defineProps({
  api: { type: Function, required: true },
  workspace: { type: String, required: true },
  documents: { type: Object, required: true },
  editable: { type: Boolean, default: false },
  uploadRequest: { type: Object, default: null },
  newVersion: { type: Object, default: null },
});

const emit = defineEmits(['open-upload', 'close-upload', 'uploaded', 'version-opened']);

const MAX_PDF_BYTES = 25 * 1024 * 1024;
const DOC_TYPES = [
  { value: 'release_note', label: '版本说明' },
  { value: 'sop', label: '运维 SOP' },
  { value: 'ticket', label: '历史工单' },
  { value: 'runbook', label: '排障手册' },
  { value: 'guide', label: '技术指南' },
];
const DOC_TYPE_LABELS = Object.fromEntries(DOC_TYPES.map((item) => [item.value, item.label]));
const STATUS_META = {
  PENDING: { label: '等待摄取', type: 'warning' },
  INDEXING: { label: '建立索引中', type: 'primary' },
  INDEXED: { label: '索引完成', type: 'success' },
  FAILED: { label: '摄取失败', type: 'danger' },
};

// KPI metrics
const totalCount = computed(() => props.documents.state.rows.length);
const publishedCount = computed(
  () => props.documents.state.rows.filter((doc) => doc.publishedVersion != null).length,
);
const activeOrFailedCount = computed(
  () => props.documents.state.rows.filter((doc) => ongoing(doc.status) || doc.status === 'FAILED').length,
);

// Upload modal state
const uploadMode = ref('pdf');
const uploadBusy = ref(false);
const uploadError = ref('');
const uploadFormRef = ref(null);
const uploadForm = reactive({
  id: '',
  text: '',
  documentType: '',
  productName: '',
  productVersion: '',
});
const selectedFile = ref(null);

watch(
  () => props.uploadRequest,
  (req) => {
    if (!req) return;
    uploadMode.value = 'pdf';
    uploadError.value = '';
    uploadForm.id = req.id || '';
    uploadForm.text = '';
    uploadForm.documentType = '';
    uploadForm.productName = '';
    uploadForm.productVersion = '';
    selectedFile.value = null;
    uploadFormRef.value?.clearValidate();
  },
);

function handleFileChange(uploadFile) {
  const raw = uploadFile.raw;
  if (!raw) return;
  if (raw.size > MAX_PDF_BYTES) {
    uploadError.value = 'PDF 文件不能超过 25 MiB。';
    selectedFile.value = null;
    return;
  }
  uploadError.value = '';
  selectedFile.value = raw;
}

async function submitUpload() {
  const valid = await uploadFormRef.value?.validate().catch(() => false);
  if (!valid) return;
  if (uploadMode.value === 'pdf' && !selectedFile.value) {
    uploadError.value = '请选择要上传的 PDF 文件。';
    return;
  }

  uploadBusy.value = true;
  uploadError.value = '';
  try {
    const route = `${documentPath(props.workspace, uploadForm.id.trim())}/versions`;
    let data;
    if (uploadMode.value === 'pdf') {
      data = new FormData();
      data.append('file', selectedFile.value);
      if (uploadForm.documentType) data.append('documentType', uploadForm.documentType);
      if (uploadForm.productName.trim()) data.append('productName', uploadForm.productName.trim());
      if (uploadForm.productVersion.trim()) data.append('productVersion', uploadForm.productVersion.trim());
    } else {
      data = {
        text: uploadForm.text.trim(),
        documentType: uploadForm.documentType || undefined,
        productName: uploadForm.productName.trim() || undefined,
        productVersion: uploadForm.productVersion.trim() || undefined,
      };
    }
    const version = await props.api(uploadMode.value === 'pdf' ? `${route}/pdf` : route, {
      method: 'POST',
      data,
    });
    ElMessage.success(`已提交版本 v${version.versionNumber}，正在异步建立索引`);
    emit('uploaded', version);
  } catch (failure) {
    uploadError.value = failure.message;
  } finally {
    uploadBusy.value = false;
  }
}

async function removeDocument(doc) {
  try {
    await ElMessageBox.confirm(
      `确认下架文档「${doc.documentId}」并异步清理其向量、全文索引及源文件？`,
      '下架文档',
      { type: 'warning', confirmButtonText: '确认下架', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }
  try {
    await props.api(documentPath(props.workspace, doc.documentId), { method: 'DELETE' });
    props.documents.reload();
    ElMessage.success('文档已下架');
  } catch (failure) {
    ElMessage.error(failure.message);
  }
}

// Version inspector drawer
const focused = ref(null);
const versionNumber = ref(1);
const versionStatus = ref(null);
const versionError = ref('');
const retryBusy = ref(false);

let drawerController = null;
let drawerTimer = null;

function stopDrawerPolling() {
  drawerController?.abort();
  drawerController = null;
  if (drawerTimer) {
    clearTimeout(drawerTimer);
    drawerTimer = null;
  }
}

function openVersionDrawer(doc) {
  focused.value = doc;
  versionNumber.value = doc.latestVersion;
}

watch(
  () => props.newVersion,
  (ver) => {
    if (!ver) return;
    openVersionDrawer({ documentId: ver.documentId, latestVersion: ver.versionNumber });
    emit('version-opened');
  },
);

async function fetchVersionStatus() {
  if (!focused.value) return;
  stopDrawerPolling();
  const controller = new AbortController();
  drawerController = controller;
  versionError.value = '';
  versionStatus.value = null;

  const path = `${documentPath(props.workspace, focused.value.documentId)}/versions/${versionNumber.value}`;
  async function poll() {
    try {
      const next = await props.api(path, { signal: controller.signal });
      if (controller.signal.aborted) return;
      versionStatus.value = next;
      if (ongoing(next.status)) {
        drawerTimer = setTimeout(poll, 1400);
      }
    } catch (failure) {
      if (!controller.signal.aborted) versionError.value = failure.message;
    }
  }
  poll();
}

watch([focused, versionNumber], ([doc]) => {
  if (doc) fetchVersionStatus();
  else stopDrawerPolling();
});

onBeforeUnmount(stopDrawerPolling);

async function retryVersion() {
  if (!focused.value) return;
  retryBusy.value = true;
  versionError.value = '';
  try {
    const path = `${documentPath(props.workspace, focused.value.documentId)}/versions/${versionNumber.value}/retry`;
    await props.api(path, { method: 'POST' });
    props.documents.reload();
    fetchVersionStatus();
    ElMessage.success('已重新加入索引队列');
  } catch (failure) {
    versionError.value = failure.message;
  } finally {
    retryBusy.value = false;
  }
}
</script>

<template>
  <div>
    <div class="sd-kpi-row">
      <div class="sd-card sd-kpi-card">
        <div>
          <span>已加载文档</span>
          <strong>{{ documents.state.cursor ? `${totalCount}+` : totalCount }}</strong>
        </div>
        <span class="sd-file-badge"><el-icon><Document /></el-icon></span>
      </div>
      <div class="sd-card sd-kpi-card">
        <div>
          <span>已发布可查</span>
          <strong style="color: #16a34a">{{ publishedCount }}</strong>
        </div>
        <el-tag type="success" effect="light" round>线上生效</el-tag>
      </div>
      <div class="sd-card sd-kpi-card">
        <div>
          <span>处理中 / 异常</span>
          <strong :style="{ color: activeOrFailedCount ? '#d97706' : '#0f172a' }">
            {{ activeOrFailedCount }}
          </strong>
        </div>
        <el-tag :type="activeOrFailedCount ? 'warning' : 'info'" effect="light" round>
          {{ activeOrFailedCount ? '自动轮询中' : '全部就绪' }}
        </el-tag>
      </div>
    </div>

    <section class="sd-card sd-panel">
      <div class="sd-panel-head">
        <div>
          <h2 class="sd-panel-title">文档目录</h2>
        </div>
        <el-button :icon="Refresh" :loading="documents.state.loading" @click="documents.reload">
          刷新
        </el-button>
      </div>

      <el-alert
        v-if="documents.state.error"
        type="error"
        show-icon
        :closable="false"
        :title="documents.state.error"
        style="margin-bottom: 14px"
      />

      <el-table v-loading="documents.state.loading" :data="documents.state.rows" stripe style="width: 100%">
        <el-table-column label="文档" min-width="240">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 11px">
              <span class="sd-file-badge" :class="{ 'is-pdf': row.contentType === 'application/pdf' }">
                <el-icon><Tickets v-if="row.contentType === 'application/pdf'" /><Document v-else /></el-icon>
              </span>
              <div style="min-width: 0">
                <div style="font-weight: 600; color: #0f172a">
                  {{ row.filename || row.documentId }}
                </div>
                <div v-if="row.filename" style="font-size: 12px; color: #64748b">
                  {{ row.documentId }}
                </div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="标签" min-width="170">
          <template #default="{ row }">
            <div style="display: flex; flex-wrap: wrap; gap: 4px">
              <el-tag v-if="row.documentType" size="small" effect="plain">
                {{ DOC_TYPE_LABELS[row.documentType] || row.documentType }}
              </el-tag>
              <el-tag v-if="row.productName" size="small" type="info" effect="light">
                {{ row.productName }}<template v-if="row.productVersion"> {{ row.productVersion }}</template>
              </el-tag>
              <span v-if="!row.documentType && !row.productName" style="color: #94a3b8; font-size: 12px">—</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="最新版本" width="100">
          <template #default="{ row }">
            <span class="sd-code">v{{ row.latestVersion }}</span>
          </template>
        </el-table-column>

        <el-table-column label="线上版本" width="110">
          <template #default="{ row }">
            <span v-if="row.publishedVersion != null" class="sd-code" style="background: #dcfce7; color: #166534">
              v{{ row.publishedVersion }}
            </span>
            <span v-else style="color: #94a3b8">—</span>
          </template>
        </el-table-column>

        <el-table-column label="摄取状态" width="130">
          <template #default="{ row }">
            <el-tag :type="STATUS_META[row.status]?.type || 'info'" effect="light" round>
              {{ STATUS_META[row.status]?.label || row.status }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" :width="editable ? 230 : 110" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openVersionDrawer(row)">版本详情</el-button>
            <el-button v-if="editable" link type="primary" @click="emit('open-upload', row.documentId)">
              上传新版
            </el-button>
            <el-button
              v-if="editable"
              link
              type="danger"
              :icon="Delete"
              @click="removeDocument(row)"
            />
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="当前工作区暂无文档">
            <el-button v-if="editable" type="primary" :icon="Upload" @click="emit('open-upload', '')">
              上传第一篇文档
            </el-button>
          </el-empty>
        </template>
      </el-table>

      <div v-if="documents.state.cursor" style="text-align: center; margin-top: 16px">
        <el-button :loading="documents.state.loading" @click="documents.more">加载更多</el-button>
      </div>
    </section>

    <el-dialog
      :model-value="Boolean(uploadRequest)"
      :title="uploadRequest?.id ? '提交文档新版本' : '上传新文档'"
      width="560px"
      :close-on-click-modal="false"
      @close="!uploadBusy && emit('close-upload')"
    >
      <el-alert
        v-if="uploadError"
        type="error"
        show-icon
        :closable="false"
        :title="uploadError"
        style="margin-bottom: 14px"
      />

      <el-form ref="uploadFormRef" :model="uploadForm" label-position="top">
        <el-form-item
          label="文档 ID"
          prop="id"
          :rules="[
            { required: true, message: '请输入文档 ID' },
            { pattern: /^[a-z0-9][a-z0-9-]{0,63}$/, message: '仅支持小写字母、数字和连字符，最长 64 字符' },
          ]"
        >
          <el-input
            v-model="uploadForm.id"
            placeholder="例如 gateway-timeout-sop"
            :readonly="Boolean(uploadRequest?.id)"
            :disabled="uploadBusy"
          />
        </el-form-item>

        <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px">
          <el-form-item label="文档类型（可选）">
            <el-select v-model="uploadForm.documentType" clearable placeholder="选择类型" :disabled="uploadBusy">
              <el-option v-for="item in DOC_TYPES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="产品名称（可选）">
            <el-input v-model="uploadForm.productName" maxlength="128" placeholder="Nebula Gateway" :disabled="uploadBusy" />
          </el-form-item>
          <el-form-item label="产品版本（可选）">
            <el-input v-model="uploadForm.productVersion" maxlength="64" placeholder="3.2" :disabled="uploadBusy" />
          </el-form-item>
        </div>

        <el-form-item label="内容格式">
          <el-segmented
            v-model="uploadMode"
            :options="[
              { label: 'PDF 文档', value: 'pdf' },
              { label: '纯文本', value: 'text' },
            ]"
            :disabled="uploadBusy"
          />
        </el-form-item>

        <el-form-item v-if="uploadMode === 'pdf'">
          <el-upload
            drag
            action="#"
            :auto-upload="false"
            :limit="1"
            accept=".pdf,application/pdf"
            :on-change="handleFileChange"
            :on-remove="() => { selectedFile = null; }"
            style="width: 100%"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">将 PDF 拖到此处，或 <em>点击选择文件</em></div>
            <template #tip>
              <div class="el-upload__tip">支持含文本层的 PDF，单文件最大 25 MiB</div>
            </template>
          </el-upload>
        </el-form-item>

        <el-form-item
          v-else
          label="正文内容"
          prop="text"
          :rules="[{ required: true, whitespace: true, message: '请输入文档正文' }]"
        >
          <el-input
            v-model="uploadForm.text"
            type="textarea"
            :rows="7"
            maxlength="100000"
            show-word-limit
            placeholder="粘贴版本说明、SOP 或历史工单正文..."
            :disabled="uploadBusy"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button :disabled="uploadBusy" @click="emit('close-upload')">取消</el-button>
        <el-button type="primary" :loading="uploadBusy" @click="submitUpload">提交摄取</el-button>
      </template>
    </el-dialog>

    <el-drawer
      :model-value="Boolean(focused)"
      title="文档版本详情"
      size="min(100%, 460px)"
      @close="focused = null"
    >
      <template v-if="focused">
        <h3 style="margin: 0 0 4px; font-size: 18px">{{ focused.filename || focused.documentId }}</h3>
        <p v-if="focused.filename" style="margin: 0 0 18px; font-size: 12.5px; color: #64748b">
          {{ focused.documentId }}
        </p>

        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 18px">
          <span style="font-size: 13.5px; color: #475569">查看版本号</span>
          <el-input-number v-model="versionNumber" :min="1" :max="focused.latestVersion" :precision="0" />
        </div>

        <el-alert
          v-if="versionError"
          type="error"
          show-icon
          :closable="false"
          :title="versionError"
          style="margin-bottom: 14px"
        />

        <el-skeleton v-else-if="!versionStatus" :rows="3" animated />

        <el-descriptions v-else :column="1" border>
          <el-descriptions-item label="摄取状态">
            <el-tag :type="STATUS_META[versionStatus.status]?.type || 'info'" effect="light" round>
              {{ STATUS_META[versionStatus.status]?.label || versionStatus.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="线上已发布">
            <span class="sd-code">
              {{ versionStatus.publishedVersion == null ? '—' : `v${versionStatus.publishedVersion}` }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="执行次数">
            {{ versionStatus.attempt }}
          </el-descriptions-item>
          <el-descriptions-item v-if="versionStatus.failureReason" label="失败原因">
            <span style="color: #dc2626">{{ versionStatus.failureReason }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <el-button
          v-if="editable && versionStatus?.status === 'FAILED'"
          type="primary"
          :icon="Refresh"
          :loading="retryBusy"
          style="margin-top: 18px; width: 100%"
          @click="retryVersion"
        >
          重试摄取任务
        </el-button>
      </template>
    </el-drawer>
  </div>
</template>
