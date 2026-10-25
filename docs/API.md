# PicturePick API 契约（第 5.2 版）

本文件是前后端唯一的接口契约。后端实现、前端调用、桌面端集成都必须严格遵守。
所有接口前缀 `/api`。所有请求/响应均为 UTF-8 JSON（图片接口除外）。

> **v5 变更摘要（AI 识鸟）**
>
> - 新增 **AI 识鸟**：调用火山方舟（Volcengine Ark）`doubao-seed-2-1-lite` 多模态模型识别照片中的
>   **科 / 属 / 种**（中文名 + 拉丁学名）与**参考照片**，用户采纳后写入**结果 JSON** 与**照片 EXIF**。
> - 新增配置接口 `GET /api/bird/status`、`PUT /api/bird/config`、`POST /api/bird/verify`；
>   新增任务接口 `POST/GET /api/sessions/{sid}/bird-tasks`、`GET .../{tid}`、`POST .../{tid}/adopt`、
>   `POST .../{tid}/dismiss`、`GET .../{tid}/reference`（详见 **§9**）。
> - 数据结构扩展：`SessionSummary.birdIdentifiedCount`、`PhotoItem.bird`、`PickResult.birdIdentifiedCount`
>   与 `photos[].bird`；新增 `BirdTask` / `BirdResult` / `BirdObservation` 等（详见 **§2**）。
> - 错误码新增 `BIRD_*` / `INVALID_CONFIG`（详见 **§1**）。
> - 环境变量与配置文件新增 `ARK_API_KEY` / `PICTUREPICK_ARK_*` / `PICTUREPICK_BIRD_MOCK*` 与
>   `settings.json`（详见 **§6.2**）。
> - 浏览器端约定新增快捷键 `B`、右侧任务队列、左栏鸟种、设置弹窗、恢复后鸟种保留（详见 **§8.4**）。
> - v1–v4.1 的既有内容全部保留：接口、字段、错误码不删不改；结果 JSON 的 `schemaVersion` 仍为 `1`
>   （只新增可选字段，旧 JSON 继续可恢复，新 JSON 在旧版本应用中被忽略新增字段）。
>
> **v5.1 变更摘要（端点直连，章节号沿用 v5）**
>
> - 配置项由 **Base URL** 改为**完整的 API 端点 URL**：后端把配置值**原样 POST**，不做任何隐藏的路径拼接。
> - 支持两种方舟接口，按端点路径自动选择协议：`/chat/completions`（Chat Completions）与
>   `/responses`（Responses API）；`BirdStatus` 以 `endpoint` / `endpointProtocol` / `endpointWarning`
>   取代 `baseUrl`（详见 **§2**、**§6.2**、**§9.1**）。
> - `PUT /api/bird/config` 请求字段改为 `endpoint`；不是受支持的完整端点 → 400 `INVALID_CONFIG`。
> - 环境变量新增 `PICTUREPICK_ARK_ENDPOINT`，配置文件字段新增 `arkEndpoint`；旧
>   `PICTUREPICK_ARK_BASE_URL` / `arkBaseUrl` 仅作兼容读取（值按端点解释），保存时迁移。
> - 设置弹窗字段改为「API 端点（完整 URL）」（`data-testid="bird-endpoint-input"`），状态行显示接口协议
>   与端点警告（详见 **§8.4**）。
>
> **v5.2 变更摘要（参考照片改为模型 API 提供，章节号沿用 v5）**
>
> - **参考照片严格由大模型 API 提供**：移除必应/维基百科等后端图片搜索兜底；识别请求（Responses API）
>   自动带 `tools:[{"type":"web_search"}]`，提示词要求模型联网检索真实来源页面并给出**真实可访问的
>   图片直链**。后端只做 SSRF 安全校验 → 下载该 URL → 缩放（≤1000px）→ 仅存内存 → `/reference` 代理；
>   **不搜索图源、不生成图片**（详见 **§9.4**）。
> - **联网搜索插件（本机已开通并实测可用；未开通时降级）**：API 返回 `ToolNotOpen`（账号未开通
>   「联网内容插件」）时自动去掉 tools 重试一次，识鸟仍成功；参考图缺失时 `result.referenceNote`
>   带开通提示（含 `https://console.volcengine.com/common-buy/CC_content_plugin`），前端参考图位置显示该提示。
>   本机实测开通后：模型联网返回真实参考图 URL（来源 WoRMS/VLIZ），后端 SSRF 校验后下载成功
>   `image/jpeg` 32KB / 2.09s，真实 UI 全流程 15/15 通过（详见 **§9.1**、**§9.4**）。
> - 结果/失败/进度模态框布局调整：结果模态框顶部**并排显示「原照片 + 参考照片（AI 提供）」**
>   （`data-testid="bird-original-image"` / `bird-reference-image`），鸟种/科属/置信度在下方；
>   失败/进度模态框也显示原照片。修复右上角 X 未绑定点击事件的问题（`modal.ts`，
>   `data-testid="modal-close"`）（详见 **§8.4**）。

## 0. 术语

- **照片（photo）**：用户所选目录下扩展名为 `.jpg` / `.jpeg`（大小写不敏感）的普通文件。
- **照片 ID**：`sha1(identity)[:16]` 的十六进制小写串。`identity` 在 server 来源会话中为绝对路径，
  在 browser 来源会话中为相对路径。会话内唯一。
- **会话来源（origin）**：`server`（后端扫描本机目录，图片由后端提供）或
  `browser`（浏览器原生目录选择，文件只在浏览器内存中，后端仅保存照片元数据与轮次状态）。
- **轮次（round）**：第 1 轮为全部照片；之后每一轮为上一轮被 pick 的照片。
- **pickCount**：该照片在历史各轮中被 pick 的总次数（最终 JSON 记录此值）。
- **viewed**：该照片在本轮是否被展示过（前端浏览到时上报）。
- **phase**：`picking`（选片中）或 `finished`（已结束并生成结果）。
- **AI 识鸟任务（bird task）**：一次「对某张照片调用模型识别鸟种」的后台任务，有
  `queued / running / completed / failed / adopted / dismissed` 六种状态（§9）。
- **鸟类识别记录（bird observation）**：用户**采纳**后的识别结果；写入会话、`PhotoItem.bird` 与
  结果 JSON 的 `photos[].bird`，同时尽力写入照片 EXIF。
- **参考照片（reference）**：模型给出的同类鸟对照图，仅在任务内存中保存、**不落盘**，
  前端通过后端代理接口读取（§9.4）。

> 说明：第 2 版起，浏览器 UI 使用**浏览器自带目录选择框**（`<input type="file" webkitdirectory>`），
> 照片文件不会上传到后端；后端只接收照片元数据（相对路径/大小）用于轮次与 pick 计数。
> CLI 的 `serve --dir` / 桌面端 `--dir` 仍使用 server 来源（后端扫描并提供图片）。
> 第 3 版起 UI 改为「系统原生文件夹对话框只返回绝对路径 → 后端扫描/供图」，browser 来源仅保留兼容。
> 第 5 版新增 AI 识鸟；**真实识别模式下照片会被上传到火山方舟**（见 §9.7 隐私说明）。

## 1. 通用错误格式

非 2xx 响应体：

```json
{ "detail": { "code": "INVALID_PATH", "message": "目录不存在或不可读" } }
```

`code` 取值（v1–v4 既有）：`INVALID_PATH`、`NO_PHOTOS`、`INVALID_PHOTOS`、`TOO_MANY_PHOTOS`、
`SESSION_NOT_FOUND`、`PHOTO_NOT_FOUND`、`ROUND_NOT_FOUND`、`NOT_ENOUGH_PICKS`、`PHASE_FINISHED`、
`PHASE_PICKING`、`DIALOG_UNAVAILABLE`、`WRITE_FAILED`、`NO_PICKED_PHOTOS`、`INVALID_SOURCE`、
`INVALID_RESTORE`、`UNSUPPORTED_SCHEMA`。

`code` 取值（v5 新增，AI 识鸟）：

| 错误码 | HTTP | 含义 |
| --- | --- | --- |
| `INVALID_CONFIG` | 400 | `PUT /api/bird/config` 配置非法（API 端点不是受支持的完整接口 URL、`mock` 不是布尔值、配置文件写失败等）；提交识别任务时代码端点无效也返回该错误 |
| `BIRD_NOT_CONFIGURED` | 409 | 未配置 ARK API Key 且未开启模拟模式，不能提交识别任务 |
| `BIRD_TASK_NOT_FOUND` | 404 | 识别任务不存在或不属于该会话 |
| `BIRD_TASK_NOT_COMPLETED` | 409 | 任务尚未完成（queued / running），或已失败/已忽略，不能采纳 |
| `BIRD_TASK_ADOPTED` | 409 | 任务已被采纳，不能再忽略 |
| `BIRD_RESULT_UNAVAILABLE` | 409 | 任务没有可采纳的结果（模型判断画面中没有鸟，`identified=false`） |
| `BIRD_REFERENCE_UNAVAILABLE` | 404 | 该任务没有可用的参考照片（未完成或参考图获取失败） |

**任务级错误码**（只出现在 `BirdTask.error.code` 中，不作为 HTTP 响应码；任务的失败不改变 HTTP 200/201）：

