# PicturePick AI 识鸟（Bird ID）实现契约（v5.2，已实现并验收）

> 本文件是「AI 识鸟」功能的**实现契约与验收记录**，与 `docs/API.md`（第 5.2 版，正式契约）
> 一致；后端/前端/测试/文档都按本文件实现。
>
> v5.1 变更：配置项改为**完整 API 端点 URL**（后端原样 POST、不拼接，支持 Chat Completions 与
> Responses API 两种协议）。
> v5.2 变更：**参考照片严格由大模型 API 提供**（移除必应/维基等后端搜索兜底；Responses API 带
> `web_search` 工具，`ToolNotOpen` 时自动降级）；结果模态框改为**原照片 + 参考照片并排**，
> 并修复所有模态框右上角 X 无法关闭的 BUG。

## 1. 目标与范围

在选片页新增 **AI 识鸟**：调用火山方舟（Volcengine Ark）`doubao-seed-2-1-lite`
多模态模型识别照片中的鸟，返回 **科 / 属 / 种**（中文名 + 拉丁学名）与参考照片；
用户确认采纳后写入 **结果 JSON** 与 **照片 EXIF**。

关键交互（用户需求原文）：

1. 选片页提供按钮，快捷键 `B`（bird）；
2. 点击后调用 doubao 2.1 lite 识别；
3. 返回科、属、种，用户确认后写入 JSON 与照片 EXIF；
4. AI 需给出同类鸟类的参考照片供对照，**参考照片不落盘存储**；
5. **后台任务**：提交后可以继续选片、也可以继续提交任务；未处理任务以**队列**形式存在
   **右侧边栏**并显示进度；点击任务卡片可查看结果并确认是否采纳；
6. 若一张照片已识别过，再次浏览时应在**图片左侧信息栏**显示鸟种。

非目标（本期不做）：凭任务结果自动批量识别全部照片；将参考照片保存到磁盘；
把识别结果写入 XMP/其他元数据；云端同步。

## 2. 配置（API Key / API 端点 / 模型）

### 2.1 解析优先级

| 配置项 | 环境变量（优先） | 配置文件字段 | 默认值 |
| --- | --- | --- | --- |
| API Key | `PICTUREPICK_ARK_API_KEY`，其次 `ARK_API_KEY` | `arkApiKey` | 无 |
| API 端点 | `PICTUREPICK_ARK_ENDPOINT`（兼容旧 `PICTUREPICK_ARK_BASE_URL`，值按端点解释） | `arkEndpoint`（兼容旧 `arkBaseUrl`，只读） | `https://ark.cn-beijing.volces.com/api/v3/chat/completions` |
| 模型名 | `PICTUREPICK_ARK_MODEL` | `arkModel` | `doubao-seed-2-1-lite` |
| 模拟模式 | `PICTUREPICK_BIRD_MOCK`（显式设置即覆盖，含 `0/false` 关闭） | `mock` | `false` |

- **端点语义（v5.1）**：配置值是**完整 API 端点 URL**，后端直接向它 `POST`，**不做任何路径拼接**。
  支持两种协议，按端点路径自动选择：以 `/chat/completions` 结尾 → Chat Completions；
  以 `/responses` 结尾 → Responses API；其它（只填 Base URL、双后缀等）→ 无效：
  `GET /api/bird/status` 返回 `endpointProtocol=null` + `endpointWarning`，
  提交任务返回 400 `INVALID_CONFIG`。
- 默认模型：`doubao-seed-2-1-lite`（火山方舟控制台模型名；如账号要求填 endpoint id / 版本别名，
  可用 `PICTUREPICK_ARK_MODEL` / 设置页覆盖）。
- 请求地址：**配置的完整端点**，`Authorization: Bearer <key>`（见 §4.1）。
- **配置文件位置**：`PICTUREPICK_CONFIG_DIR` 指定的目录（测试使用），否则
  Windows `%APPDATA%\PicturePick\settings.json`，其它平台 `~/.config/PicturePick/settings.json`。
  JSON 字段：`{"arkApiKey": "...", "arkEndpoint": "...", "arkModel": "...", "mock": false}`；
  旧字段 `arkBaseUrl` 仍读取，`PUT /api/bird/config` 保存时迁移为 `arkEndpoint` 并清除旧字段。
- 写入：先写同目录临时文件再 `os.replace`；目录/文件权限尽力收紧（0700/0600）。
- **安全**：API Key 只在后端使用，任何 API 响应都不返回明文 Key（只返回 `keyMasked`）；
  日志不得打印 Key。桌面端/浏览器端都只与本机后端通信。
- **配置文件缺失/损坏** → 视为未配置，不阻塞启动。

