# PicturePick 验证记录（第 5.2 版）

环境：Windows 11 23H2 (10.0.22621) / Python 3.10.11 / Node v22.20.0 / npm 10.9.3 /
FastAPI 0.115.14 / uvicorn 0.34.3 / Pillow 9.3.0 / PyInstaller 6.22.3 /
pywebview 6.2.1 / pythonnet 3.1.0 / Vite 5.4.21 / TypeScript 5.x。
工作目录：`E:\picturePick`。

## 0. 第 5.2 版验收汇总（AI 识鸟：参考照片由模型 API 提供 + 模态框对照/X 修复）

| 项目 | 结果 |
| --- | --- |
| 后端单元/接口（pytest） | **285 passed**（v4 既有 179 全过 + 新增 106 个 AI 识鸟用例，含端点协议/模型参考图/ToolNotOpen 降级/SSRF） |
| API 端到端 `scripts/e2e_api.py` | **PASS=238 / FAIL=0**（v4 既有 133 全过 + 新增 105 项 AI 识鸟检查） |
| 浏览器 UI `scripts/ui_e2e.cjs` | **PASS=115 / FAIL=0**（v4 既有 71 项全过 + AI 识鸟章节新增 44 项；日志 `.tmp/verify/ui-e2e-result.txt` 末尾 `ALL_PASS`） |
| 前端构建 | `npx tsc --noEmit` + `npm run build` 通过（v5.2 前端构建，产物在 `backend/picturepick/webui`） |
| 截图 | `bird-queue.png` / `bird-result.png` / `bird-info.png` / `bird-results.png` 已重新生成（模拟模式，1440×900），见 §7.4 |
| 真实 Key / 真实网络 | **双协议端点直连** + **模型 API 联网返回真实参考图** 已实测（插件已开通；真实参考图下载 32KB/2.09s，见 §7.7） |
| 真实 UI 全流程（非 mock） | **PASS=15 / FAIL=0**（真实方舟 Key + 真实网络：识鸟→AI 参考图→X 关闭→采纳→左栏/结果页/JSON，见 §7.7） |
| 桌面打包 | v5.2 未重新执行完整打包；静态 import 分析确认 `picturepick.birdid` 由 PyInstaller 自动收集，**spec 无需修改**（§7.6） |

> AI 识鸟的 pytest / API e2e 均使用后端**模拟模式**（`PICTUREPICK_BIRD_MOCK=1`），
> 不访问火山方舟、不消耗额度；真实 Key（已开通联网搜索插件）的端点/参考图/真实 UI 验证见 §7.7。
> 全部 v1–v4.1 功能与数字保持不回归（scan/session/pick/rounds/finish/export/restore/resume/静态/CORS/CLI）。

## 1. 后端单元/接口测试（pytest）

```powershell
cd E:\picturePick\backend
python -m pytest -q
```

结果：**285 passed**（v4 的 179 个用例全部保留/按新字段调整后通过 + 新增 106 个 AI 识鸟用例，
分布在 `tests/test_bird_config.py`、`test_bird_tasks.py`、`test_bird_exif.py`、`test_bird_parser.py`、
`test_bird_client.py`、`test_bird_reference.py`、`test_bird_result.py`）。

覆盖（v4 既有）：扫描过滤/排序、server 与 browser 双来源会话创建与校验、分页、原图/缩略图、
viewed/pick 幂等、多轮状态机、finish 结果 JSON（origin / sourceDirectory / absolutePath / lastRound）、
下载/导出、export-photos 后端复制、restore 恢复现场（含**旧版 JSON 无 origin/lastRound 兼容**、
**源目录不可用时 absolutePath 回退**、增删文件容错）、resume 继续筛选、静态/SPA、CORS、错误码、CLI。

覆盖（v5 新增 AI 识鸟）：