| 错误码 | 含义 |
| --- | --- |
| `BIRD_API_ERROR` | 调用方舟接口失败（HTTP 非 2xx、网络错误、返回结构异常、模拟失败等） |
| `BIRD_API_TIMEOUT` | 调用方舟接口超时（90s） |
| `BIRD_PARSE_ERROR` | 模型输出无法解析为识别结果（或照片无法读取用于识别） |
| `SESSION_NOT_FOUND` | 任务执行时会话已过期（24h）/不存在 |
| `PHOTO_NOT_FOUND` | 任务执行时照片已不存在或不可读 |

## 2. 数据结构

### SessionSummary

```json
{
  "id": "9f2c...（32 位 hex）",
  "origin": "server",
  "sourceLabel": "E:\\photos",
  "root": "E:\\photos",
  "recursive": true,
  "createdAt": "2026-10-24T15:00:00+08:00",
  "phase": "picking",
  "restored": false,
  "roundIndex": 1,
  "roundCount": 1,
  "libraryTotal": 120,
  "roundPhotoCount": 120,
  "viewedCount": 0,
  "pickCount": 0,
  "birdIdentifiedCount": 0,
  "rounds": [
    { "index": 1, "photoCount": 120, "viewedCount": 0, "pickedCount": 0 }
  ]
}
```

说明：`viewedCount` / `pickCount` 是**当前轮**的计数；`rounds[].pickedCount` 是**该轮** pick 数。
`origin=server` 时 `root`/`sourceLabel` 为绝对目录路径；`origin=browser` 时为浏览器选择的文件夹名。
`restored` 表示该会话是否通过 `POST /api/sessions/restore` 从 JSON 恢复（默认 `false`）。

**v5 新增**：`birdIdentifiedCount`（int，默认 `0`）为本会话**已采纳** AI 识鸟的照片数
（即 `photos[].bird` 非空的数量）；由 `POST .../bird-tasks/{tid}/adopt` 实时更新，
恢复会话时按结果 JSON 的 `photos[].bird` 重建。

### PhotoItem（分页列表项）

```json
{
  "id": "a1b2c3d4e5f60718",
  "name": "IMG_001.jpg",
  "relativePath": "2024/IMG_001.jpg",
  "sizeBytes": 2845312,
  "pickCount": 2,
  "picked": true,
  "viewed": false,
  "bird": null
}
```

`picked` / `viewed` 均针对**当前轮**。`pickCount` 为历史累计。

**v5 新增**：`bird`（`BirdObservation | null`）为该照片**已采纳**的鸟类识别，未识别为 `null`；
出现在 `GET .../rounds/{i}/photos` 的分页列表、`POST .../viewed` 与 `PUT/DELETE .../pick` 之后的
摘要刷新响应里（照片列表接口本身返回 `PhotoItem[]`；pick/viewed 返回 `SessionSummary`，
`birdIdentifiedCount` 随之刷新）。

### PickResult（最终 JSON）

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-10-24T15:30:00+08:00",
  "origin": "server",
  "sourceDirectory": "E:\\photos",
  "recursive": true,
  "totalPhotos": 120,
  "roundsPlayed": 3,
  "pickedPhotoCount": 12,
  "birdIdentifiedCount": 2,
  "lastRound": {
    "index": 3,
    "pickedCount": 2,
    "pickedPhotoIds": ["a1b2c3d4e5f60718", "f0e1d2c3b4a59687"]
  },
  "photos": [
    {
      "id": "a1b2c3d4e5f60718",
      "name": "IMG_001.jpg",
      "relativePath": "2024/IMG_001.jpg",
      "absolutePath": "E:\\photos\\2024\\IMG_001.jpg",
      "pickCount": 3,
      "pickedInRounds": [1, 2, 3],
      "bird": null
    }
  ]
}
```

- `photos` 包含**全部**扫到的照片（未 pick 的 `pickCount` 为 0），按 `pickCount desc, relativePath asc` 排序。
- `pickedPhotoCount` = `pickCount > 0` 的照片数。
- `pickedInRounds` 升序，记录该照片在第几轮被 pick。
- `lastRound`：**最后一轮仍然保留**的照片，供前端「一键导出保留照片」使用；
  `pickedPhotoIds` 顺序与当前轮照片顺序一致。
- `absolutePath`：`origin=server` 时为绝对路径；`origin=browser` 时为空字符串（浏览器不暴露绝对路径，
  来源以 `sourceDirectory` + `relativePath` 标识）。
- 结果 JSON 的 `sourceDirectory` 在 `origin=browser` 时等于会话的 `sourceLabel`。

**v5 新增**：`birdIdentifiedCount`（int）与 `photos[].bird`（`BirdObservation | null`）。
采纳后立即写回会话，`GET /api/sessions/{sid}/result`、`POST .../finish`、JSON 下载/另存为都会包含；
`schemaVersion` 仍为 `1`（只新增可选字段）。**旧 JSON（无这些字段）继续可恢复**。

### BirdAlternative（v5 新增）

```json
{ "species": "黄嘴白鹭", "speciesLatin": "Egretta eulophotes", "confidence": 0.04 }
```

### BirdResult（v5 新增）

模型返回的原始结构化识别结果（任务的 `result` 字段）：

```json
{
  "identified": true,
  "family": "鹭科",
  "familyLatin": "Ardeidae",
  "genus": "白鹭属",
  "genusLatin": "Egretta",
  "species": "白鹭",
  "speciesLatin": "Egretta garzetta",
  "commonNameEn": "Little Egret",
  "confidence": 0.92,
  "note": "白色羽毛、黑色喙与腿，体型修长（一句话识别依据）",
  "reason": "",
  "alternatives": [
    { "species": "黄嘴白鹭", "speciesLatin": "Egretta eulophotes", "confidence": 0.04 }
  ],
  "referenceSource": "中国鸟类图库",
  "referenceNote": "参考照片中该鸟的典型特征"
}
```

- `identified=false` 时 `reason` 为模型给出的原因（非空），其余分类字段为空、`alternatives` 为空；
  此类任务**不可采纳**（`BIRD_RESULT_UNAVAILABLE`），前端只提供「忽略 / 关闭」。
- 所有字符串字段均已 `strip` 并截断（≤ 200 字符）；`confidence` 夹到 `[0,1]`，非数字为 `null`；
  `alternatives` 最多 3 条且只保留 `species` 非空的项。
- `referenceSource` 为参考照片来源标签（模型给出的来源站点，如 `维基百科`、`中国鸟类图库`；模拟模式为
  `Mock Reference`）。
- `referenceNote`：参考照片说明/来源页面；**参考图缺失时**（如账号未开通「联网搜索」插件）后端会写入
  开通提示（含方舟控制台链接），前端在参考图占位处展示该文本。

### BirdObservation（v5 新增）

**已采纳**的识别记录（`PhotoItem.bird` / `PickResult.photos[].bird` / adopt 响应中的 `observation`）：

```json
{
  "family": "鹭科",
  "familyLatin": "Ardeidae",
  "genus": "白鹭属",
  "genusLatin": "Egretta",
  "species": "白鹭",
  "speciesLatin": "Egretta garzetta",
  "commonNameEn": "Little Egret",
  "confidence": 0.92,
  "note": "白色羽毛、黑色喙与腿，体型修长",
  "referenceSource": "中国鸟类图库",
  "model": "doubao-seed-2-1-lite",
  "adoptedAt": "2026-10-25T16:20:00+08:00",
  "exifWritten": true,
  "exifError": null
}
```

- `exifWritten`/`exifError` 记录采纳时 EXIF 写入结果；EXIF 写入失败不影响本次采纳写入 JSON/会话
  （`exifWritten=false` + 中文 `exifError`）。
- `adoptedAt` 为 ISO-8601 字符串（带时区偏移）。

### BirdTask（v5 新增）

```json
{
  "id": "4f1d...（32 位 hex）",
  "sessionId": "9f2c...",
  "photoId": "a1b2c3d4e5f60718",
  "photoName": "IMG_001.jpg",
  "photoRelativePath": "2024/IMG_001.jpg",
  "thumbnailUrl": "/api/sessions/9f2c.../photos/a1b2c3d4e5f60718/image?variant=thumb",
  "status": "queued",
  "stage": "queued",
  "progress": 0,
  "queuePosition": 1,
  "createdAt": "2026-10-25T16:10:00+08:00",
  "startedAt": null,
  "finishedAt": null,
  "elapsedMs": 0,
  "model": "doubao-seed-2-1-lite",
  "result": null,
  "error": null,
  "adopted": false,
  "referenceAvailable": false,
  "referenceUrl": null
}
```

- `status`：`queued | running | completed | failed | adopted | dismissed`；
  `stage`：`queued | preparing | identifying | reference | done`。
- `progress`：0–100 的整数，单调不减（语义见 §9.1）。
- `queuePosition`：1-based，仅 `queued` 任务有（1 = 下一个执行）；其它状态为 `null`。
- `elapsedMs`：`running` 时为已运行毫秒；结束态为总耗时；未开始为 0。
- `result`：`BirdResult | null`（`completed/adopted` 时有值；`dismissed` 后为已丢弃后的快照）。
- `error`：`{ "code": "BIRD_API_ERROR", "message": "…" } | null`（任务级错误码见 §1）。
- `referenceAvailable`：是否有可直接读取的参考照片（v5.2：**仅当模型提供了真实图片直链且后端下载成功**
  时为 `true`；未开通联网搜索插件/无 URL/下载失败均为 `false`）；`referenceUrl` 为参考图代理地址
  （`/api/sessions/{sid}/bird-tasks/{tid}/reference`），无参考图时为 `null`。
- 参考图**不包含**在任务 JSON 中（只占任务内存，见 §9.4）；`referenceSource`/`referenceUrl` 等
  外链也不暴露给前端。

### BirdQueueInfo（v5 新增）

```json
{ "queued": 1, "running": 1 }
```

当前后端进程内**全部会话**的排队 / 识别中任务数（`GET /api/bird/status` 与 `GET .../bird-tasks` 都返回）。

### 其他 v5/v5.1/v5.2 请求/响应模型

```ts
// POST /api/sessions/{sid}/bird-tasks 请求
interface BirdTaskCreateRequest { photoId: string; }

