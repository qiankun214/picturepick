# PicturePick 桌面版（PyInstaller + WebView2）

Windows 原生桌面外壳：**Python 后端内置 + pywebview/Edge WebView2 窗口**，单个 `PicturePick.exe` 双击即用，
不需要 Electron，也不需要预装 Python。

## 为什么是这个方案

本机（Windows 11）上 Electron 的 Chromium 进程在启动阶段即崩溃（`platform_channel`/`0xc0000005`，
`electron.exe --version` 亦复现，Electron 28/31/33/44 多版本均如此），与项目代码无关；
而系统自带的 Edge / WebView2 运行正常。因此桌面壳改为：

- **后端**：与命令行模式完全相同的 FastAPI 应用，在进程内后台线程用 uvicorn 启动；
- **窗口**：pywebview 6.x（成熟、活跃，Windows 上使用 Edge WebView2）；
- **打包**：PyInstaller onedir，内置 Python 运行时、后端、前端与 WebView2 桥接依赖。

## 目录

```
desktop_webview/
├── picturepick_desktop/
│   ├── desktop.py        # 主流程：空闲端口 → 后端线程 → /api/health → pywebview 窗口 → 退出清理
│   ├── __main__.py       # 入口（PyInstaller 与 python -m picturepick_desktop 共用）
│   └── __init__.py
├── picturepick-desktop.spec   # PyInstaller 打包配置（内嵌 webui 与 pywebview/pythonnet）
├── requirements.txt
└── README.md
```

## 依赖

```powershell
python -m pip install -r desktop_webview/requirements.txt
```

- `pywebview>=6.2`（Windows 走 Edge Chromium/WebView2 后端，依赖 `pythonnet>=3.1`）
- Windows 10/11 需要 WebView2 运行时：Win11 自带；Win10 一般随 Edge 安装，缺失时从微软官网安装。

## 开发运行（源码）

```powershell
cd E:\picturePick
$env:PYTHONPATH='E:\picturePick\backend;E:\picturePick\desktop_webview'
python -m picturepick_desktop --dir "E:\picturePick\samples\photos"
```

命令行参数：

| 参数 | 说明 |
| --- | --- |
| `--dir PATH` | 启动前预扫描目录，窗口直接进入选片模式 |
| `--no-recursive` | 只扫描顶层目录（默认递归） |
| `--port N` | 后端固定端口（默认自动选空闲端口） |
| `--debug` | 打开 pywebview 调试模式 |
| `--minimized` | 以最小化方式启动 |

## 打包

```powershell
cd E:\picturePick
python -m PyInstaller --noconfirm --clean `
  --distpath build/webview --workpath build/.pyinstaller-webview `
  desktop_webview/picturepick-desktop.spec
```

产物：`build/webview/PicturePick/PicturePick.exe`（整个 `PicturePick/` 目录一起分发或压缩）。

也可在仓库根目录执行一键构建（推荐）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# → release\PicturePick-WebView2\PicturePick.exe 与 release\PicturePick-WebView2-win-x64.zip
```

## 桌面端能力

- 原生目录选择框（`window.pywebview.api.pick_directory()` → 系统文件夹对话框）
- 结果 JSON 原生「另存为」（`save_json()` → 系统保存对话框，UTF-8 写入）
- 窗口关闭后自动停止后端线程，不残留进程
- 数据只在 `127.0.0.1` 本机回环，不上传任何照片

## 自动化验收钩子（仅设置环境变量时生效）

| 变量 | 作用 |
| --- | --- |
| `PICTUREPICK_DESKTOP_SCREENSHOT` | 窗口加载后把**窗口区域**截图保存为 PNG，同时把 DOM 断言 JSON 存为同名 `.json` |
| `PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS` | 截图前等待毫秒（默认 4500） |
| `PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT=1` | 截图后自动退出 |
| `PICTUREPICK_DESKTOP_EVAL` | 自定义 JS 表达式（在窗口内执行并把结果写到 JSON） |
| `PICTUREPICK_DESKTOP_USER_DATA` | 覆盖用户数据目录 |
| `PICTUREPICK_DESKTOP_LOG_DIR` | 覆盖日志目录 |

示例：

```powershell
$env:PICTUREPICK_DESKTOP_SCREENSHOT='E:\picturePick\.tmp\webview\verify.png'
$env:PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT='1'
& 'E:\picturePick\release\PicturePick-WebView2\PicturePick.exe' --dir 'E:\picturePick\samples\photos'
```

## 已知限制

- WebView2 窗口使用系统 Edge 内核，缩放/字体跟随系统 DPI；若机器缺少 WebView2 运行时会弹窗提示。
- 原生对话框（选择目录/另存为）由 pywebview 调用系统对话框，无法在无头自动化中点击，需人工确认一次。
