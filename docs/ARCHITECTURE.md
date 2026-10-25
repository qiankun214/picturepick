# PicturePick 架构与工程约定（第 5.2 版）

## 1. 目标交付物

| 交付形态 | 说明 |
| --- | --- |
| 命令行 + 浏览器 | `python -m picturepick serve --dir <目录>` 启动本地服务并自动打开浏览器 |
| 前后端分离开发 | Vite 开发服务器（5173）→ `/api` 代理到 FastAPI（8000） |
| 桌面应用 | **PyInstaller + pywebview（Edge WebView2）** 原生窗口，进程内启动同一份 FastAPI 后端；PyInstaller 产出 Windows 绿色版/zip |
| 可选历史方案 | Electron + electron-builder（代码保留在 `desktop/`，本机 Electron 无法启动时不要使用） |

第 5 版在以上形态中统一新增 **AI 识鸟**（火山方舟 `doubao-seed-2-1-lite` 识别照片鸟种）：
同一份后端代码（`picturepick.birdid` 子包）同时服务命令行、浏览器与桌面端，
不新增第三方依赖、不改变照片在本机流转的默认路径（仅用户主动识别时上传当前照片，见 §6）。

## 2. 技术选型（均在广泛使用、社区活跃的项目中选取）

- 后端：Python 3.10 + **FastAPI** + **uvicorn** + **Pillow**（缩略图 / 图片预处理 / EXIF）+ **pydantic**（1.10/2.x 兼容写法）
- 前端：**TypeScript** + **Vite**（原生 DOM，无框架，构建产物为静态 HTML/JS/CSS）
- AI 识鸟：**火山方舟（Volcengine Ark）HTTP API**，模型 `doubao-seed-2-1-lite`；支持 Chat Completions 与
  Responses API 两种完整端点。客户端用 Python 标准库 `urllib.request` 实现（**不新增依赖**）；
  参考照片由**模型 API 联网检索**（Responses API `web_search` 工具，需账号开通方舟「联网搜索」插件）
  给出真实图片直链，后端只下载/缩放/内存代理
- 桌面：**pywebview 6.x（Windows 走 Edge WebView2，依赖 pythonnet）** + **PyInstaller 6.x**
- 测试：**pytest** + `fastapi.testclient`；前端 `tsc --noEmit` + `vite build`；浏览器流程用 Playwright（Chromium）

## 3. 目录结构