// GET /api/sessions/{sid}/bird-tasks 响应
interface BirdTaskListResponse {
  tasks: BirdTask[];
  queue: BirdQueueInfo;
}

// GET /api/bird/status、PUT /api/bird/config 响应
interface BirdStatus {
  configured: boolean;
  model: string;
  endpoint: string;                  // 完整 API 端点 URL（后端原样 POST，不拼接）
  endpointProtocol: 'chat' | 'responses' | null;  // 按端点路径自动识别；null = 不支持
  endpointWarning: string | null;    // 端点不是受支持的完整接口 URL 时的提示
  keyMasked: string | null;          // 未配置为 null；永不返回明文 Key
  keySource: 'env' | 'config' | 'mock' | null;
  mock: boolean;
  queue: BirdQueueInfo;
}

// PUT /api/bird/config 请求（字段均可缺省；显式 null / 空字符串 = 清除该字段）
interface BirdConfigRequest {
  apiKey?: string | null;
  endpoint?: string | null;          // 完整端点；必须是 .../chat/completions 或 .../responses
  model?: string | null;
  mock?: boolean | null;
}

// POST /api/bird/verify 响应（失败也 200）
interface BirdVerifyResponse {
  ok: boolean;
  message: string;
  latencyMs: number | null;
  model: string;
}

// POST .../bird-tasks/{tid}/adopt 响应
interface BirdAdoptResponse {
  task: BirdTask;
  observation: BirdObservation;
  exif: { written: boolean; path: string; error: string | null };
}
```

## 3. 系统接口

> 第 2 版起浏览器 UI 不再使用本节的自定义目录浏览接口（改用浏览器自带目录选择框）；
> 这些接口保留给 CLI/调试与向后兼容，前端不应在正常流程中调用。

### GET /api/health

```json
{ "status": "ok", "name": "picturepick", "version": "1.0.0" }
```

### GET /api/fs/roots

```json
{
  "home": "C:\\Users\\qiankun",
  "startPath": "C:\\Users\\qiankun",
  "roots": [
    { "name": "C:\\", "path": "C:\\" },
    { "name": "E:\\", "path": "E:\\" }
  ]
}
```

### GET /api/fs/list?path=<绝对路径>

只列目录（用于目录选择器）。按名称不区分大小写排序，最多 500 条。

```json
{
  "path": "E:\\photos",
  "parent": "E:\\",
  "entries": [
    { "name": "2024", "path": "E:\\photos\\2024", "isDir": true }
  ]
}
```

路径不存在 / 不是目录 / 无权限 → 400 `INVALID_PATH`。

### POST /api/dialog/pick-directory

供浏览器模式调起本机原生目录选择框（tkinter，尽力而为）。在无 GUI / 打包环境不可用时返回 501 `DIALOG_UNAVAILABLE`，前端必须回退到 `/api/fs/list` 目录浏览。

```json
{ "path": "E:\\photos", "cancelled": false }
```

取消：`{ "path": null, "cancelled": true }`。

## 4. 会话接口

### POST /api/sessions

创建会话（同时激活为 current session）。支持两种来源：

**A. browser（浏览器原生目录选择，推荐）**

浏览器用 `<input type="file" webkitdirectory>` 让用户选择文件夹后，只把照片元数据发给后端：

```json
{
  "source": "browser",
  "label": "假期照片",
  "recursive": true,
  "photos": [
    { "relativePath": "2024/IMG_001.jpg", "sizeBytes": 2845312, "lastModified": 1719999999000 },
    { "relativePath": "IMG_002.jpeg", "sizeBytes": 1845312, "lastModified": 1719999998000 }
  ]
}
```

- `label`：浏览器选择的根文件夹名，用于展示与结果 JSON 的 `sourceDirectory`。
- `photos[].relativePath`：必须为相对路径（正斜杠 `/` 分隔，不允许以 `/`、盘符或 `..` 开头/包含越界段），
  扩展名必须为 `.jpg` / `.jpeg`（大小写不敏感）。
- 后端不读文件系统，照片 ID = `sha1(relativePath)[:16]`，`absolutePath` 为空字符串。
- 校验失败：`INVALID_PHOTOS`（格式非法）、`TOO_MANY_PHOTOS`（> 50000 张）、`NO_PHOTOS`（空列表）。
- 结果：201 + `SessionSummary`（`origin="browser"`，`root=label`）。
- v5：`origin=browser` 会话**不支持 AI 识鸟**（提交/采纳返回 400 `INVALID_SOURCE`，见 §9）。

**B. server（后端扫描本机目录，用于 CLI `--dir` / 桌面端 `--dir`）**

```json
{ "source": "server", "directory": "E:\\photos", "recursive": true }
```

`source` 省略时默认 `"server"`（向后兼容）。后端扫描目录并按绝对路径生成照片 ID。

- 成功 → 201，返回 `SessionSummary`。
- 目录不存在 / 不可读 → 400 `INVALID_PATH`。
- 无 jpg/jpeg 文件 → 400 `NO_PHOTOS`。

### POST /api/sessions/restore

从之前保存的 **结果 JSON** 恢复现场（载入 JSON 继续查看/导出/再筛选）。请求体直接是保存的 `PickResult` JSON：

```json
{ "schemaVersion": 1, "generatedAt": "...", "origin": "server", "sourceDirectory": "E:\\photos", "photos": [ ... ] }
```

- 校验：`schemaVersion` 必须为 1（缺失时按 1 处理；否则 400 `UNSUPPORTED_SCHEMA`）；
  `origin` 缺失时按 `"server"` 处理（兼容旧版 JSON），只有显式 `origin="browser"` 才返回 400 `INVALID_SOURCE`；
  `photos` 结构非法 → 400 `INVALID_RESTORE`。
- 后端**重新扫描 `sourceDirectory`**，按 `relativePath` 匹配照片（大小写不敏感）；JSON 中已不存在的照片会被忽略。
  若源目录不可访问、或扫描后一张都匹配不到，则**回退用 `photos[].absolutePath`** 中仍存在的文件重建
  （适用于目录被移动/改名但文件路径仍可访问的情况）；两者都不可用时返回 400 `INVALID_PATH`，
  `message` 会包含 `sourceDirectory` 原文，便于定位（如网络盘未连接）。
- 重建轮次与计数（无需额外保存轮次明细）：
  - 第 1 轮 = 当前可用的全部照片；
  - 第 i 轮（i ≥ 2）= JSON 中 `pickedInRounds` 包含 `i-1` 的照片（保持上一轮顺序）；
  - `pickCount` / `pickedInRounds` 按 JSON 恢复；`viewed` 重置为 0；
  - 最后一轮保留照片 = JSON `lastRound.pickedPhotoIds`；旧版 JSON 无 `lastRound` 时按
    `pickedInRounds` 含 `roundsPlayed` 自动推导。
- **v5**：若 JSON 的 `photos[].bird` 存在，恢复后立即生效为 `PhotoItem.bird`，并计入
  `SessionSummary.birdIdentifiedCount`；结构不合法的 `bird` 会被忽略（不导致恢复失败）。
  老 JSON（无 `bird`）行为不变。
- 恢复后的会话 `phase="finished"`、`roundIndex=roundsPlayed`，可直接看结果、下载 JSON、导出照片，
  也可调用 `POST /api/sessions/{sid}/resume` 继续筛一轮。
- 成功 → 201 + `SessionSummary`（其中 `restored=true`）。

### GET /api/sessions/current

返回最近创建且未过期的会话，供命令行 `--dir` 预扫描后前端自动进入。

```json
{ "session": null }
```

或 `{ "session": SessionSummary }`。

> `origin=browser` 的会话在页面刷新后无法恢复照片文件（文件只存在于原页面内存中），
> 前端发现 `current` 为 browser 会话且本地无文件映射时，应回到启动页并提示重新选择文件夹。

### GET /api/sessions/{sid}

返回 `SessionSummary`。不存在 → 404 `SESSION_NOT_FOUND`。

### GET /api/sessions/{sid}/rounds/{index}/photos?offset=0&limit=500

`index` 从 1 开始。分页返回该轮照片列表，顺序即放映顺序。

```json
{
  "roundIndex": 1,
  "total": 120,
  "offset": 0,
  "limit": 500,
  "photos": [ PhotoItem, ... ]
}
```

`photos[].bird` 为 v5 新增字段（见 §2 PhotoItem）。

### GET /api/sessions/{sid}/photos/{pid}/image?variant=original|thumb

返回 `image/jpeg` 二进制。`variant` 默认 `original`；`thumb` 返回最长边 ≤ 512 的缩略图。
响应头 `Cache-Control: public, max-age=3600`。

- **仅 `origin=server` 会话可用**；`origin=browser` 会话的照片由前端用浏览器本地 `File` 对象显示，
  调用该接口返回 404 `PHOTO_NOT_FOUND`。
- 照片不属于会话 / 文件已不存在 → 404 `PHOTO_NOT_FOUND`。

### POST /api/sessions/{sid}/photos/{pid}/viewed

标记当前轮该照片已浏览（幂等）。返回 `SessionSummary`。

### PUT /api/sessions/{sid}/photos/{pid}/pick

将当前轮该照片标记为 pick。重复调用幂等（不重复计数）。返回 `SessionSummary`。

### DELETE /api/sessions/{sid}/photos/{pid}/pick

取消当前轮 pick（本轮计数 -1，pickCount -1）。返回 `SessionSummary`。
若该照片本轮未被 pick，幂等返回当前状态。

### POST /api/sessions/{sid}/rounds/next

结束当前轮，用当前轮 pick 的照片创建下一轮。

- 当前轮 `pickedCount < 2` → 409 `NOT_ENOUGH_PICKS`（无法再筛选）。
- `phase == finished` → 409 `PHASE_FINISHED`。
- 成功 → 200 `SessionSummary`（`roundIndex` 已 +1，新一轮计数从 0 开始；`pickCount` 保留历史累计）。

### POST /api/sessions/{sid}/resume

在**已结束（finished）**的会话上继续筛选：用最后一轮仍然保留的照片创建新的一轮，`phase` 回到 `picking`。

- 仅 `origin="server"`；`origin="browser"` → 400 `INVALID_SOURCE`。
- `phase == picking` → 409 `PHASE_PICKING`（当前已在选片中）。
- 最后一轮保留照片 < 2 张 → 409 `NOT_ENOUGH_PICKS`。
- 成功 → 200 `SessionSummary`（`roundIndex = 原 roundIndex + 1`，新一轮照片 = 原最后一轮保留照片，顺序保持不变；
  历史 `pickCount` / `pickedInRounds` 保留）。
- v5：已采纳的鸟种记录在 resume 后继续保留（`PhotoItem.bird` / `birdIdentifiedCount` 不变）。

### POST /api/sessions/{sid}/finish

结束会话并生成最终结果。幂等：重复调用返回相同结果。

```json
{ "result": PickResult, "savedPath": null }
```

同时 `phase` 变为 `finished`。v5：`result.birdIdentifiedCount` / `result.photos[].bird` 包含已采纳的识别；
采纳发生在 finish 之后时（会话已结束但任务仍可采纳）会立即使结果缓存失效并反映到新结果中。

### GET /api/sessions/{sid}/result

- 默认返回 `PickResult`（未 finish 也可调用，返回当前状态的结果）。
- `?download=1` → 以附件形式下载，文件名 `picturepick-<根目录名>-<yyyyMMdd-HHmmss>.json`，`Content-Disposition: attachment`。

### POST /api/sessions/{sid}/export

把结果写成本机文件（桌面端原生“另存为”后调用；浏览器模式可省略）。

请求：`{ "path": "E:\\out\\pick-result.json" }`
响应：`{ "path": "E:\\out\\pick-result.json" }`
写失败 → 400 `WRITE_FAILED`。

### POST /api/sessions/{sid}/export-photos

把**最后一轮仍然保留**的照片复制到用户指定的文件夹（后端在本机直接复制文件，浏览器不读取/上传照片）。
仅 `origin="server"` 会话可用。

请求：

```json
{ "destination": "E:\\导出\\最终保留" }
```

- `destination` 必须是已存在且可写的目录（通常由系统文件夹对话框选择）。
- 后端取 `PickResult.lastRound.pickedPhotoIds`，把对应照片复制到 `destination`；
  同名文件自动追加 `-1`、`-2` 后缀，不覆盖已有文件。
- 成功 → 200：

```json
{
  "destination": "E:\\导出\\最终保留",
  "copied": 2,
  "files": [
    { "name": "IMG_0002.jpg", "relativePath": "2024-春游/IMG_0002.jpg", "sizeBytes": 2845312 },
    { "name": "IMG_0007.jpg", "relativePath": "2024-春游/IMG_0007.jpg", "sizeBytes": 1845312 }
  ]
}
```

- 错误：`destination` 不存在/无权限 → 400 `INVALID_PATH`；最后一轮没有保留照片 → 400 `NO_PICKED_PHOTOS`；
  `origin="browser"` → 400 `INVALID_SOURCE`；复制失败 → 400 `WRITE_FAILED`。
- 复制是**逐字节复制**（v5 采纳后照片 EXIF 已被修改，导出的是带鸟种 EXIF 的当前文件）。

## 5. 静态资源

后端在 `/` 提供前端构建产物（`picturepick/webui`，PyInstaller 下为 `sys._MEIPASS/webui`）：

- 存在时：`GET /` 返回 `index.html`，其余静态文件按路径返回；未匹配的路径回退到 `index.html`（SPA）。
- 不存在时：`GET /` 返回 JSON 提示（前端未构建）。
- API 路由优先于静态资源，不得被 SPA fallback 吞掉。

CORS：允许来源正则 `^https?://(localhost|127\.0\.0\.1)(:\d+)?$`（开发期 Vite 5173 端口跨域）。

