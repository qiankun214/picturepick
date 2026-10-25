# PicturePick 验证记录（Lead 集成验收）

环境：Windows 11 23H2 (10.0.22621) / Python 3.10.11 / Node v22.20.0 / npm 10.9.3 /
FastAPI 0.115.14 / uvicorn 0.34.3 / Pillow 9.3.0 / PyInstaller 6.22.3 /
Electron 33.4.11 / electron-builder 26.18.0 / Vite 5.4.21 / TypeScript 5.x。
工作目录：`E:\picturePick`。

## 1. 后端单元/接口测试（pytest）

```powershell
cd E:\picturePick\backend
python -m pytest -q
```

结果：**82 passed**（扫描过滤/排序、会话生命周期、分页、原图/缩略图、viewed/pick 幂等、
多轮状态机、finish 结果 JSON、下载/导出、静态/SPA、CORS、错误码、CLI 参数与端口占用）。

## 2. API 端到端（真实 HTTP + 真实 JPEG 样例）

```powershell
# Python 模块实例
python -m picturepick serve --port 8131 --no-open
python scripts/e2e_api.py --base-url http://127.0.0.1:8131 --photos-dir samples\photos

# PyInstaller 打包后的 sidecar 实例
build\backend\picturepick-backend.exe serve --port 8132 --no-open
python scripts/e2e_api.py --base-url http://127.0.0.1:8132 --photos-dir samples\photos
```

结果：两种实例均 **PASS=60 FAIL=0**；打包实例 `GET /` 返回 `200 text/html`（webui 已内嵌）；
停止后端口立即释放（无残留进程）。

## 3. 浏览器全流程 UI 验收（Playwright + Chromium，真实后端 + 构建产物）

脚本：`scripts/ui_e2e.cjs`（可用环境变量 `PP_BASE_URL` / `PP_PHOTOS_DIR` / `PP_CHROMIUM` /
`PP_PLAYWRIGHT_DIR` 覆盖；Lead 验收时使用 60 张样例照片）

覆盖并全部通过（**PASS=42 FAIL=0**）：
选择目录 → 选片视图/统计（60 张）→ 上一张/下一张 → 放大/缩小 → 自动放映推进 →
键盘 P 连续 pick 3 张（按钮高亮、计数递增）→ 取消 pick/恢复 → 结束本轮弹窗（保留 3 张）→
进入第 2 轮（3 张）→ 再 pick 2 张 → 生成结果 → 结果页（最终保留 2 张）→
下载 JSON 并校验（schemaVersion、totalPhotos=60、roundsPlayed=2、photos 全量、
pickedPhotoCount=3、pickCount 降序、pickedInRounds 正确、下载内容与 API 结果一致）→
再选一组回到启动页；无未捕获 JS 异常。

截图：`docs/screenshots/`（start / picker / picked / end-round-dialog / round2 / results）。
下载到的真实结果文件：`.tmp/verify/result-downloaded.json`。

## 4. 命令行验收

| 命令 | 结果 |
| --- | --- |
| `python -m picturepick version` | `1.0.0` |
| `python -m picturepick scan --dir samples\photos` | 递归扫描 60 张（`--no-recursive` 时 12 张） |
| `python -m picturepick serve --dir samples\photos --port 8133 --no-open` | `/api/sessions/current` 直接返回 60 张会话，`GET /` 200 text/html |
| 端口占用时再次 `serve --port 8133` | 打印 `Error: port 8133 is already in use` 且退出码 1 |

## 5. 桌面端打包验收

| 检查 | 结果 |
| --- | --- |
| `desktop npm run build`（tsc） | 0 error：`dist/main.js`、`dist/preload.js` |
| `npm run pack:backend` | `build/backend/picturepick-backend.exe`（5.6 MB）+ `_internal/webui` |
| 打包后 sidecar | `version` 输出 1.0.0；`/api/health` ok；`GET /` 200 text/html；kill 后端口释放 |
| `release/win-unpacked/PicturePick.exe` | 文件版本 1.0.0；`resources/app.asar` 含 package.json + dist/main.js + dist/preload.js；`resources/backend/**` 含 sidecar 与 webui（`index-7fCHypgd.js`） |
| `release/PicturePick-1.0.0-win-x64-portable.exe` | 99.4 MB（单文件便携版） |
| `release/PicturePick-1.0.0-win-x64-setup.exe` | 111.7 MB（NSIS 安装包，可选安装目录） |
| `release/PicturePick-1.0.0-win-x64-portable.zip` | 146.4 MB（绿色版目录压缩包，兜底分发） |
| 7za 校验安装包 | 两个安装包均为有效归档，条目数 1846，含 app.asar / resources\backend / webui |

> 上述产物在最终代码（含 CLI `scan` 默认递归修正）后已整体重建，并对重建后的
> `build/backend/picturepick-backend.exe` 重跑 e2e：**PASS=60 FAIL=0**，`GET /` 返回内嵌前端。

**窗口启动验证（需在普通桌面会话执行，AI 沙箱内 Chromium 无法创建命名管道）**：

```powershell
powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-shell.ps1 -Mode app
# 通过：desktop\out\verify-app.png（窗口截图）+ 退出后端口释放检查
# 或直接双击 release\win-unpacked\PicturePick.exe
```

沙箱内 `electron.exe` 启动即崩溃（EXCEPTION_BREAKPOINT / platform_channel 拒绝访问，
`electron.exe --version` 亦复现），已在 Electron 28/31/33/44 多版本验证为环境限制，
与项目代码无关；同一份前端已由第 3 项 Playwright 在真实 Chromium 中全流程验证。

## 6. 复核命令集合

```powershell
# 后端
cd E:\picturePick\backend; python -m pytest -q

# 前端类型检查与构建
cd E:\picturePick\frontend; npx tsc --noEmit; npm run build

# 一键构建（前端 → 后端 sidecar → 桌面绿色版）
powershell -ExecutionPolicy Bypass -File E:\picturePick\scripts\build.ps1
```