```
E:\picturePick\
├── docs/                     # 契约（API.md 第 5.2 版）、架构、验证记录、截图
├── backend/                  # Python 后端
│   ├── picturepick/          # app / api / models / scanner / session / export / imaging / fsbrowse / cli
│   │   └── birdid/           # AI 识鸟（第 5/5.1/5.2 版，见 §6）
│   │       ├── __init__.py   #   对外导出 BirdService / build_client / BirdConfig
│   │       ├── config.py     #   完整端点/环境变量 + settings.json 配置、掩码、原子保存
│   │       ├── prompt.py     #   系统/用户提示词常量（要求模型联网检索真实参考图直链）
│   │       ├── parser.py     #   模型输出 → BirdResult（容错 JSON 提取/校验）
│   │       ├── client.py     #   ArkBirdClient（urllib 双协议 + web_search/ToolNotOpen 降级）+ MockBirdClient
│   │       ├── reference.py  #   参考照片：仅用模型提供的 URL，下载/SSRF/缩放（不落盘）
│   │       ├── exif.py       #   无损写 JPEG EXIF（Pillow 序列化 + APP1 原位替换 + 原子替换）
│   │       ├── tasks.py      #   BirdTask 数据、任务仓库、单线程 worker、进度/队列位置
│   │       └── service.py    #   BirdService：配置 + 提交 + 采纳 + EXIF 写盘 + 会话联动 + 参考图缓存
│   ├── tests/                # pytest（280 个用例）
│   └── pyproject.toml / requirements.txt
├── frontend/                 # TypeScript + Vite 前端
│   ├── src/
│   │   ├── main.ts           # 视图路由 + 隐藏文件夹 input 复用
│   │   ├── api.ts / types.ts / state.ts / viewer.ts / desktop.ts / directory.ts
│   │   └── ui/               # start / picker / filmstrip / results / modal / toast / screens
│   │       └── bird/         #   queue.ts（右侧队列+轮询）/ resultModal.ts / settings.ts / info.ts
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

## 4. 运行时数据流（第 5 版）

> 核心原则：**浏览器只负责“选目录/选目标文件夹”，拿到绝对路径后交给本机后端；
> 浏览器绝不读取、遍历或上传照片文件，所有照片由后端扫描并通过 `/image` 按需提供。**
> 第 5 版例外：用户主动点「AI 识鸟」时，当前这一张照片由后端压缩后上传到火山方舟识别（§4.5、§6）。

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
6. 第 5 版：选片页为三栏（左信息 / 中舞台 / 右识鸟队列），AI 识鸟数据流见 §4.5。

### 4.2 恢复现场与继续筛选（第 4 版）

- 结果 JSON 含 `sourceDirectory`、`photos[].relativePath/pickCount/pickedInRounds`、`lastRound`。
- 前端「载入 JSON 恢复现场」把 JSON 发给 `POST /api/sessions/restore`；后端重新扫描源目录、
  按相对路径匹配照片并**从 pickedInRounds 重建每一轮**（第 1 轮=全部照片；第 i 轮=含 i-1 的照片），
  恢复为 `phase=finished` 的会话（`restored=true`），可再次导出 JSON/照片。
- `POST /api/sessions/{sid}/resume`：在已结束会话上以最后一轮保留照片创建新一轮并回到 picking，
  用于恢复后继续筛选。
- 恢复相关的 UI 元素：`restore-button`、`restore-input`、`restore-notice`、`resume-button`。
- 第 5 版：结果 JSON 新增可选字段 `birdIdentifiedCount` / `photos[].bird`；restore 会宽容读取
  （结构不合法则忽略该照片的鸟种），恢复后 `PhotoItem.bird` 与左栏鸟种立即生效，resume 后保留。

### 4.3 兼容说明

- CLI `--dir` / 桌面端 `--dir` 仍是 server 来源预扫描，行为一致。
- `origin="browser"`（第 2 版的元数据导入）接口保留用于兼容/测试，但当前 UI 不再使用；
  AI 识鸟不支持 browser 来源（返回 `INVALID_SOURCE`）。
- 结果 JSON `schemaVersion` 仍为 `1`：v5 只新增可选字段，旧 JSON 可恢复、新 JSON 旧版本应用忽略新字段。

### 4.4 桌面窗口（WebView2）

`PicturePick.exe` → 进程内后台线程启动 FastAPI（随机空闲端口）→ 轮询 `/api/health` →
pywebview 创建 WebView2 窗口加载 `http://127.0.0.1:<port>/?desktop=webview2` →
preload 桥接提供原生「另存为 JSON / 打开路径」→ 窗口关闭后停止后端线程。
`--selftest` 可在不开窗口的情况下检查内置前端、pythonnet、WebView2 桥接与后端是否可用。

AI 识鸟新增的 `picturepick.birdid` 子包由 `picturepick.app` 静态 import，PyInstaller 会随
import 自动收集（spec 已把 `picturepick.app` 列入 hiddenimports），**spec 无需新增 hiddenimports**；
birdid 只用 stdlib 与 Pillow，也不新增打包数据。

### 4.5 AI 识鸟数据流（第 5.2 版）