## 6. 命令行与环境变量

### 6.1 命令行

```
python -m picturepick serve [--host 127.0.0.1] [--port 8000] [--dir PATH]
                            [--recursive|--no-recursive] [--no-open] [--log-level info]
python -m picturepick scan --dir PATH [--recursive|--no-recursive]
python -m picturepick version
```

- `serve` / `scan` 均默认递归扫描子目录；`--no-recursive` 只扫描顶层。

- `serve` 默认 `--open`：启动后自动用系统默认浏览器打开 `http://host:port/`；`--no-open` 关闭（桌面端使用）。
- `serve --dir PATH`：启动前先扫描并创建 current session，浏览器打开后前端自动进入选片模式。
- 端口被占用时打印清晰错误并以非 0 退出码退出。
- 启动时 stdout 打印一行 `PicturePick server listening on http://127.0.0.1:<port>`（桌面端可据此或轮询 /api/health 判断就绪）。
- 包版本号唯一来源：`picturepick/__init__.py` 的 `__version__ = "1.0.0"`。

### 6.2 AI 识鸟配置（环境变量 / settings.json）

**解析优先级（高 → 低）**：

| 配置项 | 环境变量（优先） | 配置文件字段 | 默认值 |
| --- | --- | --- | --- |
| API Key | `PICTUREPICK_ARK_API_KEY`，其次 `ARK_API_KEY` | `arkApiKey` | 无（未配置） |
| API 端点 | `PICTUREPICK_ARK_ENDPOINT`（兼容旧 `PICTUREPICK_ARK_BASE_URL`，其值按端点解释） | `arkEndpoint`（兼容旧 `arkBaseUrl`，只读） | `https://ark.cn-beijing.volces.com/api/v3/chat/completions` |
| 模型名 | `PICTUREPICK_ARK_MODEL` | `arkModel` | `doubao-seed-2-1-lite` |
| 模拟模式 | `PICTUREPICK_BIRD_MOCK`（`1`/`true`/`yes`/`on` 开启；显式 `0`/`false`/`no`/`off` 关闭） | `mock` | `false` |

> 模拟模式的环境变量只要显式设置就优先于配置文件（含显式关闭：`PICTUREPICK_BIRD_MOCK=0`
> 可以覆盖 `settings.json` 里的 `"mock": true`）；未设置或为空时才读取配置文件。

> **端点语义（v5.1）**：配置值是**完整的 API 端点 URL**，后端直接向它 `POST`，**不做任何路径拼接**。
> 支持两种端点（按 URL 路径自动选择协议，二者互斥，不能嵌套）：
>
> - `https://ark.cn-beijing.volces.com/api/v3/chat/completions` → `endpointProtocol="chat"`（Chat Completions，默认）；
> - `https://ark.cn-beijing.volces.com/api/v3/responses` → `endpointProtocol="responses"`（Responses API）。
>
> 端点必须以 `/chat/completions` 或 `/responses` 结尾；`PUT /api/bird/config` 保存不符合的端点 →
> 400 `INVALID_CONFIG`（message 给出两种正确示例）。若配置文件/环境变量里残留旧 Base URL（如 `.../api/v3`），
> `GET /api/bird/status` 会原样返回该值 + `endpointProtocol=null` + `endpointWarning` 提示，
> 提交识别任务 → 400 `INVALID_CONFIG`；在设置页保存新端点后自动写为 `arkEndpoint` 并清除旧 `arkBaseUrl`。

**其余 AI 识鸟环境变量**（只用于测试/演示，一般无需设置）：

| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `PICTUREPICK_BIRD_MOCK_DELAY` | 模拟模式单任务耗时（秒），夹到 0–5 | `0.6` |
| `PICTUREPICK_BIRD_MOCK_FAIL` | `1` 时模拟任务直接失败（`BIRD_API_ERROR`），用于失败路径测试 | 关闭 |
| `PICTUREPICK_CONFIG_DIR` | 覆盖配置目录（测试隔离用；桌面/日常一般不用） | 平台默认 |

**配置文件位置**：

- Windows：`%APPDATA%\PicturePick\settings.json`（通常是
  `C:\Users\<用户名>\AppData\Roaming\PicturePick\settings.json`）；