- **配置 / 端点**：环境变量 / `settings.json` 优先级与覆盖（`PICTUREPICK_ARK_API_KEY` / `ARK_API_KEY` /
  `PICTUREPICK_ARK_ENDPOINT`（兼容旧 `PICTUREPICK_ARK_BASE_URL`）/ `PICTUREPICK_ARK_MODEL` /
  `PICTUREPICK_BIRD_MOCK`，含**显式 `PICTUREPICK_BIRD_MOCK=0` 覆盖配置文件 `mock:true`**）、
  完整端点校验（`chat` / `responses` 协议识别、双后缀/只填 Base URL 判定无效、`endpointWarning`）、
  旧字段 `arkBaseUrl` 兼容读取与保存迁移、Key 掩码与 `keySource`、
  `PUT /api/bird/config` 的缺省/清除/校验（`INVALID_CONFIG`：非受支持端点、`mock` 非布尔值）、
  配置文件缺失/损坏不阻塞、`POST /api/bird/verify` 成功/失败/端点无效/模拟模式。
- **任务队列**：提交返回 queued 元数据、同一照片 active 任务复用、队列顺序与 `queuePosition`、
  counters、进度 stage/百分比（单调、identifying 按时间插值）、completed 任务含 result/referenceUrl、
  dismiss 幂等（含排队中任务，结果丢弃）、`BIRD_TASK_ADOPTED` 冲突、finished 会话拒绝新任务
  （`PHASE_FINISHED`）、browser 会话拒绝（`INVALID_SOURCE`）、任务不存在/会话不匹配 404、
  worker 失败（`BIRD_API_ERROR` / `BIRD_PARSE_ERROR`）与 `notbird` 无鸟结果不可采纳。
- **采纳与结果**：adopt 写 EXIF（Pillow 读回 `ImageDescription`/XP* 标签）+ 写会话
  （`PhotoItem.bird`、`birdIdentifiedCount`），结果 JSON 的 `photos[].bird` 与 `birdIdentifiedCount`，
  重复 adopt 幂等覆盖，finished 后 adopt 使结果缓存失效，未完成/已忽略/失败任务不能采纳，
  `BIRD_RESULT_UNAVAILABLE`。
- **恢复**：带 `bird` 的结果 JSON restore 后鸟种与计数立即生效；旧 JSON（无 `bird`/`birdIdentifiedCount`）
  恢复为 `null`/0；`bird` 结构非法时宽容忽略、不影响恢复；resume 后鸟种保留。
- **EXIF 写入**：无损（图像数据字节完全不变、仅替换 APP1）、保留已有 EXIF 标签（含 GPS/MakerNote 等）、
  5 个标签值正确（ASCII/UTF-16LE）、mtime 恢复、无 EXIF 照片可新增 EXIF、非 JPEG/不存在/只读/EXIF 过大
  等失败路径返回 `written=false` + 中文 error 且不抛异常。
- **模型调用与解析**：Chat Completions 与 Responses API 两种请求体/响应解析（`choices[0].message.content`
  vs `output[].content[].text`）、Responses API 自动带 `tools:[{"type":"web_search"}]`、
  **`ToolNotOpen`（联网搜索插件未开通）自动去掉 tools 重试一次**、`thinking` 参数被 400 拒绝时自动去掉
  重试一次、超时/429/5xx 重试一次、401/403/404 不重试且错误信息带请求地址、端点无效时任务拒绝/verify 失败、
  响应结构异常、Markdown 围栏/前后说明容错解析、字段清洗与 `confidence` 夹取、`identified=false` 语义、
  mock 客户端确定性结果/延迟/失败。
- **参考照片（模型提供 URL + SSRF）**：仅使用模型返回的 `referenceImageUrl`（后端不再搜索必应/维基等图源）、
  真实图片 URL 下载/缩放/仅存内存、无 URL 或下载失败不影响任务（`referenceAvailable=false`）、
  参考图按鸟种内存缓存；**SSRF 防护**（拒绝 localhost/内网/非 http(s)/带 userinfo 的 URL）；
  插件未开通时 `result.referenceNote` 写入开通提示；`BIRD_REFERENCE_UNAVAILABLE`。

## 2. API 端到端（真实 HTTP + 真实 JPEG 样例 + mock 识别）

