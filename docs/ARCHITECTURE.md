# PicturePick 架构与工程约定（第 2 版）

## 1. 目标交付物

| 交付形态 | 说明 |
| --- | --- |
| 命令行 + 浏览器 | `python -m picturepick serve --dir <目录>` 启动本地服务并自动打开浏览器 |
| 前后端分离开发 | Vite 开发服务器（5173）→ `/api` 代理到 FastAPI（8000） |
| 桌面应用 | **PyInstaller + pywebview（Edge WebView2）** 原生窗口，进程内启动同一份 FastAPI 后端；PyInstaller 产出 Windows 绿色版/zip |
| 可选历史方案 | Electron + electron-builder（代码保留在 `desktop/`，本机 Electron 无法启动时不要使用） |

## 2. 技术选型（均在广泛使用、社区活跃的项目中选取）

- 后端：Python 3.10 + **FastAPI** + **uvicorn** + **Pillow**（缩略图）+ **pydantic**（1.10/2.x 兼容写法）
- 前端：**TypeScript** + **Vite**（原生 DOM，无框架，构建产物为静态 HTML/JS/CSS）
- 桌面：**pywebview 6.x（Windows 走 Edge WebView2，依赖 pythonnet）** + **PyInstaller 6.x**
- 测试：**pytest** + `fastapi.testclient`；前端 `tsc --noEmit` + `vite build`；浏览器流程用 Playwright（Chromium）

## 3. 目录结构

```
E:\picturePick\
├── docs/                     # 契约（API.md，第 2 版）、架构、验证记录、截图
├── backend/                  # Python 后端
│   ├── picturepick/          # app / api / models / scanner / session / export / imaging / fsbrowse / cli
│   ├── tests/                # pytest（117 个用例）
│   └── pyproject.toml / requirements.txt
├── frontend/                 # TypeScript + Vite 前端
│   ├── src/
│   │   ├── main.ts           # 视图路由 + 隐藏文件夹 input 复用
│   │   ├── library.ts        # PhotoLibrary：File↔photoId、object URL、本地缩略图
│   │   ├── export.ts         # 一键导出（showDirectoryPicker / mock / 降级下载）
│   │   ├── api.ts / types.ts / state.ts / viewer.ts / desktop.ts
│   │   └── ui/               # start（原生选目录）/ picker / filmstrip / results / modal / toast / screens
│   └── scripts/              # 受限环境下的 Vite(esbuild-wasm) 启动器
├── desktop_webview/          # 桌面版（主方案）
│   ├── picturepick_desktop/  # desktop.py（后端线程 + WebView2 窗口 + 原生桥接 + --selftest）
│   ├── picturepick-desktop.spec
│   └── requirements.txt      # pywebview + pythonnet
├── desktop/                  # 可选历史方案：Electron 外壳 + sidecar + electron-builder
├── scripts/                  # build.ps1 / verify-desktop.ps1 / dev.ps1 / e2e_api.py / ui_e2e.cjs / 样例照片
├── build.cmd / verify-desktop.cmd
├── build/                    # 打包中间产物（webview/、backend/）
└── release/                  # 桌面版产物
```

## 4. 运行时数据流（第 3 版）

> 核心原则：**浏览器只负责“选目录/选目标文件夹”，拿到绝对路径后交给本机后端；
> 浏览器绝不读取、遍历或上传照片文件，所有照片由后端扫描并通过 `/image` 按需提供。**

### 4.1 打开目录与选片（server 来源，默认）

1. 启动页只有一个「选择照片文件夹」按钮；点击后按顺序取绝对目录路径：
   ① 桌面宿主原生文件夹对话框（WebView2/Electron 桥接）；
   ② 浏览器模式：`POST /api/dialog/pick-directory`，由本机 Python 后端弹出系统对话框；
   ③ 自动化测试：`window.__picturepickTestSourceDir` 注入。
2. 拿到路径后 `POST /api/sessions`（`source="server"`，`directory=路径`）；后端扫描目录、
   建立会话与轮次，返回照片列表。
3. 图片统一通过 `GET /api/sessions/{sid}/photos/{pid}/image?variant=original|thumb` 按需加载；
   浏览器不创建 object URL、不持有 File 对象。
4. 浏览上报 `viewed`、pick 调 `PUT .../pick`、结束本轮弹窗 → `rounds/next` 或 `finish`。
5. 结果页：JSON 下载/另存；「导出保留照片」选择目标文件夹路径（获取顺序同上）后调用
   `POST /api/sessions/{sid}/export-photos`，由**后端把 lastRound 保留照片复制**到目标目录
   （同名自动 -1/-2，不覆盖）。

### 4.2 兼容说明

- CLI `--dir` / 桌面端 `--dir` 仍是 server 来源预扫描，行为一致。
- `origin="browser"`（第 2 版的元数据导入）接口保留用于兼容/测试，但当前 UI 不再使用。

### 4.3 桌面窗口（WebView2）

`PicturePick.exe` → 进程内后台线程启动 FastAPI（随机空闲端口）→ 轮询 `/api/health` →
pywebview 创建 WebView2 窗口加载 `http://127.0.0.1:<port>/?desktop=webview2` →
preload 桥接提供原生「另存为 JSON / 打开路径」→ 窗口关闭后停止后端线程。
`--selftest` 可在不开窗口的情况下检查内置前端、pythonnet、WebView2 桥接与后端是否可用。

## 5. 关键约定

- 接口以 `docs/API.md` 为唯一准绳；第 3 版新增 `POST /api/sessions/{sid}/export-photos`，
  前端改为只获取目录路径、由后端扫描与供图。
- 照片 ID：server 来源 `sha1(绝对路径)[:16]`；browser 来源（兼容接口）`sha1(relativePath)[:16]`。
- 浏览器端不得读取照片文件（禁止 `webkitdirectory` / File System Access 导入）；图片一律经 `/image`。
- 导出照片由后端复制；自动化测试通过 `window.__picturepickTestSourceDir` /
  `window.__picturepickTestExportDir` 注入路径。
- 构建：`scripts/build.ps1`（前端 → PyInstaller → release）；PowerShell 脚本统一保存为 **UTF-8 with BOM**
  （Windows PowerShell 5.1 兼容）。
- 受限环境的 npm/二进制缓存约定见 README / 各子目录 README；普通桌面环境无需关心。