```
选片页（浏览器/WebView2）
  │ ① 按 B / 点「AI 识鸟」 → POST /api/sessions/{sid}/bird-tasks {photoId}
  ▼
FastAPI（api.py → BirdService）
  │ ② 校验会话/照片/配置 → BirdTaskManager.submit() 入内存队列，立即返回 201 BirdTask(queued)
  ▼
单线程 worker（threading.Thread，daemon；一次只识别一张）
  │ ③ 读原图 → Pillow 转正/压到长边 ≤1568/q88 → base64
  │ ④ ArkBirdClient: POST 到配置的完整端点（按端点后缀自动选择 Chat Completions / Responses API；
  │    Responses 自动带 tools:[{"type":"web_search"}] 让模型联网检索参考图；Bearer Key；
  │    后端不拼接路径；超时/429/5xx 重试一次；ToolNotOpen 时去掉 tools 重试）
  │ ⑤ parser 容错解析 → BirdResult；失败 → 任务 failed（BIRD_API_ERROR / BIRD_PARSE_ERROR …）
  │ ⑥ reference：模型给出 referenceImageUrl → SSRF 校验 → 下载 → 缩放到长边 ≤1000 → 存任务内存
  │    （不搜索图源/不生成图片、不落盘；按鸟种内存缓存；无图时 referenceNote 带插件开通提示）
  ▼
任务 completed（result / referenceAvailable / referenceUrl）
  │ ⑦ 前端每 1000ms GET .../bird-tasks 刷新右侧队列卡片（页面隐藏暂停）
  │ ⑧ 点击卡片 → 结果模态框：原照片 + 参考照片（AI 提供）并排；参考图走 /reference 后端代理
  ▼
用户「采纳并写入 EXIF」→ POST .../bird-tasks/{tid}/adopt
  │ ⑨ exif.write_bird_exif()：Pillow 合并原 EXIF + 新标签 → APP1 原位替换 → 原子替换 → 恢复 mtime
  │ ⑩ 会话记录 BirdObservation（PhotoItem.bird / birdIdentifiedCount 立即生效，
  │    已结束会话的结果缓存失效）→ PickResult / 结果 JSON / restore 都带上鸟种
  ▼
再次浏览该照片：左侧信息栏「鸟类识别」块显示种/科/属/置信度/EXIF 状态；结果页显示鸟种胶囊与计数
```

## 5. 关键约定

- 接口以 `docs/API.md` 为唯一准绳（第 5.2 版新增 §9 AI 识鸟、§6.2 配置、§8.4 前端约定），
  功能唯一实现契约见 `docs/BIRDID-CONTRACT.md`。
- 照片 ID：server 来源 `sha1(绝对路径)[:16]`；browser 来源（兼容接口）`sha1(relativePath)[:16]`。
- 浏览器端不得读取照片文件（禁止 `webkitdirectory` / File System Access 导入）；图片一律经 `/image`。
- 导出照片由后端复制；自动化测试通过 `window.__picturepickTestSourceDir` /
  `window.__picturepickTestExportDir` 注入路径。
- AI 识鸟：任务队列在内存、单 worker 顺序处理；参考图**只由模型 API 提供 URL**、不落盘；
  EXIF 只改元数据、不重编码图像数据；API Key 只在后端使用，响应只给掩码（详见 §6）。
- 构建：`scripts/build.ps1`（前端 → PyInstaller → release）；PowerShell 脚本统一保存为 **UTF-8 with BOM**
  （Windows PowerShell 5.1 兼容）。
- 受限环境的 npm/二进制缓存约定见 README / 各子目录 README；普通桌面环境无需关心。

## 6. AI 识鸟模块与工程约定（第 5.2 版）

### 6.1 模块职责