- 其它平台：`~/.config/PicturePick/settings.json`（有 `XDG_CONFIG_HOME` 时为其下 `PicturePick/`）；
- 设置了 `PICTUREPICK_CONFIG_DIR` 时为其目录下的 `settings.json`。

JSON 字段：

```json
{ "arkApiKey": "ark-…", "arkEndpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions", "arkModel": "doubao-seed-2-1-lite", "mock": false }
```

- 写入方式：先写同目录临时文件再 `os.replace` 原子替换；目录/文件权限尽力收紧（0700/0600）。
- **兼容/迁移**：`PICTUREPICK_ARK_ENDPOINT` 优先于旧变量 `PICTUREPICK_ARK_BASE_URL`（值按端点解释，
  必须已含 `/chat/completions` 或 `/responses`）；配置文件字段 `arkEndpoint` 优先于旧 `arkBaseUrl`。
  `PUT /api/bird/config` 保存 `endpoint` 时写入 `arkEndpoint` 并删除旧 `arkBaseUrl`，避免双份配置。
  非 http(s) 开头的残留端点会被忽略并回退默认端点。
- 配置文件缺失/损坏/非对象 → 视为未配置，不阻塞启动；`PUT /api/bird/config` 写失败 → 400 `INVALID_CONFIG`。
- **安全**：API Key 只在后端使用，任何 API 响应都不返回明文 Key（只返回 `keyMasked`）；
  日志不得打印 Key。桌面端/浏览器端都只与本机后端通信。
- **环境变量优先于配置文件**：即使设置页保存成功，只要环境变量存在，`GET /api/bird/status`
  仍会显示环境变量的值（`keySource="env"`）。页面「AI 识鸟设置」入口 / `PUT /api/bird/config`
  只修改 `settings.json`，不会移除环境变量。
- 就绪判定：`configured = mock || key 非空`；未配置时点击识鸟按钮由前端引导打开设置弹窗。

## 7. PyInstaller 后端可执行文件

- 产物：`build/backend/picturepick-backend.exe`（onedir 目录形式，dist 目录 `build/backend/`）。
- 入口：`picturepick.cli:main`，打包后支持同样的 `serve` / `scan` / `version` 子命令。
- 打包数据：`picturepick/webui/**` 必须内嵌，运行时通过 `sys._MEIPASS/webui` 定位。
- 启动参数由桌面端传入：`serve --port <port> --no-browser --no-open ...`（`--no-open` 与 `--no-browser` 均为等价别名，至少支持 `--no-open`）。
- v5：`picturepick/birdid/` 子包**无需额外 hiddenimports**——`picturepick.app` 静态
  `from .birdid import BirdService`，PyInstaller 会随 import 自动收集（`desktop_webview/picturepick-desktop.spec`
  已把 `picturepick.app` 列入 hiddenimports）；birdid 只使用 stdlib（`urllib`、`json`、`threading` 等）
  与已有的 Pillow，不新增第三方依赖。

## 8. 浏览器端行为约定（第 3 版核心 + 第 5 版 AI 识鸟补充）

> 第 3 版核心原则：**浏览器只负责“选目录/选目标文件夹”，拿到绝对路径后交给本机后端；
> 浏览器绝不读取、遍历或上传照片文件，所有照片由后端扫描并通过 `/image` 按需提供。**
> 这样大目录也不会占满浏览器内存，照片数据也始终只在本机磁盘/本机服务之间流转。
>
> 第 5 版例外说明：用户主动点「AI 识鸟」时，**当前这一张照片**会由后端压缩后上传到火山方舟
> 用于识别（见 §9.7）；除此之外照片仍只在本机流转。

### 8.1 打开照片目录

- 页面上一个「选择照片文件夹」按钮（`data-testid="pick-folder-button"`），点击后按以下顺序获取**绝对目录路径**：
  1. 桌面版（WebView2 / Electron）：通过桌面宿主桥接调用**系统原生文件夹对话框**
     （`window.picturePickDesktop.pickDirectory()` 或 pywebview `create_file_dialog(FOLDER_DIALOG)`）；
  2. 浏览器模式：调用 `POST /api/dialog/pick-directory`，由本机 Python 后端弹出系统原生文件夹对话框
     （tkinter/Windows Shell 对话框；本机应用场景）；
  3. 自动化测试：若 `window.__picturepickTestSourceDir` 存在，直接使用该路径（不弹框）。
- 拿到路径后调用 `POST /api/sessions`（`source="server"`，`directory=路径`，`recursive=true`）创建会话；
  后端扫描目录、返回照片列表，前端通过 `/api/sessions/{sid}/photos/{pid}/image` 按需加载图片。
- **禁止**使用 `<input type="file" webkitdirectory>` 或 File System Access API 导入照片（那会把所有文件读入浏览器）；
  也**禁止**实现自定义目录浏览弹窗。
- 所有对话框都不可用（返回 501 `DIALOG_UNAVAILABLE`）时，提示用户改用
  `python -m picturepick serve --dir <目录>` 或桌面版，不得回退到自定义目录 UI。

### 8.2 一键导出「最后一轮保留的照片」

结果页提供按钮（`data-testid="export-photos"`），把 `PickResult.lastRound.pickedPhotoIds`
对应的照片**由后端复制**到用户指定的文件夹：

- 目标文件夹路径获取顺序与 8.1 相同：桌面宿主原生对话框 → `POST /api/dialog/pick-directory` →
  测试注入 `window.__picturepickTestExportDir`。
- 然后调用 `POST /api/sessions/{sid}/export-photos`（见 §4），后端在本机复制文件、处理同名冲突，
  返回复制数量与文件列表；前端显示进度（`data-testid="export-progress"`）与完成提示。
- 浏览器不下载/不上传照片本体；若用户想要浏览器下载方式，可继续使用「下载 JSON」。
- 自动化测试约定：`window.__picturepickTestExportDir` 为测试注入的目标目录路径，优先于真实对话框。

### 8.3 载入 JSON 恢复现场

- 启动页在「选择照片文件夹」之外提供「载入 JSON 恢复现场」按钮（`data-testid="restore-button"`）
  与隐藏的 `<input type="file" data-testid="restore-input" accept=".json,application/json">`。
- 用户选择之前保存的结果 JSON 后，前端读取文件文本、`JSON.parse`，调用
  `POST /api/sessions/restore`（请求体即该 JSON）。成功后进入**结果页**（`phase=finished` 的恢复会话）。
- 前端需校验基本结构（`schemaVersion`、`photos` 等）；错误（JSON 解析失败 / 版本不支持 /
  `INVALID_SOURCE` / `INVALID_PATH`）用中文 toast 提示。
- 结果页对恢复的会话显示提示（`data-testid="restore-notice"`），例如
  「已从 JSON 恢复现场：共 2 轮、最终保留 3 张；来源 E:\photos」。
- 结果页在「最后一轮保留照片 ≥ 2 张」且 `origin=server` 时显示「继续筛选」按钮
  （`data-testid="resume-button"`）→ `POST /api/sessions/{sid}/resume` → 进入选片（新一轮 = 原最后一轮保留照片）。
- 「下载 JSON」「导出保留照片」对恢复的会话照常可用。
- **v5**：恢复的会话若 JSON 带 `photos[].bird`，选片页左侧信息栏 / 结果页鸟种胶囊立即显示；
  再次浏览某张已识别照片时左栏直接显示鸟种（无需重新识别）。继续筛选（resume）后鸟种记录保留。
- 自动化测试约定：用 `page.setInputFiles('[data-testid="restore-input"]', <json 文件路径>)` 载入。

### 8.4 AI 识鸟（第 5 版新增）

接口细节见 §9；本节只约定**前端行为与 testid**。选片页保持既有 DOM/类名/快捷键不变，
新增内容如下：

**布局（三栏）**

```
header.topbar                      ← 新增「AI 识鸟」按钮 + AI 设置图标按钮
div.progress
main.workspace
  aside.photo-info                 ← 左：图片信息栏（含鸟种块 data-testid="bird-info"）
  section.stage                    ← 中：原 stage（viewer/nav/meta/overlay 原样）
  aside.bird-queue                 ← 右：AI 识鸟任务队列（data-testid="bird-queue"，可折叠）
footer.dock                        ← 原底部工具栏 + filmstrip 原样
```

- 底部工具栏、胶片条、缩放/全屏/结束本轮等既有功能与 testid 全部保留。
- 窗口宽 < 1180px 时左右两栏默认折叠为图标按钮；折叠状态存 `localStorage`
  键 `picturepick.birdPanels.v1`（`{"info": boolean, "queue": boolean}`），不影响功能。

**提交识别**

- 顶部按钮 `data-testid="bird-identify-button"`（`data-action="bird-identify"`），文案「AI 识鸟」+ `<kbd>B</kbd>`；
  旁边齿轮图标 `data-testid="bird-settings-button"`（`data-action="bird-settings"`）打开设置弹窗。
- **快捷键 `B`（大小写均可）**：对当前照片提交识别；弹窗打开或输入框等可编辑元素聚焦时不触发。
- 提交后 toast「已提交 AI 识鸟任务 · {文件名}」并刷新队列；同一照片已有 active 任务 → toast
  「该照片已在识别队列中」（后端也会复用该任务）；未配置（`birdConfigured=false`）→ toast 提示并
  自动打开设置弹窗；其它错误 → toast 后端 `message`。
