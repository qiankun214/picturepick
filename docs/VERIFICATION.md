# PicturePick 验证记录（第 2 版）

环境：Windows 11 23H2 (10.0.22621) / Python 3.10.11 / Node v22.20.0 / npm 10.9.3 /
FastAPI 0.115.14 / uvicorn 0.34.3 / Pillow 9.3.0 / PyInstaller 6.22.3 /
pywebview 6.2.1 / pythonnet 3.1.0 / Vite 5.4.21 / TypeScript 5.x。
工作目录：`E:\picturePick`。

## 1. 后端单元/接口测试（pytest）

```powershell
cd E:\picturePick\backend
python -m pytest -q
```

结果：**117 passed**（原 82 个 v1 用例全部保留 + 新增 35 个 browser 来源会话用例）。
覆盖：扫描过滤/排序、server 与 browser 双来源会话创建与校验（空/绝对路径/`..`/非 jpg/去重/上限）、
分页、原图/缩略图、viewed/pick 幂等、多轮状态机、finish 结果 JSON（origin / sourceDirectory /
absolutePath / lastRound）、下载/导出、browser 图片接口 404、静态/SPA、CORS、错误码、CLI。

## 2. API 端到端（真实 HTTP + 真实 JPEG 样例）

```powershell
cd E:\picturePick\backend
python -m picturepick serve --port 8140 --no-open
# 另开终端
python scripts/e2e_api.py --base-url http://127.0.0.1:8140 --photos-dir samples\photos
```

结果：**PASS=87 / FAIL=0**。包含：
- server 来源完整流程（创建→分页→图片→pick/unpick→三轮→finish→下载/导出）；
- **browser 来源**（元数据创建 60 张 → 校验 origin/sourceLabel/libraryTotal → `/image` 404 →
  pick 3 张 → 第 2 轮 → 再 pick 2 张 → finish → `lastRound.index=2/pickedCount=2/pickedPhotoIds` →
  `absolutePath=""`、`sourceDirectory=label`、pickCount/pickedInRounds 正确）；
- 非法输入：空列表 / 绝对路径 / `..` 越界 / 非 jpg 扩展名 / 重复 relativePath 去重；
- `GET /` 返回 `200 text/html`（内嵌 v2 前端）。

## 3. 浏览器 UI 验收（第 2 版功能）

自动化脚本：`scripts/ui_e2e.cjs`（Playwright；`PP_BASE_URL` / `PP_PHOTOS_DIR` / `PP_CHROMIUM` 可覆盖），
覆盖：原生文件夹导入（`setInputFiles(目录)`）→ blob URL 显示（0 次 `/image` 请求）→ 翻页/缩放/自动放映 →
pick/取消 → 多轮 → finish → 下载 JSON（origin=browser、lastRound）→ mock 目录句柄一键导出 →
再选一组；完整脚本已提交，可在普通桌面环境一条命令跑完。

本机受限沙箱的 Chromium 只能单进程启动且单页存活约 4 秒（超时 fail-fast，无 JS 异常），
因此实际取证采用「短流程 + 分段」方式完成，结果：
- 结果页 + 一键导出 + JSON 下载：**31/31 通过**（mock 目录句柄收到 2 个 jpg、大小正确、
  进度提示「已导出 2 张照片到…」）；
- 选片/多轮/60 张规模导入/server 来源回归：分段通过；
- 截图：`docs/screenshots/`（start / picker / end-round-dialog / round2 / results / export-photos）。

## 4. 命令行验收

| 命令 | 结果 |
| --- | --- |
| `python -m picturepick version` | `1.0.0` |
| `python -m picturepick scan --dir samples\photos` | 递归 60 张（`--no-recursive` 12 张） |
| `python -m picturepick serve --dir samples\photos --port 8140 --no-open` | `/api/sessions/current` 返回 60 张 server 会话，`GET /` 200 text/html |
| 端口占用时重复 `serve` | 打印 `Error: port ... is already in use` 且 exit 1 |

## 5. 桌面端（WebView2）打包与自检

安装依赖（首次）：

