<script setup>
import { computed, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  ArrowDown,
  Document,
  FolderOpened,
  Key,
  Menu as MenuIcon,
  Monitor,
  Plus,
  Search,
  SwitchButton,
  Upload,
  User,
  UserFilled,
} from '@element-plus/icons-vue';
import { workspacePath } from '../api.js';
import { useDocuments } from '../composables/useDocuments.js';
import DiagnosisView from './DiagnosisView.vue';
import DocumentsView from './DocumentsView.vue';
import MembersView from './MembersView.vue';
import AdminView from './AdminView.vue';

const props = defineProps({
  api: { type: Function, required: true },
  identity: { type: Object, required: true },
});

const emit = defineEmits(['sign-out']);

const ROLES = { ADMIN: '管理员', EDITOR: '可编辑', READER: '只读' };
const TITLES = { diagnosis: '故障诊断', documents: '文档与版本', members: '工作区成员', admin: '账号与密钥' };
const LAST_WORKSPACE_KEY = 'supportdesk.workspace';

function initialWorkspace(list) {
  const saved = sessionStorage.getItem(LAST_WORKSPACE_KEY);
  return (
    list.find(({ workspaceId }) => workspaceId === saved)?.workspaceId ??
    list.find(({ workspaceId }) => workspaceId === 'default')?.workspaceId ??
    list[0]?.workspaceId ??
    ''
  );
}

const workspaces = ref([...props.identity.workspaces]);
const workspaceId = ref(initialWorkspace(props.identity.workspaces));
const page = ref('diagnosis');
const navOpen = ref(false);

const createDialog = ref(false);
const createBusy = ref(false);
const createError = ref('');
const createFormRef = ref(null);
const createForm = reactive({ id: '' });

const passwordDialog = ref(false);
const passwordBusy = ref(false);
const passwordError = ref('');
const passwordFormRef = ref(null);
const passwordForm = reactive({ currentPassword: '', newPassword: '' });

const uploadRequest = ref(null);
const newVersion = ref(null);

const workspace = computed(() => workspaces.value.find((item) => item.workspaceId === workspaceId.value) ?? null);
const administrator = computed(
  () => Boolean(props.identity.profile?.admin) || workspaces.value.some((item) => item.role === 'ADMIN'),
);
const editable = computed(() => workspace.value && workspace.value.role !== 'READER');
const userLabel = computed(
  () => props.identity.profile?.email ?? (administrator.value ? '系统管理员' : '工作区密钥'),
);

const documents = useDocuments(
  () => props.api,
  () => workspaceId.value,
);

const publishedCount = computed(
  () => documents.state.rows.filter((doc) => doc.publishedVersion != null).length,
);

function chooseWorkspace(id) {
  sessionStorage.setItem(LAST_WORKSPACE_KEY, id);
  workspaceId.value = id;
}

function openUpload(id = '') {
  uploadRequest.value = { id, seq: Date.now() };
  page.value = 'documents';
}

function handleUploaded(version) {
  uploadRequest.value = null;
  newVersion.value = version;
  documents.reload();
  page.value = 'documents';
}

async function submitCreateWorkspace() {
  const valid = await createFormRef.value?.validate().catch(() => false);
  if (!valid) return;
  createBusy.value = true;
  createError.value = '';
  try {
    const id = createForm.id.trim();
    await props.api(workspacePath(id), { method: 'PUT' });
    workspaces.value = await props.api('/workspaces');
    chooseWorkspace(id);
    createDialog.value = false;
    createForm.id = '';
    ElMessage.success('工作区已创建');
  } catch (failure) {
    createError.value = failure.message;
  } finally {
    createBusy.value = false;
  }
}