- 提交**不阻塞**选片/自动放映/继续提交；浏览器前端不得在测试环境伪造识别结果。

**右侧任务队列**

- `data-testid="bird-queue"`；标题「AI 识鸟队列」+ 数量徽标 `data-testid="bird-queue-count"` +
  折叠按钮 `data-testid="bird-queue-toggle"`；空状态 `data-testid="bird-queue-empty"`。
- 任务卡片 `data-testid="bird-task-card"`（`data-task-id`、`data-status`、`data-stage`）：
  缩略图、文件名、状态胶囊（排队中/识别中/待确认/已采纳/已忽略/失败）、进度条 + 百分比/队列位置/耗时；
  完成时显示鸟种，失败时显示错误摘要。点击卡片打开结果/失败/进度模态框。
- 轮询：选片页挂载后每 **1000ms** `GET .../bird-tasks` 刷新；`document.hidden` 时暂停、重新可见立即刷新；
  `destroy()` 时停止并 abort 在途请求；轮询失败静默（不连续 toast），恢复后自动继续。
- 任务列表顺序由后端决定（running → queued → 其它最新在前，见 §9.1）。

**结果模态框**

- `data-testid="bird-result-modal"`（`openModal`，size `lg`）：
  - **顶部对照区**（`data-testid` 见下），左右并排：
    - 左：**原照片** `<img data-testid="bird-original-image">`，`src` 为
      `/api/sessions/{sid}/photos/{pid}/image?variant=original`，图注「原照片 · {文件名}」；
    - 右：**参考照片（AI 提供）** `<img data-testid="bird-reference-image">`，`src` = 任务 `referenceUrl`
      （`/reference` 代理）；无参考图/加载失败时占位显示 `result.referenceNote`（未开通联网搜索插件时
      为开通提示），并显示图注「参考照片（AI 提供）」。
  - 下方：种 `data-testid="bird-result-species"`、科 `data-testid="bird-result-family"`、
    属 `data-testid="bird-result-genus"`、拉丁名、置信度 `data-testid="bird-result-confidence"`、
    识别依据 `note`、备选 species、模型名。
  - 底部按钮：`data-testid="bird-adopt-button"`「采纳并写入 EXIF」、
    `data-testid="bird-dismiss-button"`「忽略」。
  - 失败任务：也显示**原照片**（单图对照区）+ `data-testid="bird-result-error"`（错误码 + 信息）+
    `data-testid="bird-retry-button"`「重新识别」+ 「忽略」；重新识别 = 对同一 photoId **新建**任务
    （失败的旧任务保留为 `failed`）。
  - 已采纳/已忽略任务：只读回看，按钮仅「关闭」。
  - `queued`/`running` 任务：显示**原照片** + 进度，按钮仅「关闭」（任务在后台继续）。
  - **关闭**：所有模态框右上角 X 按钮 `data-testid="modal-close"`（由 `modal.ts` 统一绑定点击关闭；
    此前漏绑导致点 X 无反应，v5.2 已修复），另外支持 `Esc` / 点击遮罩关闭。
- 采纳成功 toast：`已采纳：{species}（结果 JSON 已记录，照片 EXIF 已写入）`；
  `exif.written=false` 时 warn：`已采纳并记录到结果 JSON，但照片 EXIF 写入失败：{error}`。
  采纳后立即更新右侧卡片、左栏鸟种、当前照片数据与结果页计数。
- 忽略成功 toast「已忽略该识别任务」。

**左侧信息栏**

- 左栏容器 `data-testid="photo-info"`：文件名、路径、大小、尺寸、pick 状态（既有）。
- 鸟类识别块 `data-testid="bird-info"`：
  - 有 `photo.bird`：`data-testid="bird-info-species"` 显示 `种：白鹭（Egretta garzetta）`，
    并列出科/属（中文 + 拉丁）、`AI · 置信度 92% · 已写入 EXIF`（或 `EXIF 写入失败`，title 为原因）、
    参考照片来源行与模型名。
  - 无：`data-testid="bird-info-empty"` 显示「尚未识别 · 按 B 调用 AI 识鸟」。

**AI 设置弹窗**

- `data-testid="bird-settings-modal"`；入口两处（同一 testid `bird-settings-button` 均可用）：
  启动页底部「AI 识鸟设置」按钮 + 选片页顶部齿轮图标。
- 说明文案：调用火山方舟 `doubao-seed-2-1-lite`，**识别时照片会上传到火山方舟服务**；
  Key 仅保存在本机配置文件，接口响应不返回明文 Key。
- 字段：API Key 密码框 `bird-api-key-input`（已配置时占位显示掩码，留空 = 不修改）；
  高级（可折叠）：**API 端点（完整 URL）** `bird-endpoint-input`、模型名 `bird-model-input`；
  帮助文字给出两种端点示例（`.../chat/completions` 与 `.../responses`），并说明后端原样请求、不拼接；
  状态行 `bird-config-status`（已配置 / 未配置 / 模拟模式 / Key 来源 / 接口协议
  `Chat Completions` / `Responses API` / `端点无效` 及端点警告）；
  按钮 `bird-config-verify`「测试连接」、`bird-config-save`「保存」。
- 保存成功 toast「AI 识鸟配置已保存」并刷新选片页的配置缓存；测试连接 toast 成功/失败信息
  （弹窗保持打开，便于根据失败原因调整 Key / 模型名）。

**结果页**

- 摘要区新增一行 `data-testid="bird-identified-count"`：
  「AI 识鸟：{birdIdentifiedCount} 张已记录鸟种」；为 0 时显示「未使用 AI 识鸟」。
  优先使用结果 JSON 的 `birdIdentifiedCount`，老 JSON 无该字段时按 `photos[].bird` 计数兜底。
- 结果列表行若 `photo.bird` 存在：在文件名旁显示鸟种胶囊 `data-testid="result-bird-pill"`
  （显示 `species`，`title` 含科/属/拉丁名）。

**测试挂钩**

- 沿用 `window.__picturepickTestSourceDir` / `__picturepickTestExportDir`。
- e2e 使用后端 `PICTUREPICK_BIRD_MOCK=1` 跑模拟识别；前端不得在测试环境伪造识别结果。

## 9. AI 识鸟（第 5 版新增）

### 9.1 概览、状态机与队列语义

- **端点直连（v5.1）**：配置项是**完整的 API 端点 URL**，后端把请求 `POST` 到该 URL，**不做任何路径拼接**。
  按端点路径自动选择协议（二者互斥）：
  - 端点以 `/chat/completions` 结尾 → **Chat Completions**：请求体 `model` / `messages` /
    `temperature=0.1` / `max_tokens=1200`；响应取 `choices[0].message.content`。
  - 端点以 `/responses` 结尾 → **Responses API**：请求体 `model` / `instructions`（系统提示词）/
    `input: [{role:"user", content:[{type:"input_image", image_url:"data:..."}, {type:"input_text", text}]}]` /
    `temperature=0.1` / `max_output_tokens=1200`；响应取 `output[].content[].text`（只拼接 `type=="message"` 的条目）。
  - 图片由后端用 Pillow `exif_transpose` 转正、长边压到 ≤ 1568px、JPEG q88 后以 base64 data URL 发送。
  - 两种协议都带 `Authorization: Bearer <key>`、`Content-Type: application/json`。
  - 两种协议默认都带 `"thinking": {"type": "disabled"}`（若被模型以 400 拒绝则去掉后重试一次）。
  - 超时 90s；对超时/HTTP 429/5xx 重试一次（间隔 1.5s）；401/403/404 不重试，任务直接失败，
    错误信息包含**实际请求地址**并提示检查 Key / 端点 / 模型名。
  - 端点不受支持（`endpointProtocol=null`）时：`POST .../bird-tasks` → 400 `INVALID_CONFIG`；
    `POST /api/bird/verify` → 200 + `ok=false` + 端点警告。
  - **参考照片由模型 API 提供（v5.2）**：Responses API 请求自动带 `tools:[{"type":"web_search"}]`，
    提示词要求模型联网检索真实来源页面并给出**真实可访问的图片直链**；后端只下载模型给出的 URL
    （不搜索图源、不生成图片）。Chat Completions 协议不带 tools（模型可能凭知识给出 URL，但不保证可访问）。
  - **联网搜索插件未开通（`ToolNotOpen`）自动降级**：API 返回该错误时去掉 `tools` 重试一次，
    识鸟仍会成功；参考图缺失时任务结果的 `referenceNote` 带开通提示（含
    `https://console.volcengine.com/common-buy/CC_content_plugin`），前端参考图位置显示该提示。
  - 模型输出仍由容错解析器提取 JSON（兼容 Markdown 围栏/前后说明）；解析失败 → 任务失败 `BIRD_PARSE_ERROR`。
- 任务由**单线程 worker 顺序处理**（一次只识别一张，对限流友好）；提交后立即返回，不阻塞选片。
- 任务队列保存在**后端进程内存**中：后端重启后队列清空（已采纳结果已写入 JSON/EXIF，不受影响）。

**状态机**

```
queued ──▶ running ──▶ completed ──▶ adopted
                 └──▶ failed      └──▶ dismissed
```

- `queued`：已提交未开始；`running`：worker 正在处理（`stage` 细分）；
  `completed`：识别成功，待用户确认；`failed`：识别失败（可重新提交同照片任务重试）；
  `adopted`：用户采纳（写 JSON/会话/EXIF）；`dismissed`：用户忽略。