```powershell
# 终端 1：起后端（模拟模式，避免真实方舟调用与额度消耗）
cd E:\picturePick\backend
$env:PYTHONIOENCODING = 'utf-8'
$env:PICTUREPICK_BIRD_MOCK = '1'
$env:PICTUREPICK_BIRD_MOCK_DELAY = '0.25'
$env:PICTUREPICK_CONFIG_DIR = 'E:\picturePick\.scratch\e2e-config'
python -m picturepick serve --port 8126 --no-open

# 终端 2：跑 e2e（真实 HTTP + samples\photos 样例照片）
cd E:\picturePick
$env:PYTHONIOENCODING = 'utf-8'
python scripts\e2e_api.py --base-url http://127.0.0.1:8126 --photos-dir samples\photos
```

结果：**PASS=238 / FAIL=0**。包含：

- v4 既有 133 项全过：server 来源完整选片流程、分页、图片、pick/unpick、三轮、finish、
  下载/导出、**export-photos 后端复制**（同名自动 `-1`、目标非法 `INVALID_PATH`、无 pick
  `NO_PICKED_PHOTOS`、browser `INVALID_SOURCE`）、**restore 恢复现场**（含旧版 JSON 兼容、
  absolutePath 回退、错误码 `UNSUPPORTED_SCHEMA`/`INVALID_SOURCE`/`INVALID_PATH`/`INVALID_RESTORE`）、
  **resume 继续筛选**、browser 兼容接口、非法输入、静态资源；
- **v5/v5.1/v5.2 新增 105 项 AI 识鸟检查**（mock 模式；v5.2 参考图为 mock 内存图，不依赖联网搜索插件）：
  - 配置/端点：`GET /api/bird/status`（configured/model/**endpoint/endpointProtocol/endpointWarning**/
    `keyMasked`/`keySource="mock"`/queue）、`PUT /api/bird/config`（保存/清除/`endpoint` 完整端点校验
    `/chat/completions` 与 `/responses`、非法端点 `INVALID_CONFIG`、旧 `arkBaseUrl` 迁移、明文 Key 不泄露）、
    `POST /api/bird/verify`（模拟模式 ok=true）；
  - 任务：`POST .../bird-tasks` 201 + queued 元数据、重复提交复用、`GET .../bird-tasks` 列表与
    `queue` 计数、`GET .../bird-tasks/{tid}` 单个任务、进度/stage 推进、`elapsedMs`、
    完成后 result（科/属/种/拉丁名/confidence/note/alternatives）与 `referenceUrl`；
  - 参考图：`GET .../reference` 200 `image/jpeg` + `Cache-Control: no-store`，未完成/无图 →
    404 `BIRD_REFERENCE_UNAVAILABLE`；
  - 采纳：`POST .../adopt` 写结果 JSON（`photos[].bird` / `birdIdentifiedCount`）与 EXIF
    （Pillow 读回标签、**原图图像数据字节不变**）、重复采纳幂等、未完成/失败/无鸟/已忽略任务错误分支、
    `BIRD_TASK_NOT_FOUND` / `BIRD_TASK_NOT_COMPLETED` / `BIRD_RESULT_UNAVAILABLE`；
  - 忽略：dismiss 幂等、已采纳 `BIRD_TASK_ADOPTED`；
  - 失败路径：`PICTUREPICK_BIRD_MOCK_FAIL=1` 任务 `BIRD_API_ERROR`；`notbird` 照片 `identified=false`；
  - 结果/恢复：finish 结果 JSON 含鸟种、下载内容一致、restore 保留 `bird` 与计数、
    旧 JSON 兼容、finished 会话拒绝新任务 `PHASE_FINISHED`、browser 会话 `INVALID_SOURCE`；
  - 静态：`GET /` 返回 v5 前端（含 AI 识鸟界面）。

## 3. 浏览器 UI 验收（第 5.2 版：三栏 + AI 识鸟 + 原照片/参考照片对照 + 模态框 X）