async function submitPasswordChange() {
  const valid = await passwordFormRef.value?.validate().catch(() => false);
  if (!valid) return;
  passwordBusy.value = true;
  passwordError.value = '';
  try {
    await props.api('/users/me/password', {
      method: 'PUT',
      data: {
        currentPassword: passwordForm.currentPassword,
        newPassword: passwordForm.newPassword,
      },
    });
    passwordDialog.value = false;
    passwordForm.currentPassword = '';
    passwordForm.newPassword = '';
    ElMessage.success('密码已更新，请使用新密码重新登录。');
    emit('sign-out');
  } catch (failure) {
    passwordError.value = failure.message;
  } finally {
    passwordBusy.value = false;
  }
}

function handleAccountCommand(command) {
  if (command === 'password') {
    passwordError.value = '';
    passwordDialog.value = true;
  } else if (command === 'logout') {
    emit('sign-out');
  }
}
</script>

<template>
  <div class="sd-shell">
    <div
      v-if="navOpen"
      style="position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); z-index: 35"
      @click="navOpen = false"
    />

    <aside class="sd-sidebar" :class="{ 'is-open': navOpen }">
      <div class="sd-sidebar-head">
        <div class="sd-brand">
          <span class="sd-brand-mark"><el-icon :size="17"><Monitor /></el-icon></span>
          <span class="sd-brand-title">
            <strong>SupportDesk</strong>
          </span>
        </div>
      </div>

      <nav class="sd-nav" aria-label="主导航">
        <button
          type="button"
          class="sd-nav-btn"
          :class="{ 'is-active': page === 'diagnosis' }"
          @click="page = 'diagnosis'; navOpen = false"
        >
          <el-icon><Search /></el-icon>
          <span>故障诊断</span>
        </button>

        <button
          type="button"
          class="sd-nav-btn"
          :class="{ 'is-active': page === 'documents' }"
          @click="page = 'documents'; navOpen = false"
        >
          <el-icon><Document /></el-icon>
          <span>文档与版本</span>
          <span v-if="workspace && publishedCount" class="sd-nav-badge">{{ publishedCount }}</span>
        </button>

        <button
          v-if="administrator"
          type="button"
          class="sd-nav-btn"
          :class="{ 'is-active': page === 'members' }"
          @click="page = 'members'; navOpen = false"
        >
          <el-icon><UserFilled /></el-icon>
          <span>工作区成员</span>
        </button>

        <button
          v-if="administrator"
          type="button"
          class="sd-nav-btn"
          :class="{ 'is-active': page === 'admin' }"
          @click="page = 'admin'; navOpen = false"
        >
          <el-icon><Key /></el-icon>
          <span>账号与密钥</span>
        </button>
      </nav>

      <el-dropdown trigger="click" placement="top-start" @command="handleAccountCommand">
        <button type="button" class="sd-account-btn">
          <el-avatar :size="32" style="background: #2563eb; flex-shrink: 0">
            <el-icon><User /></el-icon>
          </el-avatar>
          <span class="sd-account-meta">
            <strong>{{ userLabel }}</strong>
            <small>{{ identity.profile ? '账号会话' : '访问密钥会话' }}</small>
          </span>
          <el-icon style="color: #94a3b8"><ArrowDown /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="identity.profile" command="password" :icon="Key">
              修改密码
            </el-dropdown-item>
            <el-dropdown-item
              command="logout"
              :icon="SwitchButton"
              :divided="Boolean(identity.profile)"
              style="color: #dc2626"
            >
              退出登录
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </aside>

    <div class="sd-main">
      <header class="sd-topbar">
        <div class="sd-topbar-left">
          <el-button class="sd-mobile-trigger" text :icon="MenuIcon" @click="navOpen = true" />
          <strong style="font-size: 15px; color: #0f172a">{{ TITLES[page] }}</strong>
        </div>

        <div class="sd-topbar-right">
          <el-icon style="color: #64748b"><FolderOpened /></el-icon>
          <el-select
            :model-value="workspaceId"
            placeholder="选择工作区"
            style="width: 185px"
            @update:model-value="chooseWorkspace"
          >
            <el-option
              v-for="item in workspaces"
              :key="item.workspaceId"
              :label="item.workspaceId"
              :value="item.workspaceId"
            />
          </el-select>

          <el-tag
            v-if="workspace"
            :type="workspace.role === 'READER' ? 'info' : 'primary'"
            effect="light"
            round
          >
            {{ ROLES[workspace.role] }}
          </el-tag>

          <el-tooltip v-if="administrator" content="创建新工作区" placement="bottom">
            <el-button circle :icon="Plus" @click="createError = ''; createDialog = true" />
          </el-tooltip>
        </div>
      </header>

      <main v-if="workspace" class="sd-content">
        <div class="sd-page-header">
          <h1>{{ TITLES[page] }}</h1>
          <el-button
            v-if="(page === 'diagnosis' || page === 'documents') && editable"
            type="primary"
            :icon="Upload"
            @click="openUpload()"
          >
            上传文档
          </el-button>
        </div>

        <DiagnosisView
          v-if="page === 'diagnosis'"
          :api="api"
          :workspace="workspace.workspaceId"
          :documents="documents"
        />

        <DocumentsView
          v-else-if="page === 'documents'"
          :api="api"
          :workspace="workspace.workspaceId"
          :documents="documents"
          :editable="editable"
          :upload-request="uploadRequest"
          :new-version="newVersion"
          @open-upload="openUpload"
          @close-upload="uploadRequest = null"
          @uploaded="handleUploaded"
          @version-opened="newVersion = null"
        />

        <MembersView
          v-else-if="page === 'members'"
          :api="api"
          :workspace="workspace.workspaceId"
        />

        <AdminView
          v-else-if="page === 'admin'"
          :api="api"
          :workspace="workspace.workspaceId"
        />
      </main>

      <main v-else class="sd-content">
        <div class="sd-card" style="padding: 56px 24px; text-align: center">
          <el-empty description="当前账号尚未分配工作区">
            <el-button v-if="administrator" type="primary" :icon="Plus" @click="createDialog = true">
              创建第一个工作区
            </el-button>
          </el-empty>
        </div>
      </main>
    </div>

    <el-dialog v-model="createDialog" title="创建工作区" width="440px" :close-on-click-modal="false">
      <el-alert
        v-if="createError"
        type="error"
        :title="createError"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
      />
      <el-form ref="createFormRef" :model="createForm" label-position="top" @submit.prevent="submitCreateWorkspace">
        <el-form-item
          label="工作区 ID"
          prop="id"
          :rules="[
            { required: true, message: '请输入工作区 ID' },
            { pattern: /^[a-z0-9][a-z0-9-]{0,63}$/, message: '仅支持小写字母、数字和连字符，最长 64 字符' },
          ]"
        >
          <el-input v-model="createForm.id" placeholder="例如 support-ops" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="createBusy" @click="submitCreateWorkspace">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="passwordDialog" title="修改账号密码" width="440px" :close-on-click-modal="false">
      <el-alert
        v-if="passwordError"
        type="error"
        :title="passwordError"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
      />
      <el-form ref="passwordFormRef" :model="passwordForm" label-position="top" @submit.prevent="submitPasswordChange">
        <el-form-item
          label="当前密码"
          prop="currentPassword"
          :rules="[{ required: true, message: '请输入当前密码' }]"
        >
          <el-input v-model="passwordForm.currentPassword" type="password" show-password />
        </el-form-item>
        <el-form-item
          label="新密码（至少 12 位）"
          prop="newPassword"
          :rules="[
            { required: true, message: '请输入新密码' },
            { min: 12, message: '新密码至少需要 12 个字符' },
          ]"
        >
          <el-input v-model="passwordForm.newPassword" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialog = false">取消</el-button>
        <el-button type="primary" :loading="passwordBusy" @click="submitPasswordChange">确认修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>