- 采纳/忽略后任务保留在列表（历史），供回看。
- **忽略**允许用于 `queued`/`running`/`completed`/`failed`（识别中的任务结果会被丢弃）；
  **已采纳**（`adopted`）任务不能忽略（409 `BIRD_TASK_ADOPTED`）。
- 同一照片存在 `queued`/`running` 任务时重复提交**复用**该任务（不新建、不报错，仍返回 201 + 该任务）。
- `stage` 与进度：`queued` → `preparing`（约 10%）→ `identifying`（20%，按已用时间向 85% 插值，
  单任务估算 15s）→ `reference`（90%）→ `done`（100%）。失败时保留最后 `stage`，`progress` 停住。
- `queuePosition`（1-based，仅 queued 有）：在 queued 任务中的顺序（1 = 下一个执行）；其它状态为 `null`。
- `elapsedMs`：running 时为「已运行毫秒」，结束态为总耗时。
- 会话结束（`phase=finished`）后不再接受**新**任务（409 `PHASE_FINISHED`）；已存在的任务继续跑完，
  仍可查看/采纳（采纳会使已结束会话的结果缓存失效，结果 JSON/EXIF 立即更新）。
- 会话过期（24h）后任务随之失效：worker 找不到会话 → 任务失败，`error.code="SESSION_NOT_FOUND"`。

**列表顺序**

`running`（最早开始优先）→ `queued`（提交顺序）→ 其它（`createdAt` 倒序，最新在前）。

### 9.2 配置接口

#### `GET /api/bird/status`

```json
{
  "configured": true,
  "model": "doubao-seed-2-1-lite",
  "endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
  "endpointProtocol": "chat",
  "endpointWarning": null,
  "keyMasked": "ark-…1a2b",
  "keySource": "env",
  "mock": false,
  "queue": { "queued": 0, "running": 0 }
}
```

- 永远 200（未配置也是 200）。
- `endpoint`：当前生效的**完整 API 端点 URL**（环境变量优先于配置文件；默认
  `https://ark.cn-beijing.volces.com/api/v3/chat/completions`）。
- `endpointProtocol`：`"chat"`（端点以 `/chat/completions` 结尾）| `"responses"`（端点以 `/responses` 结尾）
  | `null`（端点不是受支持的完整接口 URL）。
- `endpointWarning`：端点无效时的中文提示（含两种正确示例与当前值）；正常为 `null`。
  端点无效时状态仍 200，但 `POST .../bird-tasks` 会返回 400 `INVALID_CONFIG`。
- `keySource`：`"env"`（环境变量）| `"config"`（settings.json）| `"mock"`（模拟模式且无 Key）| `null`（未配置）。
- `keyMasked`：掩码后的 Key（`ark-…abcd`），未配置时为 `null`；**任何响应都不返回明文 Key**。
- `queue`：当前进程内全部会话的排队/识别中任务数。

#### `PUT /api/bird/config`

请求（字段均可缺省；缺省 = 保持；显式 `null` / 空字符串 = 清除该字段）：

```json
{
  "apiKey": "ark-xxx",
  "endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
  "model": "doubao-seed-2-1-lite",
  "mock": false
}
```

- 响应：同 `GET /api/bird/status`（200）。
- `endpoint` 必须是**完整接口 URL**：`https://.../chat/completions`（Chat Completions）或
  `https://.../responses`（Responses API），后端原样使用、不拼接；空字符串清除后回退默认端点。
- 校验失败 → 400 `INVALID_CONFIG`（`message` 说明原因并给出两种端点示例）：端点不是受支持的完整 URL
  （含只填 Base URL、双后缀等）、`mock` 不是布尔值（不接受 `"yes"`/`"on"` 等字符串）、配置文件写入失败。
- 保存只修改本机 `settings.json`（§6.2，写 `arkEndpoint` 并清除旧 `arkBaseUrl`）；环境变量仍然优先，
  `mock=true` 时即使无 Key 也 `configured=true`。

#### `POST /api/bird/verify`

用当前配置发一个极小的真实请求（text-only）验证 Key / 端点 / 模型 / 网络是否可用：Chat Completions 用
`messages` + `max_tokens=16`，Responses API 用 `input` + `max_output_tokens=16`。

```json
{ "ok": true, "message": "连接成功（doubao-seed-2-1-lite · Chat Completions）", "latencyMs": 812, "model": "doubao-seed-2-1-lite" }
```

- 失败也返回 200：`{ "ok": false, "message": "方舟接口返回 HTTP 401：…（请求地址：https://…）", "latencyMs": null, "model": "…" }`。
- 未配置：`{ "ok": false, "message": "尚未配置 ARK API Key（或开启模拟模式）", "latencyMs": null, "model": "…" }`。
- 端点无效：`{ "ok": false, "message": "<端点警告>", "latencyMs": null, "model": "…" }`。
- 模拟模式：直接返回 `{ "ok": true, "message": "模拟模式：无需真实连接", "latencyMs": 0, "model": "…" }`。

### 9.3 任务接口

#### `POST /api/sessions/{sid}/bird-tasks`

```json
{ "photoId": "a1b2c3d4e5f60718" }
```

- 201 返回 `BirdTask`（`status="queued"`，`queuePosition` 为当前排队位）；复用已有 active 任务时也返回 201 + 该任务。
- 仅 `origin=server` 会话可用。
- 错误：`SESSION_NOT_FOUND` 404、`PHOTO_NOT_FOUND` 404（照片不属于会话或文件不存在）、
  `PHASE_FINISHED` 409、`INVALID_SOURCE` 400（browser 会话）、`BIRD_NOT_CONFIGURED` 409
  （未配置 Key 且非模拟模式）、`INVALID_CONFIG` 400（配置的端点不是受支持的完整接口 URL）。

#### `GET /api/sessions/{sid}/bird-tasks`

```json
{
  "tasks": [ BirdTask, ... ],
  "queue": { "queued": 1, "running": 1 }
}
```

- 包含历史任务（adopted/dismissed/failed），顺序见 §9.1；前端据此轮询刷新队列。
- 错误：`SESSION_NOT_FOUND` 404。

#### `GET /api/sessions/{sid}/bird-tasks/{tid}`

返回单个 `BirdTask`。错误：`SESSION_NOT_FOUND` 404、`BIRD_TASK_NOT_FOUND` 404（任务不存在或不属于该会话）。

#### `POST /api/sessions/{sid}/bird-tasks/{tid}/adopt`

```json
{
  "task": BirdTask,
  "observation": BirdObservation,
  "exif": { "written": true, "path": "E:\\photos\\a.jpg", "error": null }
}
```

- 写照片 EXIF + 记录到会话（`PhotoItem.bird` / 结果 JSON 的 `birdIdentifiedCount` 与 `photos[].bird`
  立即生效）；任务 → `adopted`。
- 对已 `adopted` 任务再次 adopt：用当前结果覆盖（幂等更新），重复写 EXIF。
- 错误：`BIRD_TASK_NOT_FOUND` 404、`BIRD_TASK_NOT_COMPLETED` 409（queued/running/dismissed/failed）、
  `BIRD_RESULT_UNAVAILABLE` 409（`identified=false`）、`PHOTO_NOT_FOUND` 404、`INVALID_SOURCE` 400。
- **EXIF 写失败不算 HTTP 错误**：返回 200 + `exif.written=false` + `exif.error`（中文原因），
  JSON/会话仍记录该识别。

#### `POST /api/sessions/{sid}/bird-tasks/{tid}/dismiss`

响应 `BirdTask`（`status="dismissed"`，幂等）。错误：`BIRD_TASK_NOT_FOUND` 404、
`BIRD_TASK_ADOPTED` 409（已采纳不能忽略）。

#### `GET /api/sessions/{sid}/bird-tasks/{tid}/reference`

- 200：`image/jpeg`，`Cache-Control: no-store`（参考图由后端代理，前端不直连外网图片）。
- 404：`BIRD_REFERENCE_UNAVAILABLE`（无参考图、任务未完成或参考图获取失败）。

### 9.4 参考照片（由模型 API 提供，不落盘）

参考照片**严格由大模型 API 提供**：后端不做任何图片搜索/生成，只处理模型识别结果里的
`referenceImageUrl`（真实可访问的图片直链）。

1. **模型联网检索**：Responses API 请求带 `tools:[{"type":"web_search"}]`，系统提示词要求模型
   联网检索该鸟种的真实来源页面，并在 `referenceImageUrl` 中给出真实图片直链；
   若无法确认则必须留空（不允许编造）。`referenceSource` 填来源站点名，`referenceNote` 可写来源页面。
2. **后端下载**：对 `referenceImageUrl` 做 SSRF 校验 → 下载 → Pillow 缩放（长边 ≤1000、JPEG q85）
   → 仅存任务内存；失败/无 URL → 任务 `referenceAvailable=false`、`referenceUrl=null`，识别任务仍成功。
   **维基/维基媒体域名（`upload.wikimedia.org`、`wikipedia.org`、`commons.wikimedia.org`）在中国大陆
   不可访问，后端直接视为不可用**，不会下载或显示。
3. **首轮参考图不可用时二次请求模型**：若首轮没有参考图或只给了维基链接，后端会**再向模型请求一次**
   「中国境内可访问的真实图片文件直链」（Responses API 同样带 `web_search`；仍是模型 API 提供，
   本机不搜索图库），拿到后重新下载；仍失败才显示「未获取到参考照片」。
