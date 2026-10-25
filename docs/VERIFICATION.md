# PicturePick 验证记录（第 4 版）

环境：Windows 11 23H2 (10.0.22621) / Python 3.10.11 / Node v22.20.0 / npm 10.9.3 /
FastAPI 0.115.14 / uvicorn 0.34.3 / Pillow 9.3.0 / PyInstaller 6.22.3 /
pywebview 6.2.1 / pythonnet 3.1.0 / Vite 5.4.21 / TypeScript 5.x。
工作目录：`E:\picturePick`。

## 1. 后端单元/接口测试（pytest）

```powershell
cd E:\picturePick\backend
python -m pytest -q
```

结果：**170 passed**（原 131 个 v3 用例全部保留 + 新增 39 个 restore/resume 用例）。
覆盖：扫描过滤/排序、server 与 browser 双来源会话创建与校验、分页、原图/缩略图、viewed/pick 幂等、
多轮状态机、finish 结果 JSON（origin / sourceDirectory / absolutePath / lastRound）、下载/导出、
export-photos 后端复制（同名 `-1` 不覆盖、目标目录校验、NO_PICKED_PHOTOS、INVALID_SOURCE、逐字节一致、失败回滚）、
**restore 恢复现场**（schema/origin/目录/照片结构校验、轮次重建、增删文件容错、lastRound 映射）、
**resume 继续筛选**（阶段/数量校验、历史计数累加）、静态/SPA、CORS、错误码、CLI。

## 2. API 端到端（真实 HTTP + 真实 JPEG 样例）

```powershell
cd E:\picturePick\backend
python -m picturepick serve --port 8140 --no-open
# 另开终端
python scripts/e2e_api.py --base-url http://127.0.0.1:8140 --photos-dir samples\photos
```

结果：**PASS=124 / FAIL=0**。包含：
- server 来源完整流程（创建→分页→图片→pick/unpick→三轮→finish→下载/导出）；
- **export-photos 后端复制**：最后一轮保留照片复制到目标目录、同名自动 `-1` 不覆盖、
  目标不存在/是文件 → `INVALID_PATH`、无 pick → `NO_PICKED_PHOTOS`、browser 来源 → `INVALID_SOURCE`；
- **restore 恢复现场**：保存结果 JSON → `POST /api/sessions/restore` 201、`restored=true`、
  phase/roundIndex/libraryTotal/来源目录正确、恢复结果 pickCount/lastRound/absolutePath 与保存 JSON 一致、
  恢复后可 export-photos；错误码 `UNSUPPORTED_SCHEMA`/`INVALID_SOURCE`/`INVALID_PATH`/`INVALID_RESTORE`；
- **resume 继续筛选**：恢复会话 → resume 200 → phase=picking、roundIndex+1、新一轮照片=原 lastRound、
  历史 pickCount 累加；`PHASE_PICKING` / `NOT_ENOUGH_PICKS` 错误分支；
- **browser 来源兼容接口**（元数据创建→分页→`/image` 404→多轮→finish→lastRound）；
- 非法输入：空列表 / 绝对路径 / `..` 越界 / 非 jpg / 重复 relativePath 去重；
- `GET /` 返回 `200 text/html`（内嵌 v4 前端）。

## 3. 浏览器 UI 验收（第 4 版：只取目录路径 + 后端供图 + 后端复制导出 + 恢复现场）

自动化脚本：`scripts/ui_e2e.cjs`（Playwright；`PP_BASE_URL` / `PP_PHOTOS_DIR` / `PP_CHROMIUM` 可覆盖）。
测试通过 `window.__picturepickTestSourceDir` / `__picturepickTestExportDir` 注入绝对路径，无需真实对话框。
Lead 在本机真实 Chromium 中完整跑通：**PASS=71 / FAIL=0**，覆盖：

- 启动页只有「选择照片文件夹」，**不存在**自定义目录浏览、路径输入、`webkitdirectory` 文件导入框；
- 注入目录路径 → 后端扫描 60 张 → 选片视图；大图/胶片条全部走
  `/api/sessions/{sid}/photos/{id}/image`（断言请求命中后端、页面**不存在 blob: 图片**）；
- 上一张/下一张、放大/缩小、自动放映、键盘 P pick/取消、统计计数；
- 结束本轮弹窗 → 进入第 2 轮 → 再 pick → finish → 结果页；
- 下载 JSON 校验：`origin=server`、60 张全量、pickCount 降序、`pickedInRounds`、
  `absolutePath` 为绝对路径、`lastRound` 正确、下载内容与 API 一致；
- **一键导出**：注入目标目录 → 后端复制 → 磁盘出现 2 个 jpg，文件名等于最后一轮保留照片，
  **内容与原图逐字节一致**，页面显示「已导出 N 张到 …」；
- 结果 JSON 与页面均未经过浏览器下载照片；导出由本机后端完成；
- **载入 JSON 恢复现场**：回到启动页 → 用 `page.setInputFiles('[data-testid="restore-input"]', jsonPath)`
  选择刚才下载的结果 JSON → 进入结果页，显示恢复提示「已从 JSON 恢复现场：共 2 轮、最终保留 3 张 · 来源 …」，
  会话 `restored=true/phase=finished`，与 JSON 数据一致；恢复后可再次导出且内容逐字节一致；
- **继续筛选**：结果页「继续筛选」→ resume → 进入第 3 轮选片，总数=最后一轮保留数（2），
  会话回到 `picking`，图片仍由后端 `/image` 提供；
- 截图：`docs/screenshots/`（start / picker / picked / end-round-dialog / round2 / results /
  export-photos / restored-results / resumed-picker）。

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
| 内置运行时文件 | `pythonnet/runtime/Python.Runtime.dll`、`webview/lib/Microsoft.Web.WebView2.Core.dll`、`webview/lib/runtimes/win-x64/native/WebView2Loader.dll`、`_internal/webui`（v4 前端）均存在 |
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
>
> **当前桌面版已知问题（待修复）**：冻结后的 exe 在加载 pythonnet 时，默认走 .NET Framework(netfx) 路径，
> 报 `Failed to resolve Python.Runtime.Loader.Initialize`；改用 `PYTHONNET_RUNTIME=coreclr` 后
> pythonnet 可加载，但 pywebview 需要 `System.Windows.Forms`，必须使用
> `Microsoft.WindowsDesktop.App` 的 runtimeconfig（本机已确认安装了 .NET 8/9/10 Desktop Runtime）。
> 修复方案：桌面入口在 `import webview` 前用 `pythonnet.set_runtime("coreclr", runtime_config=...)`
> 指定 WindowsDesktop 运行时；该修复尚未打包验证（受沙箱 GUI 限制）。**修复前请使用命令行+浏览器方式。**

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
