# PicturePick · 本地照片逐轮选片器

一个 **前后端分离** 的本地选片工具：后端 Python（FastAPI）+ 前端 TypeScript/HTML（Vite），
既可以用「命令行启动 + 浏览器操作」，也可以直接运行打包好的 **Windows 桌面程序**。

核心玩法：用**浏览器自带的文件夹选择框**选一个照片目录 → 逐张浏览、放大/缩小、可自动放映 →
点「保留（pick）」标记心仪照片 → 结束本轮后 **只在已保留的照片里继续下一轮** → 循环几轮直到选出最终结果 →
下载 JSON（记录每张照片被 pick 的总次数）并**一键把最后一轮保留的照片导出到指定文件夹**。

![选择照片文件夹](docs/screenshots/start.png)

## 功能一览

| # | 需求 | 实现 |
| --- | --- | --- |
| 1 | 选择目录 | **浏览器自带文件夹选择框**（`<input type="file" webkitdirectory>`），无自定义目录弹窗；照片只在本地浏览器读取，不上传 |
| 2 | 扫描 jpg | 前端筛选 `.jpg` / `.jpeg`（大小写不敏感），递归保留相对路径，按路径排序 |
| 3 | 选片 + 自动放映 | 上一张 / 下一张按钮；自动放映可设置间隔秒数、播放/暂停；到末尾自动停止 |
| 4 | 常规显示操作 | 滚轮以光标为中心缩放、拖拽平移、± 缩放、适应窗口、1:1、双击切换、旋转、全屏 |
| 5 | pick 与统计 | 「保留」按钮可点选/取消；顶栏实时显示 当前序号 / 已浏览 / 本轮保留 / 总数 |
| 6 | 多轮筛选 | 「结束本轮」弹窗显示保留数量并询问是否进入下一轮（仅从保留照片中继续）；可随时结束 |
| 7 | 生成 JSON | 结果页下载/另存 JSON：每张照片的 `pickCount` 与 `pickedInRounds`（被 pick 的轮次） |
| 8 | 一键导出照片 | 结果页「导出保留照片」：把**最后一轮仍保留**的照片保存到用户指定的文件夹（File System Access API；不支持时降级为逐张下载） |

界面：深色 Lightroom 风格、大图 + 底部胶片条（本地生成缩略图）、键盘快捷键、加载失败重试、
未结束会话离开提示、错误 toast。所有照片数据只在本机浏览器/进程内处理，后端只监听 `127.0.0.1`。

![选片界面](docs/screenshots/picker.png)
![结束本轮](docs/screenshots/end-round-dialog.png)
![结果页与一键导出](docs/screenshots/export-photos.png)

## 三种运行方式

### 方式一：桌面版（免安装，开箱即用）

桌面版是 **PyInstaller + pywebview（Edge WebView2）** 原生窗口，内置 Python 后端与前端资源，
不需要预装 Python，也不依赖 Electron。

**打包**（在本机运行，一条命令）：