### 2.2 就绪判定

`configured = mock || (key 非空)`。未配置时点击识鸟按钮由前端引导打开设置弹窗。

## 3. 后端模块划分（建议）

```
backend/picturepick/birdid/
├── __init__.py       # 对外导出 BirdService / build_client / BirdConfig
├── config.py         # 完整端点校验/配置读取/写入/掩码；BirdConfig dataclass
├── prompt.py         # 系统提示词 + 用户提示词
├── parser.py         # 模型输出 → BirdResult（容错 JSON 提取/校验）
├── client.py         # ArkBirdClient（urllib 双协议 + web_search/ToolNotOpen 降级）+ MockBirdClient
├── reference.py      # 参考照片：仅用模型 API 提供的真实图片 URL，下载/SSRF/缩放（内存，不落盘）
├── exif.py           # 无损写 JPEG EXIF（Pillow 序列化 + APP1 原位替换）
├── tasks.py          # BirdTask 数据、任务仓库、单线程 worker、进度/队列位置
└── service.py        # BirdService：配置 + 创建任务 + 采纳 + EXIF 写盘 + 会话联动 + 参考图缓存/提示
```

- **不新增第三方依赖**（用 stdlib `urllib.request`；Pillow 已有）。
- 任务 worker 为**单线程 daemon**（顺序处理，队列位置/进度语义清晰）；进程退出不阻塞。
- 参考照片只保存在任务内存（`bytes`），**不写磁盘**；任务对象不进入结果 JSON。

## 4. 识别调用规格

### 4.1 请求

```http
POST <配置的完整端点>            # 原样请求，不拼接
Authorization: Bearer <key>
Content-Type: application/json
```

**A. 端点以 `/chat/completions` 结尾（Chat Completions，默认）**

```json
{
  "model": "doubao-seed-2-1-lite",
  "messages": [
    {"role": "system", "content": "<prompt.py 中的系统提示词>"},
    {"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<...>"}},
      {"type": "text", "text": "请识别这张照片中的鸟类，只输出约定的 JSON。"}
    ]}
  ],
  "temperature": 0.1,
  "max_tokens": 1200
}
```

响应：`choices[0].message.content`（字符串）即模型输出；不依赖 `response_format`。

**B. 端点以 `/responses` 结尾（Responses API）**

```json
{
  "model": "doubao-seed-2-1-lite",
  "instructions": "<prompt.py 中的系统提示词>",
  "input": [
    {"role": "user", "content": [
      {"type": "input_image", "image_url": "data:image/jpeg;base64,<...>"},
      {"type": "input_text", "text": "请识别这张照片中的鸟类，只输出约定的 JSON。"}
    ]}
  ],
  "temperature": 0.1,
  "max_output_tokens": 1200,
  "tools": [{"type": "web_search"}]
}
```

- `tools:[{"type":"web_search"}]` 用于让模型联网检索真实参考图来源；账号未开通「联网内容插件」时
  API 返回 `ToolNotOpen`（或同类联网搜索不可用错误），客户端**自动去掉 tools 重试一次**，识鸟仍成功
  （参考图通常为空）。Chat Completions 端点不带此工具。

响应：拼接 `output[]` 中 `type=="message"` 条目的 `content[].text`（字符串）即模型输出。

- 图片预处理：Pillow 打开 → `ImageOps.exif_transpose` → 转 RGB → 长边压到 ≤ 1568px
  → JPEG quality 88 → base64（控制请求体大小）。
- 若请求体带 `"thinking": {"type": "disabled"}` 被模型拒绝（HTTP 400 且信息含
  `thinking`/`unknown`），自动去掉该字段重试一次（默认带上以降低延迟）。
- 超时 90s；对 `Timeout`、HTTP 429、5xx 做一次重试（间隔 1.5s）。401/403/404 不重试，
  直接任务失败（错误信息提示检查 Key / 端点 / 模型名，并带实际请求地址）。
- 端点无效（`endpointProtocol=null`）→ 提交任务 400 `INVALID_CONFIG`；verify 返回 ok=false + 端点警告。

### 4.2 Prompt（system，原文需实现为 `prompt.py` 常量）

