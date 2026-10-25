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
    ├── types.ts          # 契约数据结构（含 origin / lastRound / 图片解析器类型）
    ├── state.ts          # localStorage 偏好、格式化辅助
    ├── desktop.ts        # window.picturePickDesktop 类型声明与安全降级
    ├── library.ts        # 浏览器本地照片图库（File 映射、object URL/缩略图、revoke）
    ├── export.ts         # 一键导出（File System Access API + 逐张下载降级）
    ├── viewer.ts         # 大图缩放（光标锚点）/拖拽/旋转/适应窗口/1:1
    ├── styles.css        # 深色 Lightroom 风格设计
    └── ui/
        ├── screens.ts    # 启动/致命错误整页屏
        ├── start.ts      # 启动页（浏览器原生文件夹选择）
        ├── picker.ts     # 选片视图（pick/统计/自动放映/结束本轮/下一轮）
        ├── filmstrip.ts  # 底部胶片条（IntersectionObserver 懒加载缩略图）
        ├── results.ts    # 结果页（筛选/搜索/下载 JSON/一键导出/再选一组）
        ├── modal.ts      # 模态框（Esc、焦点、async 动作）
        ├── toast.ts      # Toast
        └── dom.ts        # 转义、查询、内联 SVG 图标
```

## 功能要点（第 2 版）

- **打开照片文件夹**：使用浏览器原生文件夹选择框（隐藏的
  `<input type="file" webkitdirectory multiple>`），不再实现自定义目录浏览，也不调用
  `/api/fs/*`、`/api/dialog/*`。选中后前端过滤 `.jpg/.jpeg`、按相对路径排序，只把
  **照片元数据** 提交给后端（`POST /api/sessions`，`source="browser"`）；文件本身只留在
  浏览器内存，显示时用 `URL.createObjectURL`，离开/切换目录时统一 `revokeObjectURL`。
  胶片条/弹窗/结果列表使用 `createImageBitmap` 生成的本地缩略图，控制内存占用。
- **启动路由**：`GET /api/health` → `GET /api/sessions/current`；server 会话照常自动进入
  （图片走 `/image` 接口）；browser 会话在本页面内可继续，刷新后因本地 File 丢失会回到启动页
  并提示重新选择；finished 的 browser 会话允许进入结果页，导出时再引导重新选择原文件夹。
- **选片**：分页拉取整轮照片（`limit=500` 自动翻页），大图 + 胶片条；上一张/下一张、
  键盘 `←/→`、`P` pick/取消、`空格` 自动放映、`+/-/0/1/R/F` 缩放/适应/1:1/旋转/全屏；
  滚轮以光标为中心缩放、拖拽平移、双击切换；切换即 `POST .../viewed` 上报。
- **结束本轮**：弹窗展示本轮 pick 缩略图与数量；`pickedCount < 2` 时禁用下一轮并提示；
  `POST rounds/next` 进入下一轮，或 `POST finish` 生成结果。
- **结果页**：摘要卡片、轮次时间线、入选/全部切换、搜索、按 pickCount 排序；
  浏览器端 `result?download=1` 下载，桌面端 `preload.saveJson` 原生另存为；「再选一组照片」
  回到启动页。
- **一键导出保留照片**：按 `result.lastRound.pickedPhotoIds` 导出最后一轮保留的照片。
  优先 `window.showDirectoryPicker({mode:"readwrite"})` 写入用户指定文件夹（同名自动
  `-1/-2`），测试可通过 `window.__picturepickTestExportDirHandle` 注入 mock 目录句柄；
  不支持时降级逐张 `<a download>` 并在 UI 上提示。导出进度显示在 `export-progress`。
  browser 会话刷新后文件丢失时，导出前会引导用同一个隐藏 input 重新选择原文件夹，
  按 `relativePath` 重新匹配后再写入。
- **健壮性**：图片加载失败占位 + 重试、列表加载失败重试、网络错误整页提示、错误 toast、
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

关键交互元素带稳定的 `data-testid`：

- 启动页：`pick-folder-button`、`folder-input`
  （隐藏的 `<input type="file" webkitdirectory>`；Playwright 可对其
  `setInputFiles` 传**目录路径**，change 事件会直接触发导入；也是结果页重新匹配文件时复用的同一个 input）
- 选片：`viewer-image`（加载完成后 `data-loaded="true"`）、`prev-button`、`next-button`、
  `pick-button`、`autoplay-toggle`、`interval-input`、`end-round-button`、`stats-index`、
  `stats-viewed`、`stats-picked`、`stats-total`、`zoom-in`、`zoom-out`、`zoom-fit`、
  `zoom-actual`、`end-dialog`、`next-round-button`、`finish-button`
- 结果页：`result-view`、`export-photos`、`export-progress`、`result-download`、`restart-button`
- 通用：`toast`

> v1 的 `path-input`、`start-button`、`browse-button`、`dialog-button`、`folder-browser`、
> `folder-entry`、`folder-up`、`folder-confirm` 已随自定义目录浏览一起移除（契约 §8.1）。
