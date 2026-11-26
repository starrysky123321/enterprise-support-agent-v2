<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { CopyDocument, Key, Plus, Refresh } from '@element-plus/icons-vue';
import { workspacePath } from '../api.js';

const props = defineProps({
  api: { type: Function, required: true },
  workspace: { type: String, required: true },
});

const activeTab = ref('accounts');

// Accounts management
const accounts = ref([]);
const accountsLoading = ref(false);
const accountsError = ref('');
const deptDialog = ref(false);
const deptBusy = ref(false);
const deptForm = reactive({ id: '', email: '', department: '' });

const resetDialog = ref(false);
const resetBusy = ref(false);
const resetForm = reactive({ id: '', email: '', newPassword: '' });

// Workspace API keys management
const keys = ref([]);
const keysLoading = ref(false);
const keysError = ref('');
const issueDialog = ref(false);
const issueBusy = ref(false);
const issueRole = ref('READER');
const issuedToken = ref('');

// System observability metrics
const metrics = ref(null);
const metricsLoading = ref(false);
const metricsError = ref('');

const cacheHitRate = computed(() => {
  if (!metrics.value?.cache) return 0;
  const total = (metrics.value.cache.hit || 0) + (metrics.value.cache.miss || 0);
  return total ? Math.round((metrics.value.cache.hit / total) * 100) : 0;
});

const heapPercent = computed(() => {
  if (!metrics.value?.jvm?.heapMaxMb) return 0;
  return Math.min(100, Math.round((metrics.value.jvm.heapUsedMb / metrics.value.jvm.heapMaxMb) * 100));
});

let accountsController = null;
let keysController = null;
let metricsController = null;

async function loadAccounts() {
  accountsController?.abort();
  const controller = new AbortController();
  accountsController = controller;
  accountsLoading.value = true;
  accountsError.value = '';
  try {
    const list = [];
    let next = null;
    do {
      const params = new URLSearchParams({ limit: '100' });
      if (next) params.set('after', next);
      const page = await props.api(`/users?${params}`, { signal: controller.signal });
      list.push(...page.items);
      next = page.nextCursor;
    } while (next && !controller.signal.aborted);
    if (!controller.signal.aborted) accounts.value = list;
  } catch (failure) {
    if (!controller.signal.aborted) accountsError.value = failure.message;
  } finally {
    if (!controller.signal.aborted) accountsLoading.value = false;
  }
}

async function loadKeys() {
  keysController?.abort();
  const controller = new AbortController();
  keysController = controller;
  keysLoading.value = true;
  keysError.value = '';
  try {
    const list = await props.api(`${workspacePath(props.workspace)}/keys`, {
      signal: controller.signal,
    });
    if (!controller.signal.aborted) keys.value = list;
  } catch (failure) {
    if (!controller.signal.aborted) keysError.value = failure.message;
  } finally {
    if (!controller.signal.aborted) keysLoading.value = false;
  }
}

async function loadMetrics() {
  metricsController?.abort();
  const controller = new AbortController();
  metricsController = controller;
  metricsLoading.value = true;
  metricsError.value = '';
  try {
    const data = await props.api('/admin/metrics', { signal: controller.signal });
    if (!controller.signal.aborted) metrics.value = data;
  } catch (failure) {
    if (!controller.signal.aborted) metricsError.value = failure.message;
  } finally {
    if (!controller.signal.aborted) metricsLoading.value = false;
  }
}

watch(
  () => props.workspace,
  () => {
    loadAccounts();
    loadKeys();
    loadMetrics();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  accountsController?.abort();
  keysController?.abort();
  metricsController?.abort();
});

async function toggleActive(row, nextActive) {
  try {
    const updated = await props.api(`/users/${row.id}/status`, {
      method: 'PUT',
      data: { active: nextActive },
    });
    row.active = updated.active;
    ElMessage.success(nextActive ? '账号已启用' : '账号已停用并撤销会话');
  } catch (failure) {
    ElMessage.error(failure.message);
  }
}

function openDepartment(row) {
  deptForm.id = row.id;
  deptForm.email = row.email;
  deptForm.department = row.department || '';
  deptDialog.value = true;
}

async function saveDepartment() {
  deptBusy.value = true;
  try {
    await props.api(`/users/${deptForm.id}/department`, {
      method: 'PUT',
      data: { department: deptForm.department.trim() || null },
    });
    deptDialog.value = false;
    await loadAccounts();
    ElMessage.success('部门信息已更新');
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    deptBusy.value = false;
  }
}

function openResetPassword(row) {
  resetForm.id = row.id;
  resetForm.email = row.email;
  resetForm.newPassword = '';
  resetDialog.value = true;
}