| 模块 | 职责 |
| --- | --- |
| `config.py` | 解析环境变量 + `settings.json`（优先级见 6.3）；**端点是完整 URL**，校验协议（`/chat/completions`→chat、`/responses`→responses）并给出 `endpointWarning`；旧 Base URL 变量/字段兼容读取并在保存时迁移；`mask_key` 只暴露首尾；`save_settings` 原子写入并尽力收紧权限（0700/0600）；配置缺失/损坏视为未配置，不阻塞启动 |
| `prompt.py` | 系统提示词（要求只输出约定 JSON：`identified/family/genus/species/*Latin/confidence/note/alternatives/referenceImageUrl/referenceSource/referenceNote`；**要求模型联网检索真实来源并给出真实可访问的图片直链**，找不到必须留空、不得编造）+ 用户提示词 |
| `client.py` | `ArkBirdClient`（stdlib `urllib`，**向配置的完整端点原样 POST**；按端点自动选择 Chat Completions 或 Responses API 的请求体/响应解析；Responses 自动带 `tools:[{"type":"web_search"}]`；`ToolNotOpen`/联网搜索不可用时去掉 tools 重试一次并记录 `web_search_error`；`thinking` 被 400 拒绝时去掉重试；超时/429/5xx 重试；`verify()` 用极小 text-only 请求）与 `MockBirdClient`（确定性结果 + 内存参考图） |
| `parser.py` | 去 Markdown 围栏 → 截取 JSON → `json.loads`；字段清洗/截断/`confidence` 夹到 `[0,1]`/`alternatives` 最多 3 条；无鸟返回 `identified=false`；结构损坏抛 `BirdParseError` |
| `reference.py` | 参考图**严格使用模型 API 提供的 `referenceImageUrl`**（不做后端搜索/生成）：SSRF 校验 → 下载（15MB/6s）→ Pillow 缩到长边 ≤1000 q85 → **只返回内存 `bytes`，不落盘**；下载失败/无 URL 返回 `None` |
| `exif.py` | 无损写 JPEG EXIF（见 6.5）：保留已有标签、APP1 原位替换、内存校验、原子替换、恢复 mtime |
| `tasks.py` | `TaskRecord`（内部可变）/`BirdTask`（API 快照）转换；内存任务仓库；单线程 worker；状态机、stage/进度插值（估算 15s → 20–85%）、队列位置、排序；`RLock` 保证线程安全 |
| `service.py` | 编排：`status/update_config/verify`、`submit`（校验+入队）、`adopt`（写 EXIF + 记录会话）、`dismiss`、`reference`；worker 处理函数 `_process_task`（参考图缺失且 `web_search_error` 时写 `referenceNote` 开通提示）；参考图按鸟种内存缓存（≤16 条）；测试可注入配置解析器/客户端工厂/参考图解析器 |

### 6.2 线程模型与任务状态

- `BirdService` 在 `create_app()` 中创建并挂在 `app.state.bird`；每个后端进程一个 `BirdTaskManager`。
- 提交任务：`queue.Queue` 放入 task id，惰性启动名为 `picturepick-bird-worker` 的 **daemon 线程**；
  第一次提交时创建，之后复用。队列为空且无 running 时置 idle 事件（测试/CLI 可 `wait_idle()`）。
- **单 worker 顺序执行**：全局一次只识别一张（跨会话也串行），对火山方舟限流友好；排队任务有明确的
  `queuePosition`。进程退出不阻塞（daemon 线程）。
- 线程安全：任务记录用 `RLock` 保护；API 只读快照（`BirdTask` 模型），不会持有内部可变对象。
  `dismiss` 中断语义：任务标记 `cancelled`，worker 完成时丢弃结果；`queued` 任务被忽略后不再执行。
- 状态机：`queued → running → completed → adopted`，`completed` 也可 `dismissed`，`running` 失败 → `failed`；
  `failed` 重试 = 对同一 photoId **新建**任务（旧任务保留历史）。
- 进度语义：`queued`（0%）→ `preparing`（10%）→ `identifying`（20% 起按已用时间向 85% 插值，
  单任务估算 `ESTIMATED_SECONDS = 15`）→ `reference`（90%）→ `done`（100%）；进度单调不减。
  失败保留最后 stage，进度停住。
- 列表排序：`running`（最早开始）→ `queued`（提交顺序）→ 其它（`createdAt` 倒序）。
- 任务只在内存：后端重启后队列清空；已采纳的识别结果已写入结果 JSON / 照片 EXIF，不会丢。
- 会话生命周期：`phase=finished` 后拒绝**新**任务（409 `PHASE_FINISHED`），已存在任务继续跑完、仍可采纳；
  会话过期（24h）时 worker 找不到会话 → 任务失败 `SESSION_NOT_FOUND`。

### 6.3 配置优先级

解析优先级（高 → 低），实现见 `birdid/config.py::resolve_config`：