自动化脚本：`scripts/ui_e2e.cjs`（Playwright；`PP_BASE_URL` / `PP_PHOTOS_DIR` / `PP_CHROMIUM` 可覆盖），
后端以 `PICTUREPICK_BIRD_MOCK=1` 启动（前端不伪造识别结果，e2e 使用真实接口 + 模拟模型）。

结果：**PASS=115 / FAIL=0**（Lead 在普通桌面会话用 Playwright Chromium 跑通；v4 既有 71 项全过，
AI 识鸟章节新增 44 项全部通过；日志 `.tmp/verify/ui-e2e-result.txt` 末尾 `ALL_PASS`）。
> 说明：本机 AI 沙箱内 Chromium 启动受限（`platform_channel` 拒绝访问 / 锁文件在临时目录），
> 通过把 `TMP`/`TEMP` 指到工作区目录并在完整权限会话运行后通过；复现命令见 §6。

v4 既有覆盖（保留）：启动页只有「选择照片文件夹」、不存在自定义目录浏览/`webkitdirectory` 导入；
注入目录路径 → 后端扫描 → 选片视图；大图/胶片条全部走 `/api/.../image`（页面**不存在 blob: 图片**）；
上一张/下一张、放大/缩小、自动放映、键盘 P pick/取消、统计计数；结束本轮 → 第 2 轮 → finish → 结果页；
下载 JSON 校验（origin/全量/排序/pickedInRounds/absolutePath/lastRound）；一键导出（注入目标目录 →
后端复制 → 磁盘文件名与内容逐字节一致）；载入 JSON 恢复现场（restore-notice、restored=true）；
继续筛选（resume → 第 3 轮、总数=最后一轮保留数）。

v5 新增覆盖（AI 识鸟章节，合计新增 44 项检查；v4 71 项 + 44 = 最终 **PASS=115**）：

- 选片页三栏：左 `photo-info`（含 `bird-info-empty`）、中 stage、右 `bird-queue`；窗口变窄左右栏可折叠；
- 顶部 `bird-identify-button` 存在且带 `B` 提示；设置入口 `bird-settings-button` 可打开
  `bird-settings-modal`（API Key 输入、**API 端点（完整 URL）** `bird-endpoint-input` 与两种端点示例、
  模型名、状态行显示接口协议/端点警告、保存/测试连接按钮）；
- 快捷键 `B` 对当前照片提交任务 → toast「已提交 AI 识鸟任务」→ 右栏出现
  `bird-task-card`（排队中/识别中 → 待确认）与进度；再次按 `B`（同一照片）提示已在队列中；
- 提交后仍可继续切换照片/继续提交（不阻塞选片）；
- 点击任务卡片打开 `bird-result-modal`：顶部**原照片** `bird-original-image`（`src` 走
  `/photos/{id}/image?variant=original`）与**参考照片（AI 提供）** `bird-reference-image` 并排；
  下方 `bird-result-species` / `bird-result-family` / `bird-result-genus` / `bird-result-confidence`；
- **所有模态框右上角 X（`modal-close`）可点击关闭**（进度/失败/结果/设置弹窗）；
  失败/进度模态框也显示原照片；
- 点「采纳并写入 EXIF」→ toast「已采纳：…（结果 JSON 已记录，照片 EXIF 已写入）」；
  左侧 `bird-info` 显示 `bird-info-species` 科/属/置信度/`已写入 EXIF`；
  切到其他照片再切回，左栏鸟种仍显示（再次浏览无需重新识别）；
- 结果页 `bird-identified-count` 显示「AI 识鸟：N 张已记录鸟种」，结果行出现 `result-bird-pill`；
- 载入带 `bird` 的结果 JSON 恢复现场后，左栏鸟种与结果页计数立即生效；
- 失败路径（`PICTUREPICK_BIRD_MOCK_FAIL=1` 或 `notbird`）：失败卡片/`bird-result-error`/
  「重新识别」按钮可见；
- 既有 71 项无回归（选片/多轮/JSON/导出/恢复/继续筛选/快捷键）。

截图（UI e2e 已生成到 `docs/screenshots/`，1440×900，见 §7.4）：`bird-queue.png`、`bird-result.png`、
`bird-info.png`、`bird-results.png`。