```powershell
# 在资源管理器里双击 build.cmd，或执行：
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物：

| 路径 | 说明 |
| --- | --- |
| `release/PicturePick-WebView2/PicturePick.exe` | 绿色版目录（整个目录一起分发，双击即用，约 33 MB） |
| `release/PicturePick-WebView2-win-x64.zip` | 上述目录的压缩包（约 17 MB） |

> 若之前构建过 Electron 版，其产物已移到 `release/_legacy-electron/`；
> `release/win-unpacked/` 是更早的 Electron 绿色版残留（本机 Electron 无法启动，可手动删除），
> **当前桌面版请使用 `release/PicturePick-WebView2/PicturePick.exe`**。

**启动后自检 / 验证**：

```powershell
# 双击 verify-desktop.cmd，或执行：
powershell -ExecutionPolicy Bypass -File scripts\verify-desktop.ps1
```

脚本会先做不开窗口的运行时自检（后端/前端/pythonnet/WebView2 桥接），再启动真实窗口、
自动截图并退出，最后打印 PASS/FAIL、截图路径与日志。

> 只想验证打包运行时（不弹窗口）可执行：`release\PicturePick-WebView2\PicturePick.exe --selftest-backend`，
> 结果写入 `%LOCALAPPDATA%\PicturePick\logs\desktop.log`。

> 打包/自检输出如果失败，请把控制台输出与 `.tmp/desktop-verify/` 下的日志发给开发者。
> 需要排查启动崩溃时可用带控制台的调试版：`powershell -File scripts\build.ps1 -Console`，
> 然后运行 `build\webview-console\PicturePickConsole\PicturePick.exe --selftest` 查看完整 traceback。

### 方式二：命令行 + 浏览器（开发/日常使用）

```powershell
cd E:\picturePick\backend
python -m picturepick serve --dir "D:\我的照片"     # 启动后自动打开浏览器
```

- 不加 `--dir` 也可以：启动后在网页里点「选择照片文件夹」。
- 默认端口 `8000`，可用 `--port 8100` 指定；`--no-open` 不自动打开浏览器。
- 其他命令：

```powershell
python -m picturepick version
python -m picturepick scan --dir "D:\我的照片"              # 预览会扫到多少张（默认递归）
python -m picturepick scan --dir "D:\我的照片" --no-recursive
```

依赖：Python 3.10+，`pip install -r backend/requirements.txt`（FastAPI / uvicorn / Pillow / pydantic）。

### 方式三：开发模式（前后端分离调试）

```powershell
# 一条命令同时起后端(8000) + Vite 前端(5173)
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
# 浏览器打开 http://127.0.0.1:5173
```

前端构建产物固定输出到 `backend/picturepick/webui`，因此也可以只跑后端：

```powershell
cd frontend; npm install; npm run build      # → backend/picturepick/webui
cd ..\backend; python -m picturepick serve    # → http://127.0.0.1:8000/
```

## 使用流程

1. **选择文件夹**：点击「选择照片文件夹」，在浏览器/系统弹出的原生文件夹选择框里选相册目录。
2. **逐张筛选**：`←/→` 或按钮切换；滚轮缩放、拖拽平移；空格自动放映；`P` 或「保留」按钮标记。
3. **结束本轮**：点击「结束本轮」，弹窗显示本轮保留数量：
   - 「进入下一轮」：仅在保留的照片中继续筛选（**至少保留 2 张**才可进入下一轮）；
   - 「结束并生成结果」：随时可结束，生成最终 JSON。
4. **重复第 2–3 步**，每轮范围都会缩小。
5. **结果页**：
   - 查看摘要与按 `pickCount` 排序的列表；
   - 「下载 JSON」保存选片记录；
   - 「导出保留照片」选择目标文件夹，一键保存最后一轮保留的照片（自动处理同名文件）。

### 快捷键

| 按键 | 作用 | 按键 | 作用 |
| --- | --- | --- | --- |
| `←` / `→` | 上一张 / 下一张 | `P` | 保留 / 取消保留 |
| `空格` | 自动放映 播放/暂停 | `+` / `-` | 放大 / 缩小 |
| `0` | 适应窗口 | `1` | 原始大小 1:1 |
| `R` | 旋转 | `F` | 全屏 |
| `Esc` | 关闭弹窗 / 退出全屏 | | |

## 结果 JSON 格式

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-10-25T14:34:00+08:00",
  "origin": "browser",
  "sourceDirectory": "我的照片",
  "recursive": true,
  "totalPhotos": 120,
  "roundsPlayed": 3,
  "pickedPhotoCount": 12,
  "lastRound": {
    "index": 3,
    "pickedCount": 2,
    "pickedPhotoIds": ["011b8657457565a2", "d40578a996980ce2"]
  },
  "photos": [
    {
      "id": "011b8657457565a2",
      "name": "IMG_0005.jpg",
      "relativePath": "2024-春游/IMG_0005.jpg",
      "absolutePath": "",
      "pickCount": 3,
      "pickedInRounds": [1, 2, 3]
    }
  ]
}
```

- `photos` 包含**全部**照片（未入选的 `pickCount` 为 0），按 `pickCount` 降序、路径升序排序。
- `pickCount` = 被 pick 的总次数；`pickedInRounds` = 具体轮次；`lastRound` 供「导出保留照片」使用。
- 浏览器来源（`origin=browser`）不暴露绝对路径，`absolutePath` 为空串；
  命令行 `--dir` / 桌面端 `--dir` 的 server 来源会记录真实绝对路径。
- 文件为标准 UTF-8 JSON。

## 技术架构

```
浏览器 / 桌面窗口（WebView2）
   │  照片：本地 File → object URL（不上传）；API：HTTP 只发元数据与操作
   ▼
FastAPI 后端（Python）── 会话 / 轮次状态机 / pick 计数 / 结果 JSON（server 来源时额外提供图片）
   │
   └── 静态托管前端构建产物（Vite 打包的 HTML/JS/CSS）
```

