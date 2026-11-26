import { onBeforeUnmount, reactive, watch } from 'vue';
import { documentPath, workspacePath } from '../api.js';

export const ongoing = (status) => status === 'PENDING' || status === 'INDEXING';

export function useDocuments(getApi, getWorkspace) {
  const state = reactive({
    rows: [],
    cursor: null,
    loading: true,
    error: '',
  });

  let activeRequest = null;
  let pollController = null;
  let pollTimer = null;

  async function load(after = null) {
    activeRequest?.abort();
    const controller = new AbortController();
    activeRequest = controller;
    state.loading = true;
    state.error = '';
    try {
      const api = getApi();
      const root = workspacePath(getWorkspace());
      const query = new URLSearchParams({ limit: '25' });
      if (after) query.set('after', after);
      const result = await api(`${root}/documents?${query}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      state.rows = after ? [...state.rows, ...result.items] : result.items;
      state.cursor = result.nextCursor;
    } catch (failure) {
      if (!controller.signal.aborted) state.error = failure.message;
    } finally {
      if (!controller.signal.aborted) state.loading = false;
    }
  }

  function stopPolling() {
    pollController?.abort();
    pollController = null;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  watch(
    () => [
      getWorkspace(),
      state.rows
        .filter((row) => ongoing(row.status))
        .map(({ documentId, latestVersion }) => `${documentId}:${latestVersion}`)
        .join(' '),
    ],
    ([workspace, pendingKey]) => {
      stopPolling();
      if (!workspace || !pendingKey) return;
      const controller = new AbortController();
      pollController = controller;
      const entries = pendingKey.split(' ').map((item) => item.split(':'));

      async function poll() {
        const api = getApi();
        const checks = await Promise.allSettled(
          entries.map(([id, version]) =>
            api(`${documentPath(workspace, id)}/versions/${version}`, { signal: controller.signal }),
          ),
        );
        if (controller.signal.aborted) return;
        const updates = new Map(
          checks
            .filter((item) => item.status === 'fulfilled')
            .map(({ value }) => [value.documentId, value]),
        );
        if (updates.size) {
          state.rows = state.rows.map((row) => {
            const next = updates.get(row.documentId);
            return next?.versionNumber === row.latestVersion
              ? { ...row, status: next.status, publishedVersion: next.publishedVersion }
              : row;
          });
        }
        pollTimer = setTimeout(poll, 1800);
      }

      pollTimer = setTimeout(poll, 1500);
    },
    { immediate: true },
  );

  watch(getWorkspace, (next) => {
    state.rows = [];
    state.cursor = null;
    if (next) load();
  }, { immediate: true });

  onBeforeUnmount(() => {
    activeRequest?.abort();
    stopPolling();
  });

  return {
    state,
    reload: () => load(),
    more: () => load(state.cursor),
  };
}