```
你是资深鸟类识别专家。请根据照片识别画面中的鸟，并按下面的 JSON 结构输出。
只输出 JSON 对象本身，不要输出 Markdown 代码块、解释或多余文字。

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
  "note": "一句话识别依据（羽色/喙/腿/体型等）",
  "alternatives": [
    {"species": "黄嘴白鹭", "speciesLatin": "Egretta eulophotes", "confidence": 0.04}
  ],
  "referenceImageUrl": "https://真实图片直链.jpg",
  "referenceSource": "来源站点名称",
  "referenceNote": "该参考照片的说明/来源页面"
}

规则：
1. family/genus/species 用规范中文名，*Latin 字段用拉丁学名；无法确定拉丁名时留空字符串。
2. confidence 为 0–1 之间的小数；不确定时给出最可能结果并降低 confidence，可附 1–2 个 alternatives。
3. 如果画面中没有鸟、或照片无法判断，返回 {"identified": false, "reason": "原因"}。
4. **参考照片**：referenceImageUrl 必须是「真实存在、可直接访问的图片文件直链」（以 .jpg/.jpeg/.png/.webp
   结尾，或明确返回图片内容的 URL），内容必须是该鸟种的真实照片；**优先使用联网搜索得到的真实来源页面中的
   图片直链**。参考来源页面填 referenceSource（如「维基百科」「中国鸟类图库」等），referenceNote 可写来源页面 URL。
   如果无法确认真实图片直链，referenceImageUrl 必须留空字符串，**绝对不要编造或猜测 URL**。
5. 所有字段都必须出现；不要输出除 JSON 以外的任何内容。
```

### 4.3 输出解析（`parser.py`，必须容错）

- 去掉 ``` / ```json 围栏；截取第一个 `{` 到最后一个 `}`；`json.loads`。
- `identified == false` → `BirdResult(identified=false, reason=...)`（`reason` 必填，缺失给默认文案）。
- `identified` 非 bool 时按 `true` 处理（只要 species 非空）。
- 清洗：字符串 strip 并截断 ≤ 200 字符；`confidence` 夹到 `[0,1]`，非数字 → `null`；
  `alternatives` 只保留 dict 且 species 非空，最多 3 条。
- 解析失败 → 任务失败 `BIRD_PARSE_ERROR`（message 带模型输出前 200 字）。
- 合法 JSON 但 `identified=true` 且 `species` 为空 → 视为失败 `BIRD_PARSE_ERROR`。

### 4.4 参考照片（`reference.py`，由模型 API 提供 URL，不落盘）

1. 参考照片**严格由大模型 API 提供**：Responses API 带 `tools:[{"type":"web_search"}]`，系统提示词要求
   模型联网检索真实来源页面，并在 `referenceImageUrl` 中给出**真实可访问的图片直链**（无法确认时留空，
   不得编造）。后端**不做图片搜索/生成**，只处理该 URL。
   提示词明确**禁止 Wikimedia/Wikipedia 链接**；后端也把 `upload.wikimedia.org`、`wikipedia.org`、
   `commons.wikimedia.org` 视为不可用（中国大陆不可访问），不会下载/显示。
2. 后端：SSRF 校验（仅公网 http(s)、无 userinfo、拒绝 localhost/内网）→ 下载（上限 15MB、超时 6s，
   只接受 `image/*`）→ Pillow 缩到长边 ≤ 1000px、JPEG q85 → 存 `bytes` 于任务内存（**不落盘**）。
3. **首轮无参考图时二次请求模型**：若首轮 URL 缺失/被拒绝/下载失败，worker 会调用
   `ArkBirdClient.request_reference_url()` 再向模型要一次「中国境内可访问的真实图片文件直链」
   （同样带 `web_search`；仍是模型 API 提供），再下载一次；仍失败才置空。
4. 参考图按鸟种（species/speciesLatin）内存缓存（≤16 条），同一鸟种再次识别不重复下载。
5. `ToolNotOpen` 降级（**本机已开通并实测可用**）：账号未开通方舟「联网内容插件」时自动去掉 tools
   重试，识鸟仍成功；参考图缺失时 `result.referenceNote` 写入开通提示（含
   `https://console.volcengine.com/common-buy/CC_content_plugin`），前端参考图位置显示该提示。
   开通后无需改代码。实测开通后：模型联网返回真实参考图 URL（来源 WoRMS/VLIZ），后端下载成功
   `image/jpeg` 32KB / 2.09s，真实 UI 全流程 15/15。
6. 参考图下载失败/无 URL 不影响识别任务成功：任务 `referenceAvailable=false`、`referenceUrl=null`，
   模态框显示 `referenceNote` 提示或「未获取到参考照片」。
7. 提供 `GET .../reference` 代理返回图片字节（`Cache-Control: no-store`），
   前端不直连外网图片（避免 CORS/防盗链）。
8. **网页地址拒绝**：模型给出的 URL 若是网页（`.htm/.html` 结尾或含 `/wiki/`、`/item/`、`/cpjc/`），
   后端不下载，直接加入 `rejected` 并以「不要重复」提示让模型重新检索（最多再请求 2 次）；
   仍无图片文件直链 → 无参考图，`referenceNote` 写「AI 未能提供可访问的参考图片文件直链」。