async function saveResetPassword() {
  if (resetForm.newPassword.length < 12) {
    ElMessage.warning('新密码至少需要 12 个字符');
    return;
  }
  resetBusy.value = true;
  try {
    await props.api(`/users/${resetForm.id}/password`, {
      method: 'PUT',
      data: { newPassword: resetForm.newPassword },
    });
    resetDialog.value = false;
    resetForm.newPassword = '';
    ElMessage.success('密码已重置，旧会话已自动失效');
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    resetBusy.value = false;
  }
}

async function submitIssueKey() {
  issueBusy.value = true;
  try {
    const created = await props.api(`${workspacePath(props.workspace)}/keys`, {
      method: 'POST',
      data: { role: issueRole.value },
    });
    issuedToken.value = created.token;
    await loadKeys();
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    issueBusy.value = false;
  }
}

async function copyToken() {
  if (!issuedToken.value) return;
  try {
    await navigator.clipboard.writeText(issuedToken.value);
    ElMessage.success('密钥已复制到剪贴板');
  } catch {
    ElMessage.warning('请手动选中并复制密钥');
  }
}

async function revokeKey(keyRow) {
  try {
    await ElMessageBox.confirm('撤销后使用该密钥的自动化调用将立即失效，确认继续？', '撤销 API Key', {
      type: 'warning',
      confirmButtonText: '确认撤销',
      cancelButtonText: '取消',
    });
  } catch {
    return;
  }
  try {
    await props.api(`${workspacePath(props.workspace)}/keys/${keyRow.id}`, { method: 'DELETE' });
    await loadKeys();
    ElMessage.success('API Key 已撤销');
  } catch (failure) {
    ElMessage.error(failure.message);
  }
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString('zh-CN') : '—';
}
</script>