```powershell
python -m pip install -r backend\requirements.txt
python -m pip install -r desktop_webview\requirements.txt
python -m pip install pyinstaller
```

一键构建：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# 或双击 build.cmd
```

产物：

| 路径 | 说明 |
| --- | --- |
| `release/PicturePick-WebView2/PicturePick.exe` | 绿色版目录（双击即用） |
| `release/PicturePick-WebView2-win-x64.zip` | 目录压缩包 |

Lead 侧已验证：`scripts/build.ps1` 全流程可跑通（前端构建 → PyInstaller onedir →
release 组装 + zip），PyInstaller 构建成功；PowerShell 5.1 解析/执行通过（脚本为 UTF-8 BOM）。

包体与内容（最终精简版）：

| 项目 | 结果 |
| --- | --- |
| `release/PicturePick-WebView2/PicturePick.exe` | 5.5 MB，文件版本 1.0.0（ProductName/FileDescription 已写入） |
| `release/PicturePick-WebView2/`（整个目录） | **33.2 MB**（已排除 pywebview 无关的 PySide6/Qt/GTK 后端，原 520MB → 33MB） |
| `release/PicturePick-WebView2-win-x64.zip` | **17.2 MB** |
| 内置运行时文件 | `pythonnet/runtime/Python.Runtime.dll`、`webview/lib/Microsoft.Web.WebView2.Core.dll`、`webview/lib/runtimes/win-x64/native/WebView2Loader.dll`、`_internal/webui`（v2 前端）均存在 |
| 冻结后后端自检 | `PicturePick.exe --selftest-backend`（不开窗口）→ **SELFTEST RESULT: PASS**：内嵌前端存在、`/api/health` ok、`GET /` 由后端提供 755 字符 HTML（证明冻结后的 FastAPI/pydantic/uvicorn/静态服务正常） |
| 旧 Electron 产物 | 已移到 `release/_legacy-electron/`（本机 Electron 无法启动，避免误用） |

**窗口最终验证（需在安装有 WebView2 运行时的普通桌面会话执行，AI 沙箱内 Chromium/pythonnet 受限）**：

```powershell
# 双击 verify-desktop.cmd，或：
powershell -ExecutionPolicy Bypass -File scripts\verify-desktop.ps1
# 步骤 1：--selftest 自检（后端/内置前端/pythonnet/WebView2 桥接）
# 步骤 2：启动真实窗口 → 自动截图（.tmp/desktop-verify/verify.png）→ 退出
# 输出 PASS/FAIL、页面断言、日志路径；失败时日志在 .tmp/desktop-verify/logs/desktop.log
```

排查启动崩溃可用带控制台版本：

```powershell
powershell -File scripts\build.ps1 -Console
build\webview-console\PicturePickConsole\PicturePick.exe --selftest
build\webview-console\PicturePickConsole\PicturePick.exe --dir samples\photos
```

也可单独跑「不加载 GUI」的冻结后端自检（可在无桌面会话的环境执行）：

```powershell
release\PicturePick-WebView2\PicturePick.exe --selftest-backend
# 结果写入 %LOCALAPPDATA%\PicturePick\logs\desktop.log 与 selftest.txt
```

> 说明：早期 Electron 方案在本机遇 Chromium 启动即崩溃（`platform_channel`/`0xc0000005`，
> Electron 28/31/33/44 复现，与项目代码无关），因此桌面主方案改为 WebView2；
> Electron 代码仍保留在 `desktop/`，仅作可选历史方案。

## 6. 复核命令集合

```powershell
# 后端
cd E:\picturePick\backend; python -m pytest -q

# 前端类型检查与构建
cd E:\picturePick\frontend; npx tsc --noEmit; npm run build

# API 端到端
cd E:\picturePick\backend; python -m picturepick serve --port 8140 --no-open
cd E:\picturePick; python scripts\e2e_api.py --base-url http://127.0.0.1:8140 --photos-dir samples\photos

# 桌面版打包 + 自检
powershell -ExecutionPolicy Bypass -File E:\picturePick\scripts\build.ps1
powershell -ExecutionPolicy Bypass -File E:\picturePick\scripts\verify-desktop.ps1
```
