# PicturePick 打包说明（Windows / Electron）

> 本文是**打包与部署的唯一操作手册**。架构/选型背景见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，
> 跨平台打包调研见 [`PACKAGING-RESEARCH.md`](PACKAGING-RESEARCH.md)，Python 升级见 [`PYTHON-UPGRADE.md`](PYTHON-UPGRADE.md)。

## 0. 当前方案概览

| 项 | 说明 |
| --- | --- |
| 桌面壳 | **Electron 33**（内置 Chromium，窗口/原生对话框/进程管理都在 `desktop/src/`） |
| 后端 | 同一份 FastAPI 应用，用 **PyInstaller onedir** 打成 sidecar（`desktop/pyinstaller/`） |
| 前端 | Vite 构建产物内嵌到后端 `backend/picturepick/webui`，由后端静态托管 |
| 产物 | 绿色目录 + portable zip（可选 NSIS/portable 安装包） |
| 兼容方案 | WebView2 + pywebview（`desktop_webview/`，Windows pythonnet 兼容问题，不再默认） |
| 兜底方案 | 浏览器版（后端 + 系统浏览器/Edge App 窗口），见 `release/browser/` |

## 1. 前置环境

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Windows | 10/11 x64 | Electron 33 与 PyInstaller 均在此验证 |
| Python | **3.12** | 3.10 已 EOL；`build.ps1` 会优先用 `py -3.12` |
| Node.js / npm | 18+ | 用于编译 Electron 主进程与 Vite 前端 |
| 网络 | PyPI / npm | 首次安装依赖；离线时需已有 `.cache/`、`node_modules` |

首次安装依赖：

```powershell
cd E:\picturePick
py -3.12 -m pip install -r backend\requirements.txt
py -3.12 -m pip install pyinstaller
npm --prefix desktop install --foreground-scripts --no-audit --no-fund
```

> ⚠️ 不要用裸 `python` 命令：本机可能被 Microsoft Store 的别名抢占。统一用 `py -3.12`，
> 打包脚本内部已经自动解析（优先 `.venv` → `py -3.12` → `python`）。

## 2. 一键打包（推荐）

双击根目录 **`build-electron.cmd`**，或在 PowerShell 执行：

```powershell
cd E:\picturePick
powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1
```

它会依次执行：

1. 前端构建 → `backend/picturepick/webui`
2. `tsc` 编译 Electron 主进程/preload → `desktop/dist`
3. PyInstaller 打包后端 sidecar → `build/backend/picturepick-backend.exe`
4. 组装 Electron 绿色版 → `release/win-unpacked/`
5. 生成 portable zip → `release/PicturePick-1.0.0-win-x64-portable.zip`
6. 生成启动器（排错用）与浏览器兜底包 → `release/browser/`
7. 打印 `electron.exe --version` 运行时自检结果

### 常用参数

| 参数 | 作用 |
| --- | --- |
| `-SkipFrontend` | 跳过前端构建（前端未改时提速） |
| `-SkipBuild` | 完全跳过构建，只跑自检/生成浏览器兜底包（秒级） |
| `-Deploy` | 构建后把绿色版复制到桌面 `PicturePick` 并创建桌面快捷方式 |
| `-WithInstaller` | 额外尝试 electron-builder 生成 NSIS/portable 安装包（需联网或缓存） |
| `-RunDebug` | 构建后自动打开调试启动器（带日志） |

示例：

```powershell
# 改了 Electron 主进程，只重打桌面端并部署到桌面
powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1 -SkipFrontend -Deploy

# 只验证 Electron 运行时与已有产物
powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1 -SkipBuild
```

> 完整构建约 2–3 分钟（首次更久）。`build-electron.cmd` 会把全部输出同时写入
> 根目录 `build-electron.log`，窗口即使关闭也能查看。

## 3. 分步命令（手动排查用）