<template>
  <section class="sd-card sd-panel">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="工程师账号管理" name="accounts">
        <div class="sd-panel-head" style="margin-top: 6px">
          <span style="font-size: 13px; color: #64748b">共 {{ accounts.length }} 个注册账号</span>
          <el-button :icon="Refresh" :loading="accountsLoading" @click="loadAccounts">刷新</el-button>
        </div>

        <el-alert
          v-if="accountsError"
          type="error"
          show-icon
          :closable="false"
          :title="accountsError"
          style="margin-bottom: 14px"
        />

        <el-table v-loading="accountsLoading" :data="accounts" stripe style="width: 100%">
          <el-table-column prop="email" label="邮箱账号" min-width="220" />
          <el-table-column label="部门" width="160">
            <template #default="{ row }">
              <span>{{ row.department || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <el-switch
                :model-value="row.active"
                active-text="启用"
                inactive-text="停用"
                inline-prompt
                @change="(val) => toggleActive(row, val)"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDepartment(row)">设置部门</el-button>
              <el-button link type="warning" @click="openResetPassword(row)">重置密码</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无已注册的普通账号" />
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`工作区 API Key (${workspace})`" name="keys">
        <div class="sd-panel-head" style="margin-top: 6px">
          <span style="font-size: 13px; color: #64748b">共 {{ keys.length }} 个有效密钥</span>
          <el-button
            type="primary"
            :icon="Plus"
            @click="issuedToken = ''; issueRole = 'READER'; issueDialog = true"
          >
            签发 API Key
          </el-button>
        </div>

        <el-alert
          v-if="keysError"
          type="error"
          show-icon
          :closable="false"
          :title="keysError"
          style="margin-bottom: 14px"
        />

        <el-table v-loading="keysLoading" :data="keys" stripe style="width: 100%">
          <el-table-column label="Key ID" min-width="260">
            <template #default="{ row }">
              <span class="sd-code">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="权限角色" width="120">
            <template #default="{ row }">
              <el-tag :type="row.role === 'EDITOR' ? 'primary' : 'info'" effect="light" round>
                {{ row.role === 'EDITOR' ? '可编辑' : '只读' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="签发时间" width="190">
            <template #default="{ row }">
              {{ formatDate(row.createdAt) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="right">
            <template #default="{ row }">
              <el-button link type="danger" @click="revokeKey(row)">撤销</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="当前工作区尚未签发 API Key" />
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="系统可观测性" name="metrics">
        <div class="sd-panel-head" style="margin-top: 6px">
          <span style="font-size: 13.5px; font-weight: 600; color: #334155">实时运行指标</span>
          <el-button :icon="Refresh" :loading="metricsLoading" @click="loadMetrics">刷新</el-button>
        </div>

        <el-alert
          v-if="metricsError"
          type="error"
          show-icon
          :closable="false"
          :title="metricsError"
          style="margin-bottom: 14px"
        />

        <el-skeleton v-else-if="metricsLoading && !metrics" :rows="4" animated />

        <div v-else-if="metrics" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px">
          <div class="sd-card" style="padding: 16px 18px">
            <div style="font-size: 12.5px; color: #64748b; margin-bottom: 8px">文档检索缓存命中率（集群合计）</div>
            <div style="font-size: 24px; font-weight: 700; margin-bottom: 10px">{{ cacheHitRate }}%</div>
            <el-progress :percentage="cacheHitRate" :stroke-width="8" :show-text="false" />
            <div style="font-size: 12px; color: #64748b; margin-top: 10px">
              命中 {{ metrics.cache.hit }} · 未命中 {{ metrics.cache.miss }} · 降级 {{ metrics.cache.unavailable }}
            </div>
          </div>

          <div class="sd-card" style="padding: 16px 18px">
            <div style="font-size: 12.5px; color: #64748b; margin-bottom: 8px">分布式限流决策</div>
            <div style="font-size: 24px; font-weight: 700; margin-bottom: 10px">
              {{ metrics.quota.searchThrottled + metrics.quota.askThrottled }}
              <span style="font-size: 13px; font-weight: 500; color: #64748b">次 429 拦截</span>
            </div>
            <div style="font-size: 12px; color: #64748b; line-height: 1.7">
              搜索限流 {{ metrics.quota.searchThrottled }} · 问答限流 {{ metrics.quota.askThrottled }}<br />
              搜索本地降级 {{ metrics.quota.searchFallback }} · 问答保护 {{ metrics.quota.askUnavailable }}
            </div>
          </div>

          <div class="sd-card" style="padding: 16px 18px">
            <div style="font-size: 12.5px; color: #64748b; margin-bottom: 8px">数据库连接池（HikariCP）</div>
            <div style="font-size: 24px; font-weight: 700; margin-bottom: 10px">
              {{ metrics.dbPool.active }}
              <span style="font-size: 13px; font-weight: 500; color: #64748b">活跃连接</span>
            </div>
            <div style="font-size: 12px; color: #64748b">
              空闲 {{ metrics.dbPool.idle }} · 等待队列 {{ metrics.dbPool.pending }}
            </div>
          </div>

          <div class="sd-card" style="padding: 16px 18px">
            <div style="font-size: 12.5px; color: #64748b; margin-bottom: 8px">JVM 堆内存使用</div>
            <div style="font-size: 24px; font-weight: 700; margin-bottom: 10px">
              {{ metrics.jvm.heapUsedMb }} <span style="font-size: 13px; font-weight: 500; color: #64748b">/ {{ metrics.jvm.heapMaxMb }} MiB</span>
            </div>
            <el-progress :percentage="heapPercent" :stroke-width="8" :show-text="false" :status="heapPercent > 85 ? 'warning' : ''" />
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="deptDialog" title="设置账号部门" width="420px" :close-on-click-modal="false">
      <p style="margin: 0 0 14px; font-weight: 600">{{ deptForm.email }}</p>
      <el-input v-model="deptForm.department" maxlength="255" placeholder="例如 platform-sre" />
      <template #footer>
        <el-button @click="deptDialog = false">取消</el-button>
        <el-button type="primary" :loading="deptBusy" @click="saveDepartment">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetDialog" title="重置账号密码" width="420px" :close-on-click-modal="false">
      <p style="margin: 0 0 14px; font-weight: 600">{{ resetForm.email }}</p>
      <el-input
        v-model="resetForm.newPassword"
        type="password"
        show-password
        maxlength="256"
        placeholder="输入新密码（至少 12 位）"
      />
      <template #footer>
        <el-button @click="resetDialog = false">取消</el-button>
        <el-button type="primary" :loading="resetBusy" @click="saveResetPassword">确认重置</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="issueDialog"
      title="签发工作区 API Key"
      width="480px"
      :close-on-click-modal="false"
    >
      <template v-if="issuedToken">
        <el-alert
          type="warning"
          show-icon
          :closable="false"
          title="请立即保存此密钥，关闭弹窗后将无法再次查看明文。"
          style="margin-bottom: 14px"
        />
        <el-input :model-value="issuedToken" readonly>
          <template #append>
            <el-button :icon="CopyDocument" @click="copyToken">复制</el-button>
          </template>
        </el-input>
      </template>

      <template v-else>
        <el-form label-position="top">
          <el-form-item label="密钥角色">
            <el-select v-model="issueRole" style="width: 100%">
              <el-option label="只读（可列出文档、检索、问答）" value="READER" />
              <el-option label="可编辑（可上传版本、重试、删除文档）" value="EDITOR" />
            </el-select>
          </el-form-item>
        </el-form>
      </template>

      <template #footer>
        <el-button @click="issueDialog = false">{{ issuedToken ? '完成' : '取消' }}</el-button>
        <el-button v-if="!issuedToken" type="primary" :icon="Key" :loading="issueBusy" @click="submitIssueKey">
          立即签发
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>
