import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 后端 FastAPI 默认跑在 8000 端口，dev 环境通过 proxy 避免跨域
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
