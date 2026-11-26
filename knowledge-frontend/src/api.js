const ROOT = '/api/knowledge';
const SESSION_KEY = 'supportdesk.session';
export const COOKIE_SESSION = '__cookie__';

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

export function readSession() {
  return sessionStorage.getItem(SESSION_KEY) ?? '';
}

export function writeSession(token) {
  if (token) sessionStorage.setItem(SESSION_KEY, token);
  else sessionStorage.removeItem(SESSION_KEY);
}

function errorMessage(status, endpoint, payload, retryAfter) {
  if (status === 401) {
    return endpoint === '/auth/login' ? '账号或密码不正确。' : '凭据无效或会话已过期。';
  }
  if (status === 403) return '当前账号没有权限执行此操作。';
  if (status === 404) return '找不到该工作区、文档或版本。';
  if (status === 409) return endpoint === '/auth/register' ? '该邮箱已注册。' : '文档还没有可查询的发布版本。';
  if (status === 413) return '文件超过 25 MiB 的上传限制。';
  if (status === 415) return '请选择有效的 PDF 文件。';
  if (status === 429) return retryAfter ? `请求过于频繁，请在 ${retryAfter} 秒后重试。` : '请求过于频繁，请稍后重试。';
  if (status >= 500) return '服务暂时不可用，请稍后重试。';
  return payload?.detail || '请求未完成，请检查输入。';
}

export function createApi(token = '', onExpired) {
  const bearer = token && token !== COOKIE_SESSION ? token : '';

  async function request(endpoint, { method = 'GET', data, signal } = {}) {
    const form = data instanceof FormData;
    const headers = { Accept: 'application/json' };
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    if (data !== undefined && !form) headers['Content-Type'] = 'application/json';

    let response;
    try {
      response = await fetch(ROOT + endpoint, {
        method,
        headers,
        credentials: 'same-origin',
        body: data === undefined || form ? data : JSON.stringify(data),
        signal,
      });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new ApiError(0, '无法连接知识库，请检查服务连接后重试。');
    }
    if (response.status === 204) return null;
    const type = response.headers.get('content-type') ?? '';
    const payload = type.includes('json') ? await response.json() : null;
    if (!response.ok) {
      if (response.status === 401 && token) onExpired?.(token);
      throw new ApiError(response.status, errorMessage(
        response.status, endpoint, payload, response.headers.get('retry-after'),
      ));
    }
    return payload;
  }

  request.stream = async function stream(endpoint, { data, signal, onEvent } = {}) {
    const headers = {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    };
    if (bearer) headers.Authorization = `Bearer ${bearer}`;

    let response;
    try {
      response = await fetch(ROOT + endpoint, {
        method: 'POST',
        headers,
        credentials: 'same-origin',
        body: JSON.stringify(data ?? {}),
        signal,
      });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new ApiError(0, '无法连接知识库，请检查服务连接后重试。');
    }
    if (!response.ok) {
      const type = response.headers.get('content-type') ?? '';
      const payload = type.includes('json') ? await response.json().catch(() => null) : null;
      if (response.status === 401 && token) onExpired?.(token);
      throw new ApiError(response.status, errorMessage(
        response.status, endpoint, payload, response.headers.get('retry-after'),
      ));
    }
    if (!response.body) {
      throw new ApiError(502, '流式响应不可用。');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const emitBlock = (rawBlock) => {
      const block = rawBlock.trim();
      if (!block) return;
      let eventName = 'message';
      const dataLines = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) return;
      let parsed;
      try {
        parsed = JSON.parse(dataLines.join('\n'));
      } catch {
        parsed = { raw: dataLines.join('\n') };
      }
      onEvent?.(eventName, parsed);
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? '';
      for (const part of parts) emitBlock(part);
    }
    if (buffer.trim()) emitBlock(buffer);
  };

  return request;
}

export const anonymous = createApi();

export function workspacePath(id) {
  return `/workspaces/${encodeURIComponent(id)}`;
}

export function documentPath(workspace, document) {
  return `${workspacePath(workspace)}/documents/${encodeURIComponent(document)}`;
}