9. **AI 参考链接兜底**：模型提供的 URL 始终保留在 `BirdResult.referencePageUrl`（网页或图片直链）；
   前端在参考图缺失时渲染可点击链接 `bird-reference-link`（新标签、`noopener`），用户可打开确认。

## 5. 任务队列

### 5.1 状态机

```
queued ──▶ running ──▶ completed ──▶ adopted
                  └──▶ failed      └──▶ dismissed
```

- `queued`：已提交未开始；`running`：worker 正在处理（stage 细分）；
  `completed`：识别成功，待用户确认；`failed`：识别失败（可重试 = 重新提交同照片任务）；
  `adopted`：用户采纳；`dismissed`：用户忽略。
- 采纳/忽略后任务保留在列表（历史），供回看。
- 同一照片存在 `queued`/`running` 任务时重复提交 **复用** 该任务（不新建、不报错）。
- `stage`：`queued` → `preparing`（10%）→ `identifying`（20–85%，按已用时长估算）
  → `reference`（90%）→ `done`（100%）。失败时保留最后 stage，`progress` 停住。
- `queuePosition`（1-based，仅 queued 有）：在 queued 任务中的顺序（1 = 下一个执行）；
  running/completed 为 `null`。
- `elapsedMs`：running 时为「已运行毫秒」，结束态为总耗时。
- 会话结束（`phase=finished`）后不再接受新任务（409 `PHASE_FINISHED`）；
  已存在的任务继续跑完，仍可查看/采纳。
- 会话过期（24h）后任务随之失效：worker 找不到会话 → 任务失败 `SESSION_NOT_FOUND`。

### 5.2 列表顺序

`running`（最早开始优先）→ `queued`（提交顺序）→ 其它（`createdAt` 倒序，最新在前）。

## 6. API 契约（全部前缀 `/api`）

### 6.1 配置

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
  "queue": {"queued": 0, "running": 0}
}
```

- `endpoint`：当前生效的**完整 API 端点 URL**；`endpointProtocol`：`"chat" | "responses" | null`；
  `endpointWarning`：端点无效时的中文提示（正常为 `null`）。
- `keySource`: `"env" | "config" | "mock" | null`；`keyMasked`: 未配置时为 `null`。
- 永远 200。

#### `PUT /api/bird/config`

请求（字段均可缺省；`null` / 空字符串 = 清除该字段）：

```json
{"apiKey": "ark-xxx", "endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
 "model": "doubao-seed-2-1-lite", "mock": false}
```

- `endpoint` 必须是完整接口 URL（`.../chat/completions` 或 `.../responses`），后端原样请求、不拼接；
  空字符串清除后回退默认端点。
- 响应：同 `GET /status`（200）。校验失败 → 400 `INVALID_CONFIG`（message 说明原因并给出两种端点示例）。
- 保存写 `arkEndpoint` 并清除旧 `arkBaseUrl`；环境变量优先。`mock=true` 时即使无 Key 也 `configured=true`。

#### `POST /api/bird/verify`

用当前配置向配置的端点发一个极小的真实请求（text-only）验证连通性：Chat Completions 用
`messages` + `max_tokens=16`，Responses API 用 `input` + `max_output_tokens=16`：

```json
{"ok": true, "message": "连接成功（doubao-seed-2-1-lite · Chat Completions）", "latencyMs": 812, "model": "doubao-seed-2-1-lite"}
```

失败也返回 200：`{"ok": false, "message": "401 ...（请求地址：https://...）", "latencyMs": null, "model": "..."}`；
端点无效 → ok=false + 端点警告。模拟模式下直接返回 `ok=true, message="模拟模式：无需真实连接"`。

### 6.2 任务

#### `POST /api/sessions/{sid}/bird-tasks`

```json
{"photoId": "a1b2c3d4e5f6a7b8"}
```

- 201 返回 `BirdTask`（复用已有 active 任务时也返回 201 + 该任务）。
- 错误：`SESSION_NOT_FOUND` 404、`PHOTO_NOT_FOUND` 404、`PHASE_FINISHED` 409、
  `INVALID_SOURCE` 400（browser 会话无图片）、`BIRD_NOT_CONFIGURED` 409、
  `INVALID_CONFIG` 400（端点不是受支持的完整接口 URL）。

#### `GET /api/sessions/{sid}/bird-tasks`

```json
{"tasks": [BirdTask, ...], "queue": {"queued": 1, "running": 1}}
```

错误：`SESSION_NOT_FOUND` 404。

#### `POST /api/sessions/{sid}/bird-tasks/{tid}/adopt`

```json
{
  "task": BirdTask,
  "observation": BirdObservation,
  "exif": {"written": true, "path": "E:\\photos\\a.jpg", "error": null}
}
```

- 写 EXIF + 记录到会话（PhotoSummary / 结果 JSON 立即生效）；任务 → `adopted`。
- 对已 `adopted` 任务再次 adopt：用当前结果覆盖（幂等更新）。
- 错误：`BIRD_TASK_NOT_FOUND` 404、`BIRD_TASK_NOT_COMPLETED` 409（queued/running/
  dismissed/failed）、`BIRD_RESULT_UNAVAILABLE` 409（`identified=false`）、
  `PHOTO_NOT_FOUND` 404、`INVALID_SOURCE` 400。
- EXIF 写失败不算 HTTP 错误：返回 200 + `exif.written=false` + `exif.error`（中文原因），
  JSON/会话仍记录该识别。

#### `POST /api/sessions/{sid}/bird-tasks/{tid}/dismiss`

响应 `BirdTask`（status=`dismissed`，幂等）。错误：`BIRD_TASK_NOT_FOUND` 404、
`BIRD_TASK_ADOPTED` 409（已采纳不能忽略）。

#### `GET /api/sessions/{sid}/bird-tasks/{tid}/reference`

- 200：`image/jpeg`，`Cache-Control: no-store`。
- 404：`BIRD_REFERENCE_UNAVAILABLE`（无参考图或未完成）。

### 6.3 数据模型

```ts
type BirdTaskStatus =
  | 'queued' | 'running' | 'completed' | 'failed' | 'adopted' | 'dismissed';