4. **`ToolNotOpen` 降级**：账号未开通方舟「联网内容插件」时 API 报 `ToolNotOpen`（或同类联网搜索不可用
   错误），后端自动去掉 `tools` 重试，识鸟仍成功；此时通常没有参考图，任务结果 `referenceNote` 写入
   开通提示（含 `https://console.volcengine.com/common-buy/CC_content_plugin`），前端参考图位置展示。
   开通插件后**无需改代码/重装**，重新识别即可由模型提供真实参考图。
5. **本机实测（插件已开通）**：识别请求带 `tools:[{"type":"web_search"}]` 正常执行、`web_search_error`
   为空；模型联网检索返回真实参考图 URL（示例
   `https://images.vliz.be/thumbs/64745_little-egret-egretta-garzetta.jpg?w=700`，来源 WoRMS/VLIZ），
   后端 SSRF 校验后下载成功 `image/jpeg` **32KB / 2.09s**；真实 UI 全流程 15/15 通过。
6. 参考图**按鸟种内存缓存**（`species` + `speciesLatin` 为键，最多 16 条），同一鸟种再次识别不重复下载；
   不落盘、不写入结果 JSON。
7. `/reference` 代理返回 `image/jpeg` + `Cache-Control: no-store`；无参考图 → 404
   `BIRD_REFERENCE_UNAVAILABLE`，模态框显示 `referenceNote` 提示或「未获取到参考照片」。

- 下载上限 15MB、超时 6s；只接受 `image/*`。
- **防 SSRF**：外部图片 URL 必须是 `http(s)`、无 userinfo、非 `localhost`/`*.local`/`*.internal`，
  且 IP 直连必须是公网地址；不安全 URL 直接放弃（不影响识别任务）。
- 参考图只保存在**任务内存**（`bytes`），**绝不写入磁盘**，任务对象/结果 JSON 不包含图片数据；
  前端通过 `GET .../reference` 代理读取，不直连外网图片。
- 模拟模式下参考图由 Pillow 在内存生成 800×600 示意图（仅演示界面用）。
- 隐私：参考图 URL 由**模型 API**给出，联网检索在方舟侧完成；本机只下载该 URL，不上传照片。

### 9.5 EXIF 写入规则

采纳时写**原照片文件**（不复制、不重编码，图像数据字节完全不变）：读取 JPEG 的 APP1(Exif) 段，
用 Pillow `Image.Exif` 重新序列化（保留 IFD0 / Exif IFD / GPS / MakerNote 已有标签；仅可能丢弃内嵌
JPEG 缩略图预览），在内存校验新文件可正常打开后，写同目录临时文件 + `os.replace` **原子替换**；
替换后恢复原文件的 `mtime/atime`。

| 标签 | 值 |
| --- | --- |
| `0x010E ImageDescription` (ASCII) | `Bird: {speciesLatin}; Family: {familyLatin}; Genus: {genusLatin}`（拉丁名都缺失时用 `PicturePick AI bird identification`） |
| `0x9C9B XPTitle` (UTF-16LE bytes) | `{species} {speciesLatin}` |
| `0x9C9E XPKeywords` (UTF-16LE bytes) | `鸟类;{family};{genus};{species};{speciesLatin}` |
| `0x9C9F XPSubject` (UTF-16LE bytes) | `{family} {genus} {species}` |
| `0x9C9C XPComment` (UTF-16LE bytes) | `PicturePick AI 识别（{model}，置信度 {pct}%）`（置信度未知时省略该段） |

- 内部返回 `ExifOutcome(written: bool, path: str, error: str | null, warning: str | null)`：
  `error` **只在 `written=false`（失败）时给出**；文件不可写/被占用/非 JPEG/EXIF 过大/校验失败等 →
  `written=false` + 中文 `error`，**不抛异常到 API 层**。
- 序列化异常时降级：用只含上述标签的新 Exif，仍尝试写入；此时 `written=true`、`error=null`，
  降级原因只进内部 `warning`（如原 EXIF 读取/序列化失败、原 EXIF 未完整保留）。
- **API 的 adopt 响应 `exif` 只暴露 `written` / `path` / `error` 三个字段，不暴露 `warning`**；
  前端依据 `written` 显示「已写入 EXIF」/「EXIF 写入失败」，降级成功视为成功。
- 照片路径必须 `is_within(session.root, path)` 且是 `.jpg/.jpeg` 普通文件；EXIF 数据超过 JPEG APP1
  上限（约 64KB）时放弃写入并返回中文错误。
- XP* 标签为 Windows 资源管理器「属性 → 详细信息」可读的 UTF-16LE 备注；其它系统/软件可能忽略。

### 9.6 模拟模式（e2e / 演示 / 无 Key 开发）

- `PICTUREPICK_BIRD_MOCK=1`：使用 `MockBirdClient`，**不访问网络**；`configured=true`，无需 Key。
- `PICTUREPICK_BIRD_MOCK_DELAY`：单任务模拟耗时秒数，默认 `0.6`，夹到 0–5。
- 确定性结果：按 `photoId` 的 sha1 取模从下表取值，`confidence = 0.80 + 0.03 * (hash % 5)`
  （即 0.80–0.92）；`note` 末尾带「（模拟识别结果，仅用于测试）」；`referenceSource = "Mock Reference"`。

| # | family | familyLatin | genus | genusLatin | species | speciesLatin | commonNameEn |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 鹭科 | Ardeidae | 白鹭属 | Egretta | 白鹭 | Egretta garzetta | Little Egret |
| 1 | 鹭科 | Ardeidae | 鹭属 | Ardea | 苍鹭 | Ardea cinerea | Grey Heron |
| 2 | 鹭科 | Ardeidae | 夜鹭属 | Nycticorax | 夜鹭 | Nycticorax nycticorax | Black-crowned Night Heron |
| 3 | 䴙䴘科 | Podicipedidae | 小䴙䴘属 | Tachybaptus | 小䴙䴘 | Tachybaptus ruficollis | Little Grebe |
| 4 | 翠鸟科 | Alcedinidae | 翠鸟属 | Alcedo | 翠鸟 | Alcedo atthis | Common Kingfisher |

- mock 参考图：Pillow 内存生成 800×600 JPEG（深色渐变 + 文字 `MOCK REFERENCE` / `{speciesLatin}`），
  `referenceAvailable=true`。
- 文件名（不含扩展名）含 `notbird` → 返回 `identified=false`（用于无鸟/不可采纳路径测试）。
- `PICTUREPICK_BIRD_MOCK_FAIL=1` → 任务直接失败 `BIRD_API_ERROR`，`message` 含「模拟失败」；
  `POST /api/bird/verify` 返回 `ok=false`。

### 9.7 兼容性与限制

- **隐私**：真实识别模式下，被识别的**当前照片**（压缩后的 JPEG）会上传到火山方舟服务；
  参考照片 URL 由**模型 API 联网检索**后提供，检索请求在方舟侧完成，后端只下载该 URL（SSRF 校验后
  仅存内存）；用户未主动点「AI 识鸟」时不会上传任何照片。
- **联网搜索插件**：参考照片依赖火山方舟「联网内容插件」（Responses API 的 `web_search` 工具）；
  **本机已开通并实测可用**（模型返回真实参考图 URL，下载 32KB/2.09s）。未开通时识鸟仍成功但参考图为空，
  任务结果 `referenceNote` 给出开通链接；开通后无需改代码。
  Chat Completions 端点不带联网搜索工具（模型可能凭知识给出 URL，但不保证可访问）。
- API Key 以明文保存在本机配置文件（或使用环境变量）；`GET /api/bird/status` 只返回掩码；
  请勿在不可信机器上填写 Key，不要提交 `settings.json` 到代码仓库。
- 旧结果 JSON（无 `bird`）可恢复；新 JSON（有 `bird`）旧版本应用会忽略新增字段（`schemaVersion` 仍为 1）。
- **端点配置兼容（v5.1）**：v5.0 的环境变量 `PICTUREPICK_ARK_BASE_URL` 与配置字段 `arkBaseUrl` 仍被读取，
  但其值现在按**完整端点**解释；只填 Base URL（如 `.../api/v3`）会得到 `endpointProtocol=null` +
  `endpointWarning`，提交任务被 400 `INVALID_CONFIG` 拒绝，请在设置页改为完整端点
  （`.../chat/completions` 或 `.../responses`）。保存后自动迁移为 `arkEndpoint` 并清除旧字段。
- 参考照片不落盘；任务队列在内存，**后端重启后队列清空**（已采纳结果已写入 JSON/EXIF）。
- 单 worker 顺序处理：一次只识别一张（对限流友好）；排队任务显示队列位置。
- EXIF 写入会修改原图元数据（**不重编码图像数据**，不改变像素）；建议用户自行备份原片；
  部分云盘/只读目录/被占用文件会写入失败，此时仅记录到 JSON。
- 识别结果仅供参考，**不作为物种鉴定依据**；模型可能给出低置信度结果或错误分类。
- 不新增第三方依赖（stdlib `urllib` + 已有的 Pillow）；桌面端/浏览器端都只与本机后端通信。
- 非目标（本期不做）：凭任务结果自动批量识别全部照片；把参考照片保存到磁盘；把识别结果写入
  XMP/其他元数据；云端同步。