> **真实 Key UI 全流程（非 mock）**：另有真实方舟 Key + 真实网络的 UI 验证 **PASS=15 / FAIL=0**
> （参考图联网检索成功、X 关闭、采纳与结果 JSON 校验；详见 §7.7；证据截图在 `.tmp/real-ui/`，未入 docs）。

## 4. 命令行验收

| 命令 | 结果 |
| --- | --- |
| `python -m picturepick version` | `1.0.0` |
| `python -m picturepick scan --dir samples\photos` | 递归 60 张（`--no-recursive` 12 张） |
| `python -m picturepick serve --dir samples\photos --port 8140 --no-open` | `/api/sessions/current` 返回 60 张 server 会话，`GET /` 200 text/html |
| 端口占用时重复 `serve` | 打印 `Error: port ... is already in use` 且 exit 1 |
| **v5** `PICTUREPICK_BIRD_MOCK=1 python -m picturepick serve --port 8126 --no-open` | `/api/bird/status` 返回 `configured=true`、`mock=true`；`POST /api/bird/verify` → ok=true（模拟模式） |
| **v5.1** `PICTUREPICK_ARK_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3/responses python -m picturepick serve` | `GET /api/bird/status` 的 `endpoint` 为该完整 URL、`endpointProtocol="responses"`；换成 `/chat/completions` → `"chat"`；只填 Base URL → `endpointProtocol=null` + `endpointWarning` |
| **v5** `PICTUREPICK_CONFIG_DIR=... python -m picturepick serve` | 配置读写只落在指定目录（测试隔离 / 绿色便携），不影响真实 `%APPDATA%\PicturePick` |

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

产物（v4.1 结论保留）：

| 路径 | 说明 |
| --- | --- |
| `release/PicturePick-WebView2/PicturePick.exe` | 绿色版目录（双击即用） |
| `release/PicturePick-WebView2-win-x64.zip` | 目录压缩包 |

v4.1 已验证：`scripts/build.ps1` 全流程可跑通（前端构建 → PyInstaller onedir → release 组装 + zip），
包体 33.2MB / zip 17.2MB，`--selftest-backend` 通过。**v5.2 未重新执行完整打包**（按交付约束不跑耗时打包）；
打包影响评估见 §7.6：`picturepick.birdid` 由静态 import 自动收集，无需新增 hiddenimports/datas，
桌面端新增依赖为零（仍只有 stdlib + Pillow），运行时行为与命令行版一致。

**窗口最终验证（需在安装有 WebView2 运行时的普通桌面会话执行，AI 沙箱内 Chromium/pythonnet 受限）**：

```powershell
# 双击 verify-desktop.cmd，或：
powershell -ExecutionPolicy Bypass -File scripts\verify-desktop.ps1
# 步骤 1：--selftest 自检（后端/内置前端/pythonnet/WebView2 桥接）
# 步骤 2：启动真实窗口 → 自动截图（.tmp/desktop-verify/verify.png）→ 退出
# 输出 PASS/FAIL、页面断言、日志路径；失败时日志在 .tmp/desktop-verify/logs/desktop.log
```

> 说明：早期 Electron 方案在本机遇 Chromium 启动即崩溃（`platform_channel`/`0xc0000005`，
> Electron 28/31/33/44 复现，与项目代码无关），因此桌面主方案改为 WebView2。
>
> **当前桌面版已知问题（待修复，与 v5 无关）**：冻结后的 exe 在加载 pythonnet 时，默认走
> .NET Framework(netfx) 路径，报 `Failed to resolve Python.Runtime.Loader.Initialize`；
> 改用 `PYTHONNET_RUNTIME=coreclr` 后 pythonnet 可加载，但 pywebview 需要 `System.Windows.Forms`，
> 必须使用 `Microsoft.WindowsDesktop.App` 的 runtimeconfig。修复前请使用命令行+浏览器方式。

## 6. 复核命令集合