type BirdTaskStage = 'queued' | 'preparing' | 'identifying' | 'reference' | 'done';

interface BirdAlternative {
  species: string;
  speciesLatin: string;
  confidence: number | null;
}

interface BirdResult {
  identified: boolean;
  family: string;          // 科（中文）
  familyLatin: string;
  genus: string;           // 属（中文）
  genusLatin: string;
  species: string;         // 种（中文）
  speciesLatin: string;
  commonNameEn: string;
  confidence: number | null;
  note: string;
  reason: string;          // identified=false 时填
  alternatives: BirdAlternative[];
  referenceSource: string;
  referenceNote: string;
}

interface BirdTask {
  id: string;
  sessionId: string;
  photoId: string;
  photoName: string;
  photoRelativePath: string;
  thumbnailUrl: string;          // /api/sessions/{sid}/photos/{pid}/image?variant=thumb
  status: BirdTaskStatus;
  stage: BirdTaskStage;
  progress: number;              // 0-100
  queuePosition: number | null;
  createdAt: string;             // ISO-8601 带时区
  startedAt: string | null;
  finishedAt: string | null;
  elapsedMs: number;
  model: string;
  result: BirdResult | null;
  error: { code: string; message: string } | null;
  adopted: boolean;
  referenceAvailable: boolean;
  referenceUrl: string | null;   // /api/sessions/{sid}/bird-tasks/{tid}/reference
}

interface BirdObservation {
  family: string;
  familyLatin: string;
  genus: string;
  genusLatin: string;
  species: string;
  speciesLatin: string;
  commonNameEn: string;
  confidence: number | null;
  note: string;
  referenceSource: string;
  model: string;
  adoptedAt: string;             // ISO-8601
  exifWritten: boolean;
  exifError: string | null;
}
```

#### 既有模型扩展（向后兼容，schemaVersion 仍为 1）

- `PhotoItem.bird: BirdObservation | null`（`GET .../rounds/{i}/photos` 与 pick/viewed 的
  摘要刷新响应里都包含；未识别为 `null`）。
- `SessionSummary.birdIdentifiedCount: number`（本会话已采纳识别的照片数）。
- `PickResult.photos[].bird: BirdObservation | null`（**结果 JSON 新增字段**，老 JSON 无此字段）；
- `PickResult.birdIdentifiedCount: number`。
- `POST /api/sessions/restore`：接受带 `bird` 的 JSON，恢复后 `PhotoItem.bird` 立即生效；
  老 JSON（无 bird）继续可用。`schemaVersion` 仍为 `1`（仅新增可选字段）。
- **v5.3 状态保存/导入**：`PickResult.phase`（`picking`/`finished`，缺省 finished）；
  选片页「保存 JSON」（`picker-save-json`）下载 `phase=picking` 的当前状态，restore 后直接回到选片态
  （当前轮 pick 保留，可继续）；finished/旧 JSON 恢复后进结果页；非法 phase → `INVALID_RESTORE`。
- `BirdResult.referenceNote`：v5.2 起参考图缺失（如账号未开通「联网搜索」插件）时，worker 会把
  开通提示写入该字段（含 `https://console.volcengine.com/common-buy/CC_content_plugin`），
  前端在参考图占位处显示；`referenceAvailable` 仅当模型提供图片 URL 且后端下载成功时为 `true`。