```powershell
cd E:\picturePick

# 1) 前端
cd frontend; npm run build; cd ..
#    产物：backend/picturepick/webui

# 2) Electron 主进程 / preload
node desktop\node_modules\typescript\bin\tsc -p desktop\tsconfig.json
#    产物：desktop/dist/main.js、preload.js

# 3) 后端 sidecar
py -3.12 -m PyInstaller --noconfirm --clean `
  --distpath build --workpath build\.pyinstaller-work `
  desktop\pyinstaller\picturepick-backend.spec
#    产物：build/backend/picturepick-backend.exe + _internal/
#    自检：build\backend\picturepick-backend.exe version

# 4) 组装绿色版 + zip（不依赖 electron-builder，受限环境也能用）
node desktop\scripts\pack-portable.mjs
#    产物：release/win-unpacked/、release/PicturePick-<version>-win-x64-portable.zip
```

## 4. 部署与运行（重要）

### 4.1 必须放在普通目录运行

本机 DSH 工作区（`E:\picturePick`）会给文件加 **Low 完整性级别 + 拒绝型 ACL**，
Electron 的 Chromium 在该目录启动会 **STATUS_BREAKPOINT（0x80000003）静默崩溃**（无窗口）。
证据与排查过程见 `docs/PACKAGING-RESEARCH.md` 与 `.tmp/` 日志。

所以：

- ✅ 把 `PicturePick-<version>-win-x64-portable.zip` 复制到桌面/`D:\Apps` 等普通目录，解压后
  **直接双击 `PicturePick.exe`**；
- ✅ 或用 `-Deploy` 自动部署到 `%USERPROFILE%\Desktop\PicturePick` 并建快捷方式；
- ❌ 不要在 `E:\picturePick\release\win-unpacked` 里直接双击。

### 4.2 启动器说明

| 文件 | 用途 |
| --- | --- |
| `PicturePick.exe` | **正常启动方式**，直接双击（无需任何 cmd） |
| `Start-PicturePick.cmd` | 仅当从 DSH/终端启动、环境里有 `ELECTRON_RUN_AS_NODE=1` 时使用（会清该变量） |
| `Start-PicturePick-Debug.cmd` | 排错用：设置调试日志并把控制台输出写到包内 `logs\console.log` |
| `resources/backend/picturepick-backend.exe` | 后端 sidecar，由主进程自动拉起，无需手动运行 |

### 4.3 日志与自检

```powershell
# 后端/窗口自检（需在普通目录的绿色版上跑）
powershell -ExecutionPolicy Bypass -File scripts\verify-electron.ps1
```

| 日志 | 位置 |
| --- | --- |
| Electron 主进程 | `%APPDATA%\PicturePick\logs\main.log` |
| 后端 sidecar | `%APPDATA%\PicturePick\logs\backend.log` |
| 启动早期日志（崩溃前） | `%TEMP%\picturepick-startup\startup.log`（或 `PICTUREPICK_DESKTOP_LOG_DIR`） |
| 调试启动器控制台 | 包内 `logs\console.log` |

## 5. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 双击 exe 完全没反应 | ① 是否在 `E:\picturePick` 里双击？→ 复制到桌面再试；② 是否有残留 `PicturePick.exe`（任务管理器结束）；③ 看 `%TEMP%\picturepick-startup\startup.log`；④ 用 `Start-PicturePick-Debug.cmd` 看日志 |
| 从终端启动没反应 | 环境里有 `ELECTRON_RUN_AS_NODE=1`，用 `Start-PicturePick.cmd` 或先 `Remove-Item Env:\ELECTRON_RUN_AS_NODE` |
| 点右上角 X 没反应 | 选片页注册了 `beforeunload` 防误退出；Electron 主进程已加 `will-prevent-unload` 原生确认框，选择「退出程序」即可关闭（见 §6） |
| 提示单实例锁 | 已有实例在运行（可能有隐藏窗口），任务管理器结束所有 `PicturePick.exe` 后重试 |
| 后端没起来/窗口显示错误页 | 看 `main.log`、`backend.log`；确认 `resources/backend/picturepick-backend.exe` 存在（重新打包） |
| `electron.exe --version` 报 WARN | Electron 二进制被安全策略/运行库拦截；尝试升级 Electron（`npm --prefix desktop install electron@latest`）或改用浏览器兜底包 |
| 打包脚本报 `electron.exe --version` 检查异常 | 已用 try/catch 包裹，不影响产物；把 `build-electron.log` 发开发者 |
| 安装包构建失败 | `-WithInstaller` 依赖联网/缓存；绿色版 zip 仍可正常使用 |

