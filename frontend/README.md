# PicturePick Frontend

TypeScript + Vite + 原生 DOM（无 UI 框架）实现的选片 Web UI。
接口契约见仓库根目录 `docs/API.md`，工程约定见 `docs/ARCHITECTURE.md`。

## 命令

```powershell
cd E:\picturePick\frontend

# 安装（沙箱内必须使用工作区缓存，避免 EPERM）
$env:npm_config_cache='E:\picturePick\.cache\npm-frontend'
$env:npm_config_logs_dir='E:\picturePick\.cache\npm-frontend\_logs'
$env:npm_config_foreground_scripts='true'
npm install --foreground-scripts

npm run typecheck   # tsc --noEmit
npm run build       # tsc --noEmit + vite build → ../backend/picturepick/webui
npm run dev         # Vite dev server: http://127.0.0.1:5173（/api 代理到 127.0.0.1:8000）
npm run preview     # 预览构建产物
```

构建产物固定输出到 `backend/picturepick/webui`（`vite.config.ts` → `build.outDir`），
由 FastAPI 以 SPA 方式托管，不要再手动修改该目录。

## 目录结构

```
frontend/
├── index.html            # 单页入口（内联 SVG favicon）
├── vite.config.ts        # outDir、dev 端口 5173、/api 代理、optimizeDeps
├── scripts/
│   ├── vite-wasm.mjs     # 沙箱兼容的 Vite 启动器（build/dev/preview）
│   ├── esbuild-shim.mjs  # 把 Vite 的 esbuild 调用接到 esbuild-wasm
│   ├── esbuild-loader.mjs
│   └── child-process-shim.mjs
└── src/
    ├── main.ts           # 启动流程与视图路由（健康检查 → current session → 视图）
    ├── api.ts            # docs/API.md 的完整封装 + ApiError（中文文案映射）
    ├── types.ts          # 契约数据结构
    ├── state.ts          # localStorage 偏好/最近目录、格式化辅助
    ├── desktop.ts        # window.picturePickDesktop 类型声明与安全降级
    ├── viewer.ts         # 大图缩放（光标锚点）/拖拽/旋转/适应窗口/1:1
    ├── styles.css        # 深色 Lightroom 风格设计
    └── ui/
        ├── screens.ts    # 启动/致命错误整页屏
        ├── start.ts      # 启动页 + 目录浏览弹窗 + 系统目录选择
        ├── picker.ts     # 选片视图（pick/统计/自动放映/结束本轮/下一轮）
        ├── filmstrip.ts  # 底部胶片条（IntersectionObserver 懒加载缩略图）
        ├── results.ts    # 结果页（筛选/搜索/下载 JSON/再选一组）
        ├── modal.ts      # 模态框（Esc、焦点、async 动作）
        ├── toast.ts      # Toast
        └── dom.ts        # 转义、查询、内联 SVG 图标
```

## 功能要点

- **启动**：`GET /api/health` → `GET /api/sessions/current`；有 picking 会话直接进入选片，
  finished 进入结果页，否则进入目录选择。目录支持手输、回车开始、目录浏览弹窗（盘符/上级/
  面包屑）、系统目录对话框（桌面端走 preload，浏览器端走 `/api/dialog/pick-directory`，
  501 时自动回退目录浏览）、最近目录（localStorage）。
- **选片**：分页拉取整轮照片（`limit=500` 自动翻页），大图 + 胶片条；上一张/下一张、
  键盘 `←/→`、`P` pick/取消、`空格` 自动放映、`+/-/0/1/R/F` 缩放/适应/1:1/旋转/全屏；
  滚轮以光标为中心缩放、拖拽平移、双击切换；切换即 `POST .../viewed` 上报。
- **结束本轮**：弹窗展示本轮 pick 缩略图与数量；`pickedCount < 2` 时禁用下一轮并提示；
  `POST rounds/next` 进入下一轮，或 `POST finish` 生成结果。
- **结果页**：摘要卡片、轮次时间线、入选/全部切换、搜索、缩略图懒加载、按 pickCount 排序；
  浏览器端 `result?download=1` 下载，桌面端 `preload.saveJson` 原生另存为，文件名与后端一致；
  「再选一组照片」回到启动页。
- **健壮性**：图片 404 占位 + 重试、列表加载失败重试、网络错误整页提示、错误 toast、
  未结束会话 `beforeunload` 提示、重复请求幂等保护。

## 沙箱兼容构建（重要）

本仓库的开发/构建沙箱禁止 Node 通过管道 stdio 启动子进程（`spawn EPERM`），而原生
`esbuild` 的 JS API 依赖一个常驻子进程服务，因此直接 `vite build` 会失败。

处理方式：`package.json` 将 `esbuild` 依赖别名到 `esbuild-wasm`（WebAssembly，进程内执行），
`scripts/vite-wasm.mjs` 在导入 Vite 之前：

1. 注册 Node loader 把 `esbuild` 重定向到 `scripts/esbuild-shim.mjs`；
2. 初始化 wasm（`worker: false`）并补齐浏览器构建需要的 `self` 全局；
3. 用 wasm 的 `transform` 把 `vite.config.ts` 编译为 ESM 临时文件，再以
   `configFile: false` 内联给 Vite（wasm 版 fs 不支持 readdir，故不走配置打包）；
4. 对 Vite 的 `net use` 网络驱动器探测做了垫片（`child-process-shim.mjs`）。

`npm run build` 与 `npm run dev` 均已在该沙箱内验证可用（wasm 首次编译约 8 秒）。
在普通桌面环境下同样适用，只是构建比原生 esbuild 稍慢。

## 测试 ID（供 Playwright e2e）

关键交互元素带稳定的 `data-testid`：`path-input`、`start-button`、`browse-button`、
`dialog-button`、`folder-browser`、`folder-entry`（含 `data-path`）、`folder-up`、
`folder-confirm`、`viewer-image`（加载完成后 `data-loaded="true"`）、`prev-button`、
`next-button`、`pick-button`、`autoplay-toggle`、`interval-input`、`end-round-button`、
`stats-index`、`stats-viewed`、`stats-picked`、`stats-total`、`zoom-in`、`zoom-out`、
`zoom-fit`、`zoom-actual`、`end-dialog`、`next-round-button`、`finish-button`、
`result-view`、`result-download`、`restart-button`、`toast`。