## 7. EXIF 写入（`exif.py`）

写入**原照片文件**（不复制、不重编码、图像数据字节完全不变）：读取 JPEG 的 APP1(Exif)
段并用 Pillow `Image.Exif` 重新序列化（保留 IFD0 / Exif IFD / GPS / MakerNote 已有标签，
仅可能丢弃内嵌 JPEG 缩略图预览），在内存校验新文件可正常打开后，写同目录临时文件 +
`os.replace` 原子替换；替换后恢复原文件的 `mtime/atime`。

| 标签 | 值 |
| --- | --- |
| `0x010E ImageDescription` (ASCII) | `Bird: {speciesLatin}; Family: {familyLatin}; Genus: {genusLatin}`（拉丁缺失时用英文兜底 `PicturePick AI bird identification`） |
| `0x9C9B XPTitle` (UTF-16LE bytes) | `{species} {speciesLatin}` |
| `0x9C9E XPKeywords` (UTF-16LE bytes) | `鸟类;{family};{genus};{species};{speciesLatin}` |
| `0x9C9F XPSubject` (UTF-16LE bytes) | `{family} {genus} {species}` |
| `0x9C9C XPComment` (UTF-16LE bytes) | `PicturePick AI 识别（{model}，置信度 {pct}%）` |

- 返回值 `ExifOutcome(written: bool, path: str, error: str|null, warning: str|null)`；
  `error` 仅在 `written=false` 时给出；成功但发生降级（原 EXIF 读取/序列化失败、写入新 EXIF）时
  `written=true, error=None`，原因记在内部 `warning`（API 的 `exif` 响应只暴露 `written/path/error`）；
  文件不可写/被占用/非 JPEG → `written=false` + 中文 `error`，**不抛异常到 API 层**。
- 序列化异常时降级：用只含上述标签的新 Exif（`error` 提示原有 EXIF 可能未保留），仍尝试写入。
- 照片路径必须 `is_within(session.root, path)` 且是 `.jpg/.jpeg` 普通文件。

## 8. 前端 UX 契约

### 8.1 选片页布局（保持现有 DOM/类名/快捷键不变）

```
header.topbar                      ← 新增「AI 识鸟」按钮 + AI 设置图标
div.progress
main.workspace                     ← 新增：三栏
  aside.photo-info                 ← 左：图片信息栏（含鸟种）
  section.stage                    ← 中：原 stage（viewer/nav/meta/overlay 原样）
  aside.bird-queue                 ← 右：AI 识鸟任务队列（可折叠）
footer.dock                        ← 原底部工具栏 + filmstrip 原样
```

- 左栏 `data-testid="photo-info"`：文件名、路径、大小、尺寸、pick 状态；
  **鸟类识别块** `data-testid="bird-info"`：
  - 有 `photo.bird`：`data-testid="bird-info-species"` 显示
    `种：白鹭（Egretta garzetta）`，并列出科/属（中文 + 拉丁）、
    `AI · 置信度 92% · 已写入 EXIF` / `EXIF 写入失败`；来源行 `referenceSource`。
  - 无：`data-testid="bird-info-empty"` 显示「尚未识别 · 按 B 调用 AI 识鸟」。
- 右栏 `data-testid="bird-queue"`：标题「AI 识鸟队列」+ 数量徽标
  `data-testid="bird-queue-count"` + 折叠按钮 `data-testid="bird-queue-toggle"`；
  空状态 `data-testid="bird-queue-empty"`；卡片 `data-testid="bird-task-card"`
  （`data-task-id`、`data-status`）：缩略图、文件名、状态胶囊
  （排队中/识别中/待确认/已采纳/已忽略/失败）、进度条 + 百分比/队列位置/耗时、
  完成时显示鸟种、失败时显示错误摘要。点击卡片打开结果模态框。
- 窗口窄（<1180px）时左右栏可折叠为图标按钮；折叠状态存 localStorage，不影响功能。

### 8.2 提交识别

- 顶部按钮：`data-action="bird-identify"`、`data-testid="bird-identify-button"`，
  文案「AI 识鸟」+ `<kbd>B</kbd>`。