```powershell
# 后端（pytest）
cd E:\picturePick\backend; python -m pytest -q

# 前端类型检查与构建
cd E:\picturePick\frontend; npx tsc --noEmit; npm run build

# API 端到端（v5：mock 识别，避免真实方舟调用）
cd E:\picturePick\backend
$env:PYTHONIOENCODING = 'utf-8'; $env:PICTUREPICK_BIRD_MOCK = '1'
$env:PICTUREPICK_BIRD_MOCK_DELAY = '0.25'; $env:PICTUREPICK_CONFIG_DIR = 'E:\picturePick\.scratch\e2e-config'
python -m picturepick serve --port 8126 --no-open
# 另开终端：
cd E:\picturePick; $env:PYTHONIOENCODING = 'utf-8'
python scripts\e2e_api.py --base-url http://127.0.0.1:8126 --photos-dir samples\photos

# UI e2e（普通桌面会话；v5 新增 AI 识鸟章节，产出 docs/screenshots/bird-*.png）
# 终端 1：mock 后端（全新进程；当前会话不能是 picking，否则启动页不出现）
cd E:\picturePick\backend
$env:PICTUREPICK_BIRD_MOCK = '1'; $env:PICTUREPICK_BIRD_MOCK_DELAY = '0.25'
$env:PICTUREPICK_CONFIG_DIR = 'E:\picturePick\.tmp\ui-e2e-config'
python -m picturepick serve --host 127.0.0.1 --port 8130 --no-open

# 终端 2：UI e2e（Chromium 的临时目录必须可写：把 TMP/TEMP 指到工作区）
cd E:\picturePick
New-Item -ItemType Directory -Force .tmp\chromium-temp | Out-Null
$env:TMP = 'E:\picturePick\.tmp\chromium-temp'; $env:TEMP = $env:TMP
$env:PP_BASE_URL = 'http://127.0.0.1:8130'; $env:PP_PHOTOS_DIR = 'E:\picturePick\samples\photos'
$env:PP_OUT_DIR = 'E:\picturePick\.tmp\verify'
$env:PP_PLAYWRIGHT_DIR = 'E:\picturePick\.scratch\ui-test'
$env:PP_CHROMIUM = 'E:\picturePick\.cache\playwright\chromium-1148\chrome-win\chrome.exe'
node scripts\ui_e2e.cjs

# 桌面版打包 + 自检（v5.2 未重新跑，结论见 §5/§7.6）
powershell -ExecutionPolicy Bypass -File E:\picturePick\scripts\build.ps1
powershell -ExecutionPolicy Bypass -File E:\picturePick\scripts\verify-desktop.ps1
```

## 7. 第 5.2 版 AI 识鸟验收明细（新增）

### 7.1 pytest（285 passed）

- v4 既有 179 全过（新字段为可选字段，不破坏旧断言；`PhotoItem`/`PickResult` 断言按需包含 `bird`）。
- 新增 106 个用例，集中于 7 个 `test_bird_*.py`，覆盖契约 `docs/BIRDID-CONTRACT.md`
  §2 配置/端点、§4 调用/双协议/`web_search`/`ToolNotOpen`/解析/模型参考图、§5 任务队列、§7 EXIF、
  §6.3 结果 JSON/restore、§9 模拟模式与失败路径；其中含 **端点协议（chat/responses、无效端点）**、
  **模型提供参考图 URL（不搜索图源）**、**ToolNotOpen 自动降级**、**SSRF 防护** 等用例。

### 7.2 API e2e（PASS=238 / FAIL=0）

- v4 既有 133 全过 + v5/v5.1/v5.2 累计 105 项（配置/完整端点校验/任务/队列/进度/参考图/采纳 EXIF/
  错误分支/结果 JSON/restore）。
- 全部使用 `PICTUREPICK_BIRD_MOCK=1`，确定性结果、无需网络与 Key（mock 参考图为内存生成示意图，
  不依赖方舟联网搜索插件）；EXIF 断言用 Pillow 读回标签并校验**原图图像数据字节不变**。

### 7.3 UI e2e（PASS=115 / FAIL=0）