| 配置项 | 环境变量（优先） | 配置文件字段 | 默认值 |
| --- | --- | --- | --- |
| API Key | `PICTUREPICK_ARK_API_KEY`，其次 `ARK_API_KEY` | `arkApiKey` | 无（未配置） |
| API 端点 | `PICTUREPICK_ARK_ENDPOINT`（兼容旧 `PICTUREPICK_ARK_BASE_URL`，值按端点解释） | `arkEndpoint`（兼容旧 `arkBaseUrl`，只读） | `https://ark.cn-beijing.volces.com/api/v3/chat/completions` |
| 模型名 | `PICTUREPICK_ARK_MODEL` | `arkModel` | `doubao-seed-2-1-lite` |
| 模拟模式 | `PICTUREPICK_BIRD_MOCK`（显式设置即覆盖，含 `0/false` 关闭） | `mock` | `false` |

- **端点与协议（v5.1）**：配置的是**完整接口 URL**，`ArkBirdClient` 直接向它 `POST`，**不做任何路径拼接**。
  `endpoint_protocol()` 按 URL 路径选择协议：`.../chat/completions` → `chat`；`.../responses` → `responses`；
  其它（含双后缀、只填 Base URL）→ `None`：`GET /api/bird/status` 返回 `endpointWarning`，
  `POST .../bird-tasks` 返回 400 `INVALID_CONFIG`。`PUT /api/bird/config` 的 `endpoint` 走同一校验；
  空字符串清除后回退默认端点。旧 Base URL 变量/字段只按端点读取，保存时迁移为 `arkEndpoint` 并清掉 `arkBaseUrl`。
- 配置文件：`PICTUREPICK_CONFIG_DIR` 优先，否则 Windows `%APPDATA%\PicturePick\settings.json`、
  其它平台 `~/.config/PicturePick/settings.json`；保存为「临时文件 + `os.replace`」原子写，
  目录/文件权限尽力收紧（0700/0600）。
- `configured = mock || (key 非空)`；`keySource` 为 `env | config | mock | null`。
- **API Key 只在后端使用**：`GET /api/bird/status` 只返回 `keyMasked`；日志/异常信息不得打印 Key；
  `BirdConfig.api_key` 用 `repr=False` 避免意外泄露。桌面端/浏览器端都只与本机后端通信。

### 6.4 参考图内存代理（模型 API 提供 URL，不落盘）

- 参考图**只来自模型识别结果**：Responses API 请求带 `tools:[{"type":"web_search"}]`，提示词要求模型
  联网检索真实来源页面并给出真实可访问的图片直链；后端**不做图片搜索/生成**，只处理 URL。
- 后端流程：SSRF 校验（仅公网 `http(s)`、无 userinfo、拒绝 `localhost`/`*.local`/`*.internal`）→
  下载（上限 15MB、超时 6s，只接受 `image/*`）→ Pillow `exif_transpose` 缩到长边 ≤1000、JPEG q85 →
  存 `TaskRecord.reference`（`bytes`）**不写磁盘**、不进入结果 JSON。
- **按鸟种内存缓存**：`BirdService._reference_cache` 以（species, speciesLatin）为键，最多 16 条，
  同一鸟种再次识别不重复下载；进程重启即消失。
- **插件未开通降级（本机已开通并实测可用）**：Responses API 返回 `ToolNotOpen`（账号未开通
  「联网内容插件」）时，客户端去掉 `tools` 重试一次，识鸟仍成功；参考图缺失时 worker 把开通提示写入
  `result.referenceNote`（含 `https://console.volcengine.com/common-buy/CC_content_plugin`）。
  **本机实测开通后：模型联网返回真实参考图 URL（来源 WoRMS/VLIZ），后端下载成功
  `image/jpeg` 32KB / 2.09s，真实 UI 全流程 15/15。** Chat Completions 端点不带联网搜索工具。
- 前端不直连外网图片：`GET /api/sessions/{sid}/bird-tasks/{tid}/reference` 由后端代理返回
  `image/jpeg` + `Cache-Control: no-store`；无参考图 → 404 `BIRD_REFERENCE_UNAVAILABLE`，
  模态框显示 `referenceNote` 提示/「未获取到参考照片」。
