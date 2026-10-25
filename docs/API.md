# PicturePick API 契约（v1）

本文件是前后端唯一的接口契约。后端实现、前端调用、桌面端集成都必须严格遵守。
所有接口前缀 `/api`。所有请求/响应均为 UTF-8 JSON（图片接口除外）。

## 0. 术语

- **照片（photo）**：用户所选目录下扩展名为 `.jpg` / `.jpeg`（大小写不敏感）的普通文件。
- **照片 ID**：`sha1(绝对路径字符串)[:16]` 的十六进制小写串，在会话内唯一、跨会话稳定。
- **轮次（round）**：第 1 轮为全部照片；之后每一轮为上一轮被 pick 的照片。
- **pickCount**：该照片在历史各轮中被 pick 的总次数（最终 JSON 记录此值）。
- **viewed**：该照片在本轮是否被展示过（前端浏览到时上报）。
- **phase**：`picking`（选片中）或 `finished`（已结束并生成结果）。

## 1. 通用错误格式

非 2xx 响应体：

```json
{ "detail": { "code": "INVALID_PATH", "message": "目录不存在或不可读" } }
```

`code` 取值：`INVALID_PATH`、`NO_PHOTOS`、`SESSION_NOT_FOUND`、`PHOTO_NOT_FOUND`、
`ROUND_NOT_FOUND`、`NOT_ENOUGH_PICKS`、`PHASE_FINISHED`、`DIALOG_UNAVAILABLE`、`WRITE_FAILED`。

## 2. 数据结构

### SessionSummary

```json
{
  "id": "9f2c...（32 位 hex）",
  "root": "E:\\photos",
  "recursive": true,
  "createdAt": "2026-10-24T15:00:00+08:00",
  "phase": "picking",
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
`rounds[].pickedCount` 之和 > 0 时代表历史累计。

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
  "sourceDirectory": "E:\\photos",
  "recursive": true,
  "totalPhotos": 120,
  "roundsPlayed": 3,
  "pickedPhotoCount": 12,
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

## 3. 系统接口

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

扫描目录并创建会话（同时激活为 current session）。

请求：

```json
{ "directory": "E:\\photos", "recursive": true }
```

- 成功 → 201，返回 `SessionSummary`。
- 目录不存在 / 不可读 → 400 `INVALID_PATH`。
- 无 jpg/jpeg 文件 → 400 `NO_PHOTOS`。

### GET /api/sessions/current

返回最近创建且未过期的会话，供命令行 `--dir` 预扫描后前端自动进入。

```json
{ "session": null }
```

或 `{ "session": SessionSummary }`。

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
照片不属于会话 / 文件已不存在 → 404 `PHOTO_NOT_FOUND`。

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
