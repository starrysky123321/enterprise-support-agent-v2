<script setup>
import { reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { Key, Lock, Message, Monitor } from '@element-plus/icons-vue';
import { COOKIE_SESSION, anonymous, createApi } from '../api.js';

defineProps({
  sessionMessage: { type: String, default: '' },
});

const emit = defineEmits(['sign-in']);

const mode = ref('login');
const busy = ref(false);
const error = ref('');
const formRef = ref(null);
const form = reactive({ email: '', password: '', key: '' });

const modeOptions = [
  { label: '账号登录', value: 'login' },
  { label: '注册', value: 'register' },
  { label: '访问密钥', value: 'key' },
];

function switchMode(next) {
  error.value = '';
  form.password = '';
  form.key = '';
  mode.value = next;
  formRef.value?.clearValidate();
}

async function submit() {
  if (busy.value) return;
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;

  busy.value = true;
  error.value = '';
  try {
    if (mode.value === 'key') {
      const token = form.key.trim();
      await createApi(token)('/workspaces');
      emit('sign-in', token);
    } else {
      const payload = { email: form.email.trim(), password: form.password };
      if (mode.value === 'register') {
        await anonymous('/auth/register', { method: 'POST', data: payload });
        ElMessage.success('注册成功，请登录后由管理员分配工作区。');
        switchMode('login');
      } else {
        await anonymous('/auth/login', { method: 'POST', data: payload });
        emit('sign-in', COOKIE_SESSION);
      }
    }
  } catch (failure) {
    error.value = failure.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="sd-auth-minimal">
    <div class="sd-auth-box">
      <div class="sd-auth-header">
        <span class="sd-brand-mark"><el-icon :size="18"><Monitor /></el-icon></span>
        <h1>SupportDesk</h1>
      </div>

      <div class="sd-card sd-auth-card">
        <el-segmented
          :model-value="mode"
          :options="modeOptions"
          block
          style="margin-bottom: 22px"
          :disabled="busy"
          @update:model-value="switchMode"
        />

        <el-alert
          v-if="error || sessionMessage"
          :type="error ? 'error' : 'info'"
          :title="error || sessionMessage"
          show-icon
          :closable="false"
          style="margin-bottom: 18px"
        />

        <el-form ref="formRef" :model="form" label-position="top" @submit.prevent="submit">
          <template v-if="mode === 'key'">
            <el-form-item
              label="访问密钥"
              prop="key"
              :rules="[{ required: true, whitespace: true, message: '请输入访问密钥' }]"
            >
              <el-input
                v-model="form.key"
                type="password"
                size="large"
                show-password
                autocomplete="off"
                placeholder="输入管理员 Token 或工作区 API Key"
                :prefix-icon="Key"
              />
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item
              label="邮箱"
              prop="email"
              :rules="[
                { required: true, message: '请输入邮箱地址' },
                { type: 'email', message: '请输入有效的邮箱格式' },
              ]"
            >
              <el-input
                v-model="form.email"
                size="large"
                autocomplete="username"
                placeholder="name@company.com"
                :prefix-icon="Message"
              />
            </el-form-item>

            <el-form-item
              label="密码"
              prop="password"
              :rules="[
                { required: true, message: '请输入密码' },
                ...(mode === 'register' ? [{ min: 12, message: '密码至少需要 12 个字符' }] : []),
              ]"
            >
              <el-input
                v-model="form.password"
                type="password"
                size="large"
                show-password
                :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
                :placeholder="mode === 'register' ? '至少 12 个字符' : '输入密码'"
                :prefix-icon="Lock"
              />
            </el-form-item>
          </template>

          <el-button
            type="primary"
            size="large"
            native-type="submit"
            :loading="busy"
            style="width: 100%; margin-top: 6px; font-weight: 600"
          >
            {{ mode === 'register' ? '创建账号' : '进入工作台' }}
          </el-button>

          <div v-if="mode === 'login'" style="margin-top: 14px; text-align: center">
            <el-button
              link
              type="info"
              size="small"
              @click="ElMessage.info('请联系系统管理员在「账号与密钥」中重置密码')"
            >
              忘记密码？
            </el-button>
          </div>
        </el-form>
      </div>
    </div>
  </main>
</template>
