# PicturePick API 契约（v1）

本文件是前后端唯一的接口契约。后端实现、前端调用、桌面端集成都必须严格遵守。
所有接口前缀 `/api`。所有请求/响应均为 UTF-8 JSON（图片接口除外）。

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

> 说明：第 2 版起，浏览器 UI 使用**浏览器自带目录选择框**（`<input type="file" webkitdirectory>`），
> 照片文件不会上传到后端；后端只接收照片元数据（相对路径/大小）用于轮次与 pick 计数。
> CLI 的 `serve --dir` / 桌面端 `--dir` 仍使用 server 来源（后端扫描并提供图片）。

## 1. 通用错误格式

非 2xx 响应体：

```json
{ "detail": { "code": "INVALID_PATH", "message": "目录不存在或不可读" } }
```

`code` 取值：`INVALID_PATH`、`NO_PHOTOS`、`INVALID_PHOTOS`、`TOO_MANY_PHOTOS`、`SESSION_NOT_FOUND`、
`PHOTO_NOT_FOUND`、`ROUND_NOT_FOUND`、`NOT_ENOUGH_PICKS`、`PHASE_FINISHED`、`PHASE_PICKING`、
`DIALOG_UNAVAILABLE`、`WRITE_FAILED`、`NO_PICKED_PHOTOS`、`INVALID_SOURCE`、`INVALID_RESTORE`、
`UNSUPPORTED_SCHEMA`。

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
  "rounds": [
    { "index": 1, "photoCount": 120, "viewedCount": 0, "pickedCount": 0 }
  ]
}
```

说明：`viewedCount` / `pickCount` 是**当前轮**的计数；`rounds[].pickedCount` 是**该轮** pick 数。
`origin=server` 时 `root`/`sourceLabel` 为绝对目录路径；`origin=browser` 时为浏览器选择的文件夹名。
`restored` 表示该会话是否通过 `POST /api/sessions/restore` 从 JSON 恢复（默认 `false`）。

### PhotoItem（分页列表项）

```json
{
  "id": "a1b2c3d4e5f60718",
  "name": "IMG_001.jpg",
  "relativePath": "2024/IMG_001.jpg",
  "sizeBytes": 2845312,
  "pickCount": 2,
  "picked": true,
  "viewed": false
}
```

`picked` / `viewed` 均针对**当前轮**。`pickCount` 为历史累计。

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
      "pickedInRounds": [1, 2, 3]
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

### POST /api/sessions/{sid}/finish

结束会话并生成最终结果。幂等：重复调用返回相同结果。

```json
{ "result": PickResult, "savedPath": null }
```

同时 `phase` 变为 `finished`。

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

## 5. 静态资源

后端在 `/` 提供前端构建产物（`picturepick/webui`，PyInstaller 下为 `sys._MEIPASS/webui`）：

- 存在时：`GET /` 返回 `index.html`，其余静态文件按路径返回；未匹配的路径回退到 `index.html`（SPA）。
- 不存在时：`GET /` 返回 JSON 提示（前端未构建）。
- API 路由优先于静态资源，不得被 SPA fallback 吞掉。

CORS：允许来源正则 `^https?://(localhost|127\.0\.0\.1)(:\d+)?$`（开发期 Vite 5173 端口跨域）。

## 6. 命令行

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

## 7. PyInstaller 后端可执行文件

- 产物：`build/backend/picturepick-backend.exe`（onedir 目录形式，dist 目录 `build/backend/`）。
- 入口：`picturepick.cli:main`，打包后支持同样的 `serve` / `scan` / `version` 子命令。
- 打包数据：`picturepick/webui/**` 必须内嵌，运行时通过 `sys._MEIPASS/webui` 定位。
- 启动参数由桌面端传入：`serve --port <port> --no-browser --no-open ...`（`--no-open` 与 `--no-browser` 均为等价别名，至少支持 `--no-open`）。

## 8. 浏览器端行为约定（第 3 版修正）

> 第 3 版核心原则：**浏览器只负责“选目录/选目标文件夹”，拿到绝对路径后交给本机后端；
> 浏览器绝不读取、遍历或上传照片文件，所有照片由后端扫描并通过 `/image` 按需提供。**
> 这样大目录也不会占满浏览器内存，照片数据也始终只在本机磁盘/本机服务之间流转。

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
- 自动化测试约定：用 `page.setInputFiles('[data-testid="restore-input"]', <json 文件路径>)` 载入。