- Lead 在普通桌面会话（Playwright Chromium + mock 后端 8130）完整跑通：**PASS=115 / FAIL=0**，
  日志 `.tmp/verify/ui-e2e-result.txt` 末尾 `ALL_PASS`；v4 既有 71 项全过，AI 识鸟章节新增 **44 项**
  全过（含完整端点设置、结果模态框**原照片 + 参考照片并排对照**、`bird-original-image` 走原图接口、
  `modal-close` X 关闭、左栏鸟种与结果页计数）。
- 本机 AI 沙箱内 Chromium 受 `platform_channel`/临时目录锁文件限制；通过把 `TMP`/`TEMP` 指到工作区
  目录并在完整权限会话运行后通过。复现命令见 §6；截图见 §7.4。

### 7.4 截图清单（UI e2e 生成，模拟模式）

| 文件（1440×900 PNG） | 内容 |
| --- | --- |
| `docs/screenshots/bird-queue.png` | v5 选片页三栏布局 + 右侧「AI 识鸟队列」（含识别中/待确认卡片与进度、左栏鸟种空状态） |
| `docs/screenshots/bird-result.png` | 识鸟结果模态框：顶部**原照片（左）+ 参考照片（AI 提供，右）并排**，右上有 X；下方种/科/属（中文+拉丁）+ 置信度 + 识别依据/备选 + 采纳/忽略（v5.2 已重新生成） |
| `docs/screenshots/bird-info.png` | 采纳后左侧「图片信息」栏的鸟种块（种/科/属、置信度、已写入 EXIF、来源与模型） |
| `docs/screenshots/bird-results.png` | 结果页「AI 识鸟：N 张已记录鸟种」计数与行内鸟种胶囊 |

> 截图在**模拟模式**生成：mock 参考图为后端内存生成的示意图；真实模式下参考图由模型 API
> （联网搜索）提供，需账号开通方舟「联网搜索」插件。
> 旧截图 `start/picker/picked/end-round-dialog/round2/results/export-photos/restored-results/resumed-picker.png`
> 为 v4 界面（三栏/识鸟界面以 `bird-*.png` 为准）。

### 7.5 前端构建

- `cd frontend; npx tsc --noEmit` + `npm run build`（Vite 构建到 `backend/picturepick/webui`）通过；
  v5.2 新增/修改的 `src/ui/bird/resultModal.ts`、`src/ui/modal.ts` 等均通过类型检查，未新增前端依赖。

### 7.6 打包影响评估

- `backend/picturepick/app.py` 静态 `from .birdid import BirdService` → `picturepick.birdid`
  及其子模块（config/client/parser/prompt/reference/exif/tasks/service）由 PyInstaller 随 import
  自动收集；`desktop_webview/picturepick-desktop.spec` 已含 `picturepick.app`，**无需新增 hiddenimports**。
- birdid 仅使用 Python 标准库（`urllib`/`json`/`threading`/`queue` 等）与已有 Pillow，
  **无新增第三方依赖、无新增打包数据**；`PIL.ImageDraw` 已在 spec hiddenimports 中。
- v5.2 未执行完整打包（避免耗时）；以上为静态分析结论，桌面端行为与命令行版一致。

### 7.7 真实 Key 双协议 + 联网检索参考图 + 真实 UI 全流程实测（v5.2，2026-10-25）

真实环境（本机网络 + 用户火山方舟 Key，插件已开通，配置目录隔离）实测：

- **端点直连 / 双协议**：
  - `endpoint=https://ark.cn-beijing.volces.com/api/v3/responses` + 模型 `doubao-seed-2-1-lite-260915`：
    识鸟任务 **2.4s** 完成，模型返回 JSON 解析正常（示例照片为抽象图，故 `identified=false`）；
  - `endpoint=https://ark.cn-beijing.volces.com/api/v3/chat/completions`：同样实测成功
    （任务完成、结果解析正常）；两种端点均由后端**原样 POST**，`endpointProtocol` 正确。
