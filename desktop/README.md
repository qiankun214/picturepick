# PicturePick 桌面端（Electron + PyInstaller sidecar）

Electron 外壳：选择空闲端口拉起 Python 后端 sidecar（开发模式 `python -m picturepick serve`，
打包后 `resources/backend/picturepick-backend.exe`），轮询 `/api/health` 就绪后加载
`http://127.0.0.1:<port>/`；退出时用 `taskkill /T /F` 杀掉整个后端进程树。

## 目录结构

```
desktop/
├── src/
│   ├── main.ts                     # 主进程：窗口、端口、后端 sidecar、健康检查、IPC、退出清理
│   ├── preload.ts                  # contextBridge 白名单 API → window.picturePickDesktop
│   └── types.d.ts                  # Window 类型声明
├── pyinstaller/
│   ├── picturepick-backend.spec    # onedir 打包配置（产物 build/backend/picturepick-backend.exe）
│   ├── backend_entry.py            # 薄入口：from picturepick.cli import main
│   └── version_info.txt            # exe 版本信息
├── scripts/
│   ├── make_icon.py                # 用 Pillow 生成 resources/icon.png / icon.ico
│   ├── install.ps1                 # 固定环境变量的 npm install 封装
│   └── pack-portable.mjs           # 不依赖 electron-builder 的绿色版打包兜底
├── resources/                      # icon.png(512) / icon.ico(多尺寸)
├── electron-builder.yml            # appId/productName、portable + nsis、extraResources
├── package.json / tsconfig.json
└── dist/                           # tsc 输出（main.js / preload.js）
```

## 一次性安装依赖（必须带这些环境变量）

沙箱内 HOME/AppData 不可写，npm 生命周期脚本用管道 stdio 会 EPERM，GitHub 不可达，
因此安装命令必须固定以下环境变量（已封装为 `scripts/install.ps1`）：

```powershell
$env:npm_config_cache='E:\picturePick\.cache\npm-desktop'
$env:npm_config_logs_dir='E:\picturePick\.cache\npm-desktop\_logs'
$env:npm_config_foreground_scripts='true'          # 生命周期脚本继承 stdio，避免 spawn EPERM
$env:electron_config_cache='E:\picturePick\.cache\electron'   # 注意：小写，electron install.js 只认这个
$env:ELECTRON_MIRROR='https://npmmirror.com/mirrors/electron/'  # github 不可达
$env:ELECTRON_BUILDER_CACHE='E:\picturePick\.cache\electron-builder'
$env:ELECTRON_BUILDER_BINARIES_MIRROR='https://npmmirror.com/mirrors/electron-builder-binaries/'
cd E:\picturePick\desktop
npm install --foreground-scripts
```

或直接：`powershell -ExecutionPolicy Bypass -File scripts\install.ps1`

## 编译

```powershell
cd E:\picturePick\desktop
npm run build       # tsc -p tsconfig.json → dist/main.js、dist/preload.js
```

## 开发模式启动（依赖 backend/ 已就绪）

```powershell
cd E:\picturePick\desktop
npm start           # 编译后 electron .
```

- 开发模式后端命令：`python -m picturepick serve --host 127.0.0.1 --port <空闲端口> --no-open`，cwd 为 `E:\picturePick\backend`。
- 可用 `PICTUREPICK_PYTHON` 覆盖解释器路径（例如 `C:\Python310\python.exe`）。
- 后端 stdout/stderr 写入 `<userData>/logs/backend.log`（沙箱下自动退化到 `desktop/out/logs/backend.log`），
  开发模式同时打印到控制台。主进程日志为同目录的 `main.log`。

## 打包 Python 后端（PyInstaller onedir）

```powershell
cd E:\picturePick\desktop
npm run pack:backend
# 等价于：
# python -m PyInstaller --noconfirm --clean --distpath ../build --workpath ../build/.pyinstaller-work pyinstaller/picturepick-backend.spec
```

产物：

```
E:\picturePick\build\backend\picturepick-backend.exe
E:\picturePick\build\backend\_internal\...        # Python 运行时与依赖
```

说明：
- 前端构建产物 `backend/picturepick/webui/**` 会被内嵌到 `_internal/webui`（后端通过 `sys._MEIPASS/webui` 定位）。
  若前端尚未构建，spec 会打印警告并跳过（此时 `/` 只返回“前端未构建”的 JSON 提示）；前端构建完成后重新执行 `pack:backend` 即可。
