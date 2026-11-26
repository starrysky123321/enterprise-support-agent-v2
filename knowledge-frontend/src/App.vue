<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { Monitor } from '@element-plus/icons-vue';
import { COOKIE_SESSION, createApi, readSession, writeSession } from './api.js';
import AuthView from './components/AuthView.vue';
import WorkbenchView from './components/WorkbenchView.vue';

const token = ref(readSession());
const sessionMessage = ref('');
const identity = ref(null);
const startupError = ref('');
const attempt = ref(0);

let activeController = null;

function expire(expiredToken) {
  if (readSession() !== expiredToken) return;
  writeSession('');
  identity.value = null;
  sessionMessage.value = '会话已过期，请重新登录。';
  token.value = '';
}

const api = computed(() => createApi(token.value, expire));

watch(
  [token, attempt],
  ([currentToken]) => {
    activeController?.abort();
    activeController = null;
    if (!currentToken) {
      identity.value = null;
      return;
    }
    const controller = new AbortController();
    activeController = controller;
    const client = api.value;
    const options = { signal: controller.signal };
    Promise.all([
      currentToken === COOKIE_SESSION || currentToken.startsWith('ks_')
        ? client('/users/me', options)
        : Promise.resolve(null),
      client('/workspaces', options),
    ])
      .then(([profile, workspaces]) => {
        if (!controller.signal.aborted) {
          identity.value = { profile, workspaces };
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted && error.status !== 401) {
          startupError.value = error.message;
        }
      });
  },
  { immediate: true },
);

onBeforeUnmount(() => activeController?.abort());

function signIn(nextToken) {
  writeSession(nextToken);
  startupError.value = '';
  sessionMessage.value = '';
  token.value = nextToken;
}

async function signOut() {
  if (token.value === COOKIE_SESSION || token.value.startsWith('ks_')) {
    await api.value('/auth/logout', { method: 'POST' });
  }
  writeSession('');
  identity.value = null;
  startupError.value = '';
  sessionMessage.value = '';
  token.value = '';
}

function retryStartup() {
  startupError.value = '';
  attempt.value += 1;
}

function backToLogin() {
  writeSession('');
  startupError.value = '';
  token.value = '';
}
</script>

<template>
  <AuthView v-if="!token" :session-message="sessionMessage" @sign-in="signIn" />

  <main v-else-if="!identity" class="sd-startup">
    <div class="sd-brand">
      <span class="sd-brand-mark"><el-icon :size="18"><Monitor /></el-icon></span>
      <span class="sd-brand-title">
        <strong>SupportDesk</strong>
        <small>企业技术支持与故障诊断工作台</small>
      </span>
    </div>

    <template v-if="startupError">
      <el-alert type="error" show-icon :closable="false" :title="startupError" style="max-width: 420px" />
      <div style="display: flex; gap: 10px">
        <el-button type="primary" @click="retryStartup">重新连接</el-button>
        <el-button @click="backToLogin">返回登录</el-button>
      </div>
    </template>
    <div v-else style="display: flex; flex-direction: column; align-items: center; gap: 12px; color: #64748b">
      <el-icon class="is-loading" :size="28" color="#2563eb"><Monitor /></el-icon>
      <span style="font-size: 13.5px">正在连接知识库工作台...</span>
    </div>
  </main>

  <WorkbenchView v-else :api="api" :identity="identity" @sign-out="signOut" />
</template>