## 6. 选片页退出确认（Electron）

前端在选片页（`.picker` 存在时）注册了 `beforeunload`。浏览器会弹"离开此页面？"，
Electron 不会自动弹；因此 `desktop/src/main.ts` 监听 `will-prevent-unload`，
用原生对话框询问：

- **继续选片** → 取消关闭；
- **退出程序** → 忽略 `beforeunload`，窗口关闭并结束后端进程。

## 7. 兼容方案：WebView2 / pywebview（可选）

仅在需要对比或排查时使用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -WebView2
# 产物：release/PicturePick-WebView2/PicturePick.exe
```

已知问题：Windows 上 pythonnet 冻结后报
`Failed to resolve Python.Runtime.Loader.Initialize`（上游 pywebview #1832 未发版），
因此**不作为默认方案**。详见 `PACKAGING-RESEARCH.md` §4.3。

## 8. 浏览器兜底包

无需 Electron，直接使用系统浏览器/Edge App 窗口：

```powershell
# 构建后自动生成；也可单独重建：
powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1 -SkipBuild
# 运行：
release\browser\Start-PicturePick-Browser.cmd
```

启动器会：清 `ELECTRON_RUN_AS_NODE` → 最小化启动后端（127.0.0.1:8111）→
用 Edge `--app` 模式打开独立窗口。

## 9. 跨平台（规划中）

Electron 壳本身跨平台，后端 sidecar 需在各自平台构建（PyInstaller 不支持交叉编译）：

| 平台 | 产物 | 构建位置 |
| --- | --- | --- |
| Windows | win-unpacked / zip / NSIS | 本机 |
| macOS | `.app` / `.dmg` | macOS（或 CI `macos-*` runner），需签名/公证 |
| Linux | AppImage / deb | `ubuntu-22.04` CI 或 Docker |

`electron-builder.yml` 目前只有 Windows target；mac/Linux 的 target、图标（.icns/.png）、
签名与 CI 矩阵详见 `PACKAGING-RESEARCH.md` 的路线图。

## 10. 版本与路径速查

| 项 | 值 |
| --- | --- |
| Electron | 33.4.11（`desktop/package.json`） |
| electron-builder | 26.18.0 |
| Python / PyInstaller | 3.12 / 6.22.3 |
| 构建入口 | `build-electron.cmd` → `scripts/build-electron.ps1` → `scripts/build.ps1` |
| 自检入口 | `verify-desktop.cmd` → `scripts/verify-electron.ps1` |
| 绿色版 | `release/win-unpacked/PicturePick.exe` |
| zip | `release/PicturePick-1.0.0-win-x64-portable.zip` |
| 后端 sidecar | `build/backend/picturepick-backend.exe` |
| Electron 编译产物 | `desktop/dist/main.js`、`preload.js` |

## 11. 修改记录

- 2026-10-25：桌面壳从 pywebview/WebView2 切换为 Electron；新增 `build-electron.cmd`、`verify-electron.ps1`、
  `-Deploy`、浏览器/Edge App 兜底包；修复窗口模式 stdout/stderr 与 pythonnet 相关启动问题。
- 2026-11-01：新增 `will-prevent-unload` 原生确认框（选片页点 X 可关闭）；补充本打包说明。
