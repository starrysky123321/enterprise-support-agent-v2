<script setup>
import { onBeforeUnmount, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus } from '@element-plus/icons-vue';
import { workspacePath } from '../api.js';

const props = defineProps({
  api: { type: Function, required: true },
  workspace: { type: String, required: true },
});

const ROLES = [
  { value: 'READER', label: '只读' },
  { value: 'EDITOR', label: '可编辑' },
];

const rows = ref([]);
const loading = ref(true);
const error = ref('');

const dialogOpen = ref(false);
const editingMember = ref(null);
const accounts = ref([]);
const accountsLoading = ref(false);
const saveBusy = ref(false);
const formError = ref('');
const formRef = ref(null);
const form = reactive({ accountId: '', role: 'READER' });

let listController = null;
let accountsController = null;

async function refresh() {
  listController?.abort();
  const controller = new AbortController();
  listController = controller;
  loading.value = true;
  error.value = '';
  try {
    const members = await props.api(`${workspacePath(props.workspace)}/members`, {
      signal: controller.signal,
    });
    if (!controller.signal.aborted) rows.value = members;
  } catch (failure) {
    if (!controller.signal.aborted) error.value = failure.message;
  } finally {
    if (!controller.signal.aborted) loading.value = false;
  }
}

watch(() => props.workspace, () => refresh(), { immediate: true });

onBeforeUnmount(() => {
  listController?.abort();
  accountsController?.abort();
});

async function loadAccounts() {
  accountsController?.abort();
  const controller = new AbortController();
  accountsController = controller;
  accountsLoading.value = true;
  try {
    const list = [];
    let next = null;
    do {
      const params = new URLSearchParams({ limit: '100' });
      if (next) params.set('after', next);
      const page = await props.api(`/users?${params}`, { signal: controller.signal });
      list.push(...page.items.filter((item) => item.active));
      next = page.nextCursor;
    } while (next && !controller.signal.aborted);
    if (!controller.signal.aborted) accounts.value = list;
  } catch (failure) {
    if (!controller.signal.aborted) formError.value = failure.message;
  } finally {
    if (!controller.signal.aborted) accountsLoading.value = false;
  }
}

function openDialog(member = null) {
  editingMember.value = member;
  formError.value = '';
  form.accountId = member?.accountId || '';
  form.role = member?.role || 'READER';
  dialogOpen.value = true;
  formRef.value?.clearValidate();
  if (!member) loadAccounts();
}

async function submitMember() {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;
  saveBusy.value = true;
  formError.value = '';
  try {
    const targetId = editingMember.value?.accountId ?? form.accountId;
    await props.api(`${workspacePath(props.workspace)}/members/${targetId}`, {
      method: 'PUT',
      data: { role: form.role },
    });
    dialogOpen.value = false;
    await refresh();
    ElMessage.success('成员权限已更新');
  } catch (failure) {
    formError.value = failure.message;
  } finally {
    saveBusy.value = false;
  }
}

async function removeMember(member) {
  try {
    await ElMessageBox.confirm(`确认撤销 ${member.email} 对该工作区的访问权限？`, '移除成员', {
      type: 'warning',
      confirmButtonText: '确认移除',
      cancelButtonText: '取消',
    });
  } catch {
    return;
  }
  try {
    await props.api(`${workspacePath(props.workspace)}/members/${member.accountId}`, {
      method: 'DELETE',
    });
    await refresh();
    ElMessage.success('已移除工作区成员');
  } catch (failure) {
    ElMessage.error(failure.message);
  }
}
</script>

<template>
  <section class="sd-card sd-panel">
    <div class="sd-panel-head">
      <div>
        <h2 class="sd-panel-title">成员列表</h2>
        <span style="font-size: 12.5px; color: #64748b">共 {{ rows.length }} 名已分配成员</span>
      </div>
      <el-button type="primary" :icon="Plus" @click="openDialog()">添加成员</el-button>
    </div>

    <el-alert
      v-if="error"
      type="error"
      show-icon
      :closable="false"
      :title="error"
      style="margin-bottom: 14px"
    />

    <el-table v-loading="loading" :data="rows" stripe style="width: 100%">
      <el-table-column prop="email" label="邮箱账号" min-width="240" />
      <el-table-column label="角色权限" width="140">
        <template #default="{ row }">
          <el-tag :type="row.role === 'EDITOR' ? 'primary' : 'info'" effect="light" round>
            {{ ROLES.find((item) => item.value === row.role)?.label || row.role }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" align="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDialog(row)">修改角色</el-button>
          <el-button link type="danger" @click="removeMember(row)">移除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="此工作区暂无成员" />
      </template>
    </el-table>

    <el-dialog
      v-model="dialogOpen"
      :title="editingMember ? '修改成员角色' : '添加工作区成员'"
      width="440px"
      :close-on-click-modal="false"
    >
      <el-alert
        v-if="formError"
        type="error"
        show-icon
        :closable="false"
        :title="formError"
        style="margin-bottom: 14px"
      />

      <el-form ref="formRef" :model="form" label-position="top">
        <p v-if="editingMember" style="margin: 0 0 16px; font-weight: 600">
          {{ editingMember.email }}
        </p>

        <el-form-item
          v-else
          label="选择账号"
          prop="accountId"
          :rules="[{ required: true, message: '请选择要添加的账号' }]"
        >
          <el-select
            v-model="form.accountId"
            filterable
            :loading="accountsLoading"
            placeholder="按邮箱搜索已启用账号"
            style="width: 100%"
          >
            <el-option
              v-for="acc in accounts"
              :key="acc.id"
              :label="acc.email"
              :value="acc.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="工作区角色" prop="role" :rules="[{ required: true }]">
          <el-select v-model="form.role" style="width: 100%">
            <el-option v-for="item in ROLES" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saveBusy" :disabled="accountsLoading" @click="submitMember">
          保存权限
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>