- 验证后端可执行文件：`build\backend\picturepick-backend.exe version` / `serve --port 18000 --no-open` 后访问 `/api/health`。

## 打包 Electron 应用（electron-builder）

```powershell
cd E:\picturePick\desktop
npm run pack:backend   # 先准备后端 sidecar
npm run dist           # tsc + electron-builder（portable + nsis）→ E:\picturePick\release\
npm run dist:dir       # 只解包，不生成安装包（用于证明打包链路，产物 release\win-unpacked\）
```

- 配置见 `electron-builder.yml`：appId `com.picturepick.app`、productName `PicturePick`、asar 开启、
  `extraResources` 把 `../build/backend` 复制到 `resources/backend`。
- 产物位置：`E:\picturePick\release\PicturePick-1.0.0-win-x64-portable.exe`、
  `E:\picturePick\release\PicturePick-1.0.0-win-x64-setup.exe`，解包目录 `release\win-unpacked\`。

### 网络受限 / 沙箱 EPERM 时的兜底方案

1. **electron-builder 需要联网下载 winCodeSign / nsis / 7za**。设置了上面的
   `ELECTRON_BUILDER_BINARIES_MIRROR` 后从 npmmirror 下载；若仍失败，先用
   `npm run dist:dir`（`electron-builder --dir`）证明“asar 打包 + extraResources + Electron 运行时”链路可用，
   然后使用下面的绿色版脚本。
2. **Electron 二进制缓存路径**：electron-builder 内部走 `@electron/get`，其默认缓存是
   `%LOCALAPPDATA%\electron\Cache`（本沙箱不可写，会报 `EPERM: mkdir ...\AppData\Local\electron\Cache\...`）。
   已在 `electron-builder.yml` 中用 `electronDownload.cache: E:\picturePick\.cache\electron-builder\electron`
   指到工作区；`npm install` 用的则是 `electron_config_cache`（小写）环境变量。
3. **生产 node_modules 收集**：electron-builder 会调用 `npm list`（管道 stdio → 沙箱内 `spawn EPERM`）收集依赖。
   本应用没有运行时 node 依赖（tsc 产物全在 `dist/`），因此 `electron-builder.yml` 配置了
   `beforeBuild: scripts/before-build.js`，该钩子返回 `false` 表示“node_modules 由我们自己处理”，
   electron-builder 会跳过 npm install/rebuild 与依赖收集。
4. **electron-builder 内部仍用管道 stdio 拉起 app-builder.exe / 7za.exe，在受限沙箱可能报 `spawn EPERM`**。
   此时用不创建管道、纯 `fs` 复制的兜底脚本产出可直接双击运行的绿色版：

   ```powershell
   cd E:\picturePick\desktop
   npm run pack:portable        # → release\win-unpacked\PicturePick.exe + release\PicturePick-1.0.0-win-x64-portable.zip
   ```

   该脚本做的事与 electron-builder 等价：复制 `node_modules/electron/dist` → `release/win-unpacked`、
   `electron.exe` 改名 `PicturePick.exe`、写入 `resources/app/{package.json,dist}`、复制
   `build/backend` → `resources/backend`，最后用 `Compress-Archive` 压缩 zip。
5. **受限沙箱内生成安装包（portable.exe / nsis setup.exe）**：electron-builder 打包安装包时会
   `execFile` 调 7za.exe、并把 NSIS 脚本写入 makensis 的 stdin（管道 stdio），沙箱会 `spawn EPERM`。
   `scripts/eb-sandbox.cjs` 在加载 electron-builder 前给 `child_process.execFile` 和
   `builder-util` 的 `spawnAndWriteWithOutput` 打补丁：输出重定向到临时文件、NSIS 脚本落盘后把
   参数 `-` 换成文件路径（语义等价）。沙箱内用它生成完整安装包：

   ```powershell
   cd E:\picturePick\desktop
   npm run dist:sandbox        # → release\PicturePick-1.0.0-win-x64-portable.exe + ...setup.exe
   npm run dist:dir:sandbox    # 只解包（等价 electron-builder --dir）
   node scripts/eb-sandbox.cjs --config electron-builder.yml   # 底层命令
   ```

   > 普通（非沙箱）环境无需该包装器，直接 `npm run dist` 即可。

## 验证与调试环境变量

| 变量 | 作用 |
| --- | --- |
| `PICTUREPICK_DESKTOP_TEST_URL` | 跳过后端，直接加载指定 URL（用于 Electron 空壳启动验证） |
| `PICTUREPICK_DESKTOP_SCREENSHOT` | 启动/加载完成后把窗口截图写到该 PNG 路径 |
| `PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS` | 截图前等待毫秒（默认 1500） |
| `PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT=1` | 截图后自动退出（用于脚本化验证） |
| `PICTUREPICK_DESKTOP_HEADLESS=1` | 启动失败时不弹模态错误框，直接退出 |
| `PICTUREPICK_DESKTOP_USER_DATA` | 覆盖 Electron userData 目录（沙箱内建议指向工作区） |
| `PICTUREPICK_DESKTOP_LOG_DIR` | 覆盖日志目录 |
| `PICTUREPICK_DESKTOP_BACKEND_TIMEOUT_MS` | 等待 `/api/health` 超时（默认 60000） |
| `PICTUREPICK_PYTHON` | 开发模式覆盖 Python 解释器 |

示例（沙箱内空壳启动 + 截图 + 自动退出）：

```powershell
$env:PICTUREPICK_DESKTOP_TEST_URL='data:text/html,<body style="background:%230f1115;color:%23eee">PicturePick shell OK</body>'
$env:PICTUREPICK_DESKTOP_SCREENSHOT='E:\picturePick\desktop\out\shell.png'
$env:PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT='1'
$env:PICTUREPICK_DESKTOP_USER_DATA='E:\picturePick\.tmp\desktop\userdata'
npx electron .
```

## 退出清理

- `will-quit` 中先 `taskkill /PID <pid> /T /F` 杀掉后端进程树再真正退出（最多等待 5s）；
- 若后端在启动阶段异常退出，主进程会显示错误页 + 错误对话框并退出（`main.log`/`backend.log` 保留现场）；
- 单实例：第二个实例启动时聚焦已有窗口。

## 已实现 IPC（preload 暴露）

```ts
window.picturePickDesktop = {
  isDesktop: true,
  pickDirectory(): Promise<string | null>,             // 原生目录选择
  saveJson(defaultName, content): Promise<string | null>, // 原生另存为写 JSON
  openPath(path): Promise<string>,                     // 系统默认程序打开
};
```

## 验证脚本（Lead 复核用）

| 脚本 | 作用 |
| --- | --- |
| `scripts/verify-shell.ps1 -Mode shell` | 空壳验证：跳过后端，加载内置页面，截图到 `desktop/out/verify-shell.png`（不需要 backend/） |
| `scripts/verify-shell.ps1 -Mode app` | 真实验证：拉起后端 + 真实前端，截图 `desktop/out/verify-app.png`，并检查退出后端口释放 |
| `scripts/verify-package.ps1 -Mode backend` | 验证 `build/backend/picturepick-backend.exe`：version / serve / `/api/health` / 端口清理 |
| `scripts/verify-package.ps1 -Mode portable` | 验证绿色版目录 + zip |
| `scripts/verify-package.ps1 -Mode dist [-FullDist]` | 验证 electron-builder `--dir`（加 `-FullDist` 生成 portable+nsis 安装包） |
| `scripts/verify-package.ps1 -Mode all` | 依次执行上述三项 |

> **重要**：窗口验证（`verify-shell.ps1`）必须在**非 DSH 沙箱**的 PowerShell 中执行。
> Chromium 通过命名管道创建 Mojo 进程通道，DSH 沙箱禁止创建命名管道，导致 `electron.exe`
> 一启动就 `EXCEPTION_BREAKPOINT`（连 `electron.exe --version` 都会崩，退出码 -2147483645）。
> 这是环境限制而非代码问题；`verify-package.ps1 -Mode backend/portable` 可以在沙箱内执行。

```powershell
# 在普通（非沙箱）PowerShell 中：
powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-shell.ps1 -Mode shell
powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-shell.ps1 -Mode app
```

补充说明：
- 所有 `.ps1` 脚本均为纯 ASCII，且在 Windows PowerShell 5.1 与 PowerShell 7 下均可直接运行（避免编码问题）。
- `verify-shell.ps1` 会自动清除 `ELECTRON_RUN_AS_NODE`（某些 CI/终端会全局设置它，导致 Electron 以纯 Node 运行）。

## 验收记录

自测环境：Windows 10.0.22621 / Node v22.20.0 / npm 10.9.3 / Python 3.10.11 / PyInstaller 6.22.3 /
Electron 33.4.11 / electron-builder 26.18.0。以下命令均在 `E:\picturePick\desktop` 执行。

| 检查项 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 依赖安装 | `npm install --foreground-scripts`（固定缓存/镜像环境变量） | ✅ | 321 packages；`node_modules/electron/dist/electron.exe` 188 MB |
| TS 编译 | `npm run build` | ✅ 0 error | `dist/main.js`（29 KB）、`dist/preload.js` |
| PyInstaller spec 机制 | 用 `.tmp` 假后端先跑通同款 spec | ✅ | 产出 `backend/picturepick-backend.exe`，`version` 可运行 |
| 后端 sidecar | `npm run pack:backend` | ✅ | `build/backend/picturepick-backend.exe` 5,609,169 B；`_internal/webui/index.html` 已内嵌；无 `desktop/dist/backend` 残留 |
| 后端功能 | `npm run verify:backend` | ✅ | version 1.0.0；`/api/health` ok；`GET /` 200 text/html；kill 后端口释放 |
| 后端验证脚本 | `verify-package.ps1 -Mode backend -RequireWebui` | ✅ | 脚本路径与 Node 验证器联动正常 |
| 绿色版兜底 | `npm run pack:portable` | ✅ | `release/win-unpacked/PicturePick.exe`（346.1 MB）；`release/PicturePick-1.0.0-win-x64-portable.zip`（139.6 MB） |
| electron-builder 解包 | `npm run dist:dir` / `node scripts/eb-sandbox.cjs --dir --config electron-builder.yml` | ✅ | `release/win-unpacked/`：PicturePick.exe、`resources/app.asar`（31 KB，含 dist/main.js、dist/preload.js、package.json）、`resources/backend/**` |
| 完整安装包 | `npm run dist:sandbox`（沙箱包装器） | ✅ | `release/PicturePick-1.0.0-win-x64-portable.exe` 94.8 MB；`release/PicturePick-1.0.0-win-x64-setup.exe` 106.5 MB（+blockmap） |
| 安装包完整性 | `7za l <installer>` | ✅ | 两个 exe 均为有效 7z/NSIS 归档（各 1846 条目），含 `resources\backend\**`、`resources\app.asar`、`_internal\webui` |
| 打包验证脚本 | `verify-package.ps1 -Mode dist` | ✅ | 自动 `npm run build` + `electron-builder --dir`，断言 PicturePick.exe / app.asar / 内置后端全部 PASS |
| 窗口启动/截图 | `scripts/verify-shell.ps1 -Mode shell/app` | ⏳ 需非沙箱执行 | 见下节说明 |
| 退出清理后端 | `verify-backend.mjs` / `verify-shell.ps1 -Mode app` | ✅（sidecar 层） | taskkill 后端口立即释放；app 层由 verify-shell `-Mode app` 复核 |

### 沙箱限制（为什么窗口截图要单独执行）

DSH 沙箱禁止创建命名管道，而 Chromium 的进程间通信（Mojo platform channel）依赖命名管道：

- `electron.exe --version` → 退出码 `-2147483645`（EXCEPTION_BREAKPOINT），连版本号都输出不了；
- 最小探针（不含本项目代码）加 `--no-sandbox` 可到 `app ready`，随后 Chromium 日志：
  `FATAL:platform_channel.cc(89)] Check failed: . : 拒绝访问。(0x5)`；
- `--no-sandbox` / `--single-process` 均无法绕过。

因此窗口验证脚本 `scripts/verify-shell.ps1` 必须在普通（非沙箱）PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-shell.ps1 -Mode app
# 产物：desktop\out\verify-app.png、.tmp\desktop\logs\main.log、backend.log
```

`-Mode app` 会：拉起 `python -m picturepick serve`（开发模式）→ 轮询 `/api/health` → 加载真实前端 →
截图 → 退出 → 校验后端端口已释放。`-Mode shell` 则不依赖 backend/frontend，用于验证壳本身。

> 打包产物与后端 sidecar 的验证（`verify-backend.mjs`、`verify-package.ps1 -Mode backend`）不受该限制，
> 已在本沙箱内全部通过。