桌面端（`desktop_webview/`）：进程内后台线程启动同一份 FastAPI 应用 → pywebview 创建 WebView2 窗口 →
加载 `http://127.0.0.1:<port>/?desktop=webview2`；退出时停止后端。原生「另存为 JSON / 打开路径」通过
pywebview JS API 提供。

```
E:\picturePick\
├── backend/                  # Python 后端（FastAPI + uvicorn + Pillow）
│   ├── picturepick/          # app / api / models / scanner / session / export / imaging / fsbrowse / cli
│   ├── tests/                # pytest（117 个用例）
│   └── pyproject.toml / requirements.txt
├── frontend/                 # TypeScript + Vite 前端（原生 DOM，无框架）
│   └── src/                  # main / api / library(本地图库) / export(一键导出) / viewer / ui/*
├── desktop_webview/          # 桌面版：pywebview(WebView2) + PyInstaller（推荐）
│   ├── picturepick_desktop/  # 桌面主流程与原生桥接（--selftest 自检模式）
│   └── picturepick-desktop.spec
├── desktop/                  # 可选历史方案：Electron 外壳（本机环境可能无法启动）
├── docs/                     # API 契约、架构约定、验证记录、截图
├── scripts/                  # build.ps1 / verify-desktop.ps1 / dev.ps1 / e2e 脚本 / 样例照片生成器
├── build.cmd / verify-desktop.cmd   # 双击即可打包 / 自检
├── build/                    # 打包中间产物（含 build/backend、build/webview）
└── release/                  # 桌面版产物
```

接口契约（前后端唯一准绳）见 [`docs/API.md`](docs/API.md)，工程约定见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，
验收记录见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)。

## 从源码构建桌面版

前置：Python 3.10+（含 pip）、Node.js 18+（npm）。首次需要安装依赖：

```powershell
python -m pip install -r backend\requirements.txt
python -m pip install -r desktop_webview\requirements.txt   # pywebview + pythonnet（WebView2）
python -m pip install pyinstaller
```

然后一条命令打包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# 或双击 build.cmd
```

分步命令：

```powershell
cd frontend
$env:npm_config_foreground_scripts='true'
npm install; npm run build          # → backend/picturepick/webui
cd ..
python -m PyInstaller --noconfirm --clean --distpath build/webview --workpath build/.pyinstaller-webview desktop_webview/picturepick-desktop.spec
# 产物：build/webview/PicturePick/PicturePick.exe
```

可选：Electron 版（历史方案；本机 Electron 无法启动时不要使用）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -WithElectron -WithInstaller
# → release/win-unpacked、release/PicturePick-1.0.0-win-x64-portable.exe、...-setup.exe
```

## 测试与验证

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 后端单元/接口 | `cd backend; python -m pytest -q` | 117 passed |
| API 端到端（server + browser 来源） | `python scripts/e2e_api.py --base-url ... --photos-dir samples\photos` | PASS=87 / FAIL=0 |
| 浏览器全流程 UI（Playwright/Chromium） | `node scripts/ui_e2e.cjs`（详见脚本头部说明） | 由当前环境限制分段验证；功能覆盖见 docs/VERIFICATION.md |
| 桌面版 | `powershell -File scripts\verify-desktop.ps1` | 运行时自检 + 窗口截图（本机运行） |

## 第三方开源库

均选用社区活跃、成熟广泛使用的项目：

- 后端：FastAPI、uvicorn、Pillow、pydantic、httpx（测试）、pytest
- 前端：TypeScript、Vite
- 桌面端：**pywebview + WebView2**（主方案）、PyInstaller；Electron + electron-builder（可选历史方案）
- 验证：Playwright（Chromium）

## 常见问题

- **选择文件夹后没有反应**：请用 Chrome/Edge 等 Chromium 内核浏览器打开 `http://127.0.0.1:8000/`；
  个别浏览器不支持 `webkitdirectory` 时会退化为多选图片文件。
- **导出保留照片时**：Chromium 会弹出系统「选择目标文件夹」框；若浏览器不支持
  File System Access API，会降级为逐张下载到浏览器默认下载目录。
- **页面刷新后无法继续浏览器会话**：照片文件只存在原页面内存中，刷新后请重新选择原文件夹；
  结果页导出时会提示重新选择原目录以匹配文件。
- **端口被占用**：`serve` 会打印清晰错误并退出，换 `--port` 即可；桌面端自动选择空闲端口。
- **JSON 保存在哪里**：浏览器为默认下载目录；桌面端会弹出「另存为」对话框。
- **照片不会上传**：所有数据都在本机处理，后端只监听 `127.0.0.1`。