- 快捷键 `B`（大小写均可；模态框打开/输入框聚焦时不触发）。
- 点击/快捷键 → 取当前照片 → `POST .../bird-tasks`；
  成功 toast「已提交 AI 识鸟任务 · {文件名}」并刷新队列；
  已有 active 任务 → toast「该照片已在识别队列中」；
  未配置 → 打开 AI 设置弹窗（见 8.4）并提示先填写 Key；
  其它错误 → toast 后端 message。
- 提交后**不阻塞**选片/自动放映/再次提交。

### 8.3 队列轮询与结果确认

- 选片页挂载后每 1000ms `GET .../bird-tasks` 刷新（页面隐藏时暂停，重新可见立即刷新）；
  `destroy()` 时停止并 abort 在途请求。轮询失败静默（不连续 toast），恢复后自动继续。
- 结果模态框 `data-testid="bird-result-modal"`（`openModal`，size lg）：
  - **顶部对照区并排**：左「原照片 · {文件名}」`<img data-testid="bird-original-image">`
    （src 走 `/photos/{pid}/image?variant=original`）；右「参考照片（AI 提供）」
    `<img data-testid="bird-reference-image">`（src = 任务 `referenceUrl`）。无参考图/加载失败时
    右框显示 `result.referenceNote`（未开通联网搜索插件时为开通提示）或「未获取到参考照片」。
  - 下方：种 `bird-result-species`、科 `bird-result-family`、属 `bird-result-genus`、拉丁名、
    置信度条 `bird-result-confidence`、识别依据 note、备选 species、模型名。
  - 底部按钮：`bird-adopt-button`「采纳并写入 EXIF」、`bird-dismiss-button`「忽略」。
  - 失败任务模态框：也显示**原照片**（单图）+ 错误码/信息 `bird-result-error` +
    `bird-retry-button`「重新识别」+ `bird-dismiss-button`「忽略」；进度模态框同样显示原照片。
  - 已采纳任务：显示「已采纳」状态，按钮变为仅「关闭」（或允许重新采纳覆盖）。
  - **关闭**：所有模态框右上角 X `data-testid="modal-close"`（`modal.ts` 统一绑定点击关闭；
    v5.2 修复此前未绑定事件、点 X 无反应的 BUG），也支持 `Esc` / 点击遮罩关闭。
- 采纳成功 toast：`已采纳：{species}（结果 JSON 已记录，照片 EXIF 已写入）`；
  `exif.written=false` 时 warn：`已采纳并记录到结果 JSON，但照片 EXIF 写入失败：{error}`。
  采纳后立即更新左栏、当前照片数据、结果页计数。
- 忽略成功 toast「已忽略该识别任务」。
- 重新识别 = 对同一 photoId 新建任务（失败的旧任务保留为 failed）。

### 8.4 AI 设置弹窗（`data-testid="bird-settings-modal"`）

入口：启动页底部「AI 识鸟设置」按钮 `bird-settings-button` + 选片页顶部图标按钮
（同一 testid，两个入口都可用）。内容：

- 说明：调用火山方舟 `doubao-seed-2-1-lite`，**照片会上传到方舟服务**；Key 仅保存在本机。
- API Key 密码输入 `bird-api-key-input`（已配置时占位显示掩码，留空 = 不修改）；
- 高级（可折叠）：**API 端点（完整 URL）** `bird-endpoint-input`（帮助文字给出
  `.../chat/completions` 与 `.../responses` 两种示例）、模型名 `bird-model-input`；