- 参考图任何失败都不影响识别任务成功（`referenceAvailable=false`）。

### 6.5 EXIF 无损写入

目标：只改元数据、**不复制、不重编码、图像数据字节完全不变**，并尽量保留用户原有 EXIF。

1. 用 Pillow `Image.Exif` 读取原图 EXIF（预加载 Exif IFD / GPS IFD / Interop IFD，
   保留 IFD0/Exif/GPS/MakerNote 等已有标签；可能丢失内嵌 JPEG 缩略图预览，Pillow 固有行为）。
2. 写入/覆盖 5 个标签：`ImageDescription`（ASCII 拉丁名摘要）、`XPTitle`、`XPSubject`、`XPKeywords`、
   `XPComment`（UTF-16LE，Windows 资源管理器可读）；序列化失败时降级为只含新标签的 Exif。
3. 解析原 JPEG 段（SOI → APPn → … → SOS），**原位替换** APP1(Exif) 段；原文件没有 EXIF 时插入到
   所有 APPn 之后、第一个非 APP 段之前。图像数据段（SOS 之后）逐字节保持。
4. 在内存用 Pillow 重新打开校验新字节流是合法 JPEG，失败则保留原文件并返回错误。
5. 写同目录临时文件（`.picturepick-exif-*.jpg.tmp`）+ `fsync` + `os.replace` **原子替换**；
   替换成功后恢复原文件的 `mtime/atime`（`os.utime`），避免破坏按时间排序。
6. 任何一步失败都返回 `ExifOutcome(written=False, error=中文原因)`，**不抛异常到 API 层**；
   adopt 仍返回 200（识别结果已写入结果 JSON/会话）。`error` 仅失败时给出；成功但发生降级时
   `written=True, error=None`，降级原因只记在内部 `warning`（API 不暴露）。
- 路径安全：仅允许 `is_within(session.root, path)` 的 `.jpg/.jpeg` 普通文件；EXIF 序列化结果超过
  JPEG APP1 上限（约 64KB）时放弃写入。

### 6.6 结果落地与恢复

- 采纳后 `BirdObservation` 写入 `Session.bird_observations[photo_id]`，`PhotoItem.bird` /
  `SessionSummary.birdIdentifiedCount` 立即生效；已结束会话的 `_finished_result` 缓存失效，
  下一次取结果/下载 JSON 会重新生成（包含鸟种）。
- 结果 JSON：`photos[].bird`（`null` = 未识别）+ `birdIdentifiedCount`；`schemaVersion` 仍为 1。
- restore：宽容解析 `photos[].bird`（结构不合法忽略），恢复后鸟种立即生效；
  resume / 继续筛选不丢失鸟种。
- 前端：采纳后立即更新当前照片 `bird`、左栏与队列卡片；结果页计数优先用 JSON 字段，
  老 JSON 无该字段时按 `photos[].bird` 兜底。

### 6.7 前端三栏与轮询

- `src/ui/bird/`：`queue.ts`（右侧队列 + 1000ms 轮询 + 卡片增量更新）、`resultModal.ts`
  （结果/失败/进度/回看模态框；结果模态框顶部**原照片 + 参考照片并排对照**，失败/进度显示原照片）、
  `settings.ts`（设置弹窗）、`info.ts`（左栏鸟种块）；
  `picker.ts` 组装三栏、按钮/快捷键 `B`、面板折叠状态（`localStorage: picturepick.birdPanels.v1`，
  窗口 <1180px 默认折叠）。全部 testid 见 `docs/API.md` §8.4。
- 通用模态框 `modal.ts` 统一渲染右上角 X（`data-testid="modal-close"`）并绑定点击关闭；
  v5.2 修复了此前 X 未绑定点击事件、无法关闭的 BUG（Esc / 点击遮罩关闭保持不变）。
- 轮询：`document.hidden` 暂停、重新可见立即刷新；`destroy()` 停止定时器并 abort 在途请求；
  失败静默降级，下一轮自动恢复。