- **参考图链路已打通（联网搜索插件已开通）**：
  - 识别请求带 `tools:[{"type":"web_search"}]` 正常执行，`web_search_error` 为空（不再 ToolNotOpen）；
  - 模型联网检索后返回**真实参考图 URL**：
    `https://images.vliz.be/thumbs/64745_little-egret-egretta-garzetta.jpg?w=700`
    （来源 **WoRMS / VLIZ**）；后端 SSRF 校验后下载成功：`image/jpeg` **32KB**，下载耗时 **2.09s**；
  - 参考图仅在任务内存、经 `/reference` 代理显示，不落盘；本机不抓取搜索引擎/图库、不生成图片
    （v5.1 的搜索兜底已移除）；未开通插件时仍会自动降级识鸟但无参考图（自动化测试覆盖该降级路径）。
  - **维基/维基媒体链接会被后端直接拒绝**（`upload.wikimedia.org`、`wikipedia.org`、`commons.wikimedia.org`
    在中国大陆不可访问）；若首轮没有有效参考图，后端会**再向模型请求一次**「境内可访问的真实图片直链」
    （仍由模型 API 提供，本机不搜索图库），仍失败才显示「未获取到参考照片」。该“维基拒绝→二次模型请求→
    下载成功”路径由 pytest 覆盖（`test_blocked_reference_triggers_second_model_request`）。
  - **模型返回网页地址时**：后端识别出 `.htm/.html`、`/wiki/`、`/item/` 等页面 URL 后不下载，
    而是把该 URL 作为「不要重复」的反馈再次要求模型给图片文件直链（最多再请求 2 次），仍失败则无参考图；
    pytest 覆盖「首轮网页 → 第一次重试仍网页 → 第二次重试给图片 → 下载成功」
    （`test_page_url_is_rejected_and_model_asked_again`）。
  - **AI 只给网页链接时**：结果保留 `result.referencePageUrl`，前端在参考图占位处渲染可点击链接
    （`data-testid="bird-reference-link"`，`target=_blank` + `noopener`），用户可直接打开确认；
    UI e2e 通过接口拦截模拟「无图片、仅网页链接」场景并验证链接可点击（+2 项检查）。
- **真实 UI 全流程（真实 Key + 真实网络，非 mock）：PASS=15 / FAIL=0**
  - 测试连接 → 识鸟（白鹭 / 鹭科 / 白鹭属，置信度 **90%**）→ 结果模态框左「原照片」右「参考照片（AI 提供）」
    并排、参考图 `naturalWidth > 0` → 点击右上角 X 关闭 → 重新打开 → 采纳 → 左栏显示鸟种 →
    结果页 AI 计数 → 结果 JSON `birdIdentifiedCount=1`；
  - 任务总耗时约 **10.2s**（含联网检索）；真实结果截图证据保存在
    `.tmp/real-ui/real-result.png`（1440×900，仅作验证记录，**未放入 docs/ 以免引入第三方图片版权问题**）。

### 7.8 已知限制（与契约 §11 一致）

- AI 识鸟任务队列只在后端进程内存中：重启后端后未完成/已完成未采纳的任务消失（已采纳结果已写入
  JSON/EXIF，不会丢）。
- **参考照片依赖方舟「联网搜索」插件**（**本机已开通并实测可用**）：未开通时识鸟仍成功但参考图为空
  （`referenceNote` 给开通链接）；参考图只存内存（按鸟种缓存 ≤16 条），后端重启后需重新识别；
  模型也可能对某些鸟种找不到真实图片直链（此时同样无参考图）。
- EXIF 写入修改原图元数据（不重编码图像数据、保留已有标签）；只读/被占用会写入失败，此时仅记录到
  JSON，并提示「照片 EXIF 写入失败」。
- 真实识别会将被识别照片上传到火山方舟；参考照片 URL 由模型 API 联网检索后提供，**搜索请求由方舟侧
  完成**，本机只下载该 URL（不上传照片、不抓取搜索引擎）。识别结果仅供参考，不作为物种鉴定依据。
- `origin=browser` 会话不支持 AI 识鸟（`INVALID_SOURCE`）；本期不做批量自动识别/云端同步/XMP 写入。
