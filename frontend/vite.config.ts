import { defineConfig } from 'vite';

/**
 * PicturePick 前端构建配置。
 *
 * 契约（docs/ARCHITECTURE.md §3）：构建产物必须输出到
 * backend/picturepick/webui，由 FastAPI 以 SPA 方式提供静态资源。
 * 开发模式下 Vite 监听 5173，并把 /api 代理到 FastAPI 默认端口 8000。
 */
export default defineConfig({
  appType: 'spa',
  base: '/',
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  // 源码没有裸模块依赖（无 UI 框架），无需依赖预构建扫描。
  // 这也让沙箱内的 esbuild-wasm（无 readdir）可以正常跑 dev server。
  optimizeDeps: {
    noDiscovery: true,
    include: [],
  },
  build: {
    outDir: '../backend/picturepick/webui',
    emptyOutDir: true,
    target: 'es2020',
    sourcemap: false,
    cssCodeSplit: false,
    assetsDir: 'assets',
    reportCompressedSize: false,
  },
});