- 状态行 `bird-config-status`：已配置/未配置/模拟模式/Key 来源/**接口协议**
  （Chat Completions / Responses API / 端点无效）与端点警告；
- 按钮：`bird-config-verify`「测试连接」+ `bird-config-save`「保存」。
- 保存成功 toast「AI 识鸟配置已保存」并刷新选片页状态；测试连接 toast 成功/失败信息。

### 8.5 结果页

- 摘要区新增一行 `data-testid="bird-identified-count"`：
  「AI 识鸟：{birdIdentifiedCount} 张已记录鸟种」；0 时显示「未使用 AI 识鸟」。
- 结果列表行若 `photo.bird` 存在：在文件名旁显示鸟种胶囊 `{species}`（title 含科/属/拉丁名）。

### 8.6 测试挂钩

- 沿用 `window.__picturepickTestSourceDir` / `__picturepickTestExportDir`。
- 前端不得在测试环境伪造识别结果；e2e 使用后端 `PICTUREPICK_BIRD_MOCK=1`。

## 9. 模拟模式（e2e / 演示 / 无 Key 开发）

- `PICTUREPICK_BIRD_MOCK=1`：`MockBirdClient`，不访问网络。
- `PICTUREPICK_BIRD_MOCK_DELAY`：单任务模拟耗时秒数，默认 `0.6`，夹到 0–5。
- 确定性结果：按 photoId 的 sha1 从下列列表取值（confidence = 0.80 + 0.03 * (hash%5)）：

| # | family | familyLatin | genus | genusLatin | species | speciesLatin | commonNameEn |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 鹭科 | Ardeidae | 白鹭属 | Egretta | 白鹭 | Egretta garzetta | Little Egret |
| 1 | 鹭科 | Ardeidae | 鹭属 | Ardea | 苍鹭 | Ardea cinerea | Grey Heron |
| 2 | 鹭科 | Ardeidae | 夜鹭属 | Nycticorax | 夜鹭 | Nycticorax nycticorax | Black-crowned Night Heron |
| 3 | 䴙䴘科 | Podicipedidae | 小䴙䴘属 | Tachybaptus | 小䴙䴘 | Tachybaptus ruficollis | Little Grebe |
| 4 | 翠鸟科 | Alcedinidae | 翠鸟属 | Alcedo | 翠鸟 | Alcedo atthis | Common Kingfisher |

- `note` 末尾带「（模拟识别结果，仅用于测试）」；`referenceSource = "Mock Reference"`。
- mock 参考图：Pillow 内存生成 800×600 JPEG（深色渐变 + 文字 `MOCK REFERENCE` /
  `{speciesLatin}`），`referenceAvailable=true`。
- 文件名（不含扩展名）含 `notbird` → 返回 `identified=false`（用于失败/无鸟路径测试）。
- `PICTUREPICK_BIRD_MOCK_FAIL=1` → 任务直接失败 `BIRD_API_ERROR`，message 含「模拟失败」。

## 10. 验收结果（v5.2，2026-10-25）

- `pytest`：**292 passed**（既有 179 + 新增 113：配置/端点双协议/任务/EXIF 保真/解析/
  模型参考图/ToolNotOpen 降级/SSRF 防护/结果恢复/客户端容错）。
- `scripts/e2e_api.py`：**PASS=238 / FAIL=0**（既有 133 + 新增 105 项识鸟检查：
  status/config/endpoint/tasks/adopt/EXIF/reference/result/restore/dismiss/失败路径）。
- `scripts/ui_e2e.cjs`：**PASS=122 / FAIL=0**（既有 71 + 新增 51 项：按钮/快捷键 B/队列卡片/
  进度/原照片+参考照片并排对照/模态框 X 关闭/参考图/采纳/左栏鸟种/结果页计数/完整端点设置；
  Playwright Chromium + mock 后端）。
- `frontend`：`npm run build`（tsc + vite）通过。
- 截图：`docs/screenshots/bird-queue.png`、`bird-result.png`（原照片+参考照片对照）、`bird-info.png`、
  `bird-results.png`（模拟模式生成，1440×900）。
- **真实 Key / 真实网络实测（v5.2，插件已开通）**：`endpoint=.../api/v3/responses` +
  `doubao-seed-2-1-lite-260915`：识鸟任务 2.4s 完成、解析正常；`.../chat/completions` 端点同样成功。
  `tools:[{"type":"web_search"}]` 正常执行、`web_search_error` 为空；模型联网返回真实参考图 URL
  （来源 WoRMS/VLIZ），后端 SSRF 校验后下载成功 `image/jpeg` **32KB / 2.09s**；
  **真实 UI 全流程 PASS=15 / FAIL=0**（白鹭/鹭科/白鹭属 置信度 90%、并排对照、X 关闭、采纳、
  左栏鸟种、结果页计数、结果 JSON `birdIdentifiedCount=1`；任务总耗时约 10.2s）。
  插件未开通时的 `ToolNotOpen` 自动降级由 pytest 覆盖。
- 桌面端 WebView2 复用同一后端，未新增依赖；PyInstaller 随 import 自动收集 `birdid`
  子包（本轮未重新打包，打包影响见 `docs/VERIFICATION.md`）。

## 11. 兼容性与限制

- 旧结果 JSON（无 `bird`）可恢复；新 JSON（有 `bird`）旧版本应用会忽略新增字段。
- 参考照片**由模型 API 提供 URL**（需方舟「联网搜索」插件；**本机已开通并实测可用**；
  未开通时识鸟仍成功但无参考图）；参考图不落盘、只存任务内存（按鸟种缓存）；
  任务队列在内存，后端重启后队列清空（已采纳结果已写入 JSON/EXIF）。
- 单 worker 顺序处理：一次只识别一张（火山方舟限流友好）；排队任务显示队列位置。
- EXIF 写入会修改原图元数据（**不重编码图像数据**）；建议用户自行备份。
- API Key 以明文保存在本机配置文件（或使用环境变量）；请勿在不可信机器上填写。
