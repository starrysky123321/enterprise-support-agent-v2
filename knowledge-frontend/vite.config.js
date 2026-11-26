import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/element-plus/')) return 'element-plus';
          if (id.includes('/node_modules/@element-plus/')) return 'element-icons';
          if (id.includes('/node_modules/vue/')) return 'vue';
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      '/api/knowledge': process.env.KNOWLEDGE_UI_DEV_API ?? 'http://127.0.0.1:8081',
    },
  },
});
