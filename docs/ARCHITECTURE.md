# PicturePick 架构与工程约定

## 1. 目标交付物

| 交付形态 | 说明 |
| --- | --- |
| 命令行 + 浏览器 | `python -m picturepick serve --dir <目录>` 启动本地服务并自动打开浏览器 |
| 前后端分离开发 | Vite 开发服务器（5173）→ `/api` 代理到 FastAPI（8000） |
| 桌面应用 | Electron 外壳 + PyInstaller 打包的 Python 后端 sidecar，electron-builder 产出 Windows 绿色版/安装包 |

## 2. 技术选型（均在广泛使用、社区活跃的项目中选取）

- 后端：Python 3.10 + **FastAPI** + **uvicorn** + **Pillow**（缩略图）+ **pydantic**（1.10/2.x 兼容写法，当前环境为 1.10.11）
- 前端：**TypeScript** + **Vite**（原生 DOM，无框架，构建产物为静态 HTML/JS/CSS）
- 桌面：**Electron 33.4.11** + **electron-builder 26.x**；Python 后端用 **PyInstaller** 打包为 sidecar
- 测试：**pytest** + `fastapi.testclient`；前端 `tsc --noEmit` + `vite build`；浏览器全流程用 Playwright（Chromium）

## 3. 目录结构（写入边界）

```
E:\picturePick\
├── docs/                     # Lead：契约与设计文档
├── backend/                  # backend-dev 独占
│   ├── pyproject.toml
│   ├── picturepick/
│   │   ├── __init__.py       # __version__ = "1.0.0"
│   │   ├── __main__.py
│   │   ├── cli.py            # serve / scan / version
│   │   ├── app.py            # create_app()、CORS、SPA 静态服务
│   │   ├── api.py            # 路由实现
│   │   ├── models.py         # pydantic 模型（与 docs/API.md 一致）
│   │   ├── scanner.py        # jpg 扫描、排序、路径安全
│   │   ├── session.py        # Session / Round / pick 计数状态机
│   │   ├── export.py         # PickResult 生成、下载文件名、写文件
│   │   ├── imaging.py        # 缩略图生成与 LRU 缓存
│   │   ├── fsbrowse.py       # 盘符/目录浏览、原生目录对话框
│   │   └── webui/            # 前端构建产物落点（gitignore；由 Vite 构建）
│   └── tests/                # pytest
├── frontend/                 # frontend-dev 独占
│   ├── package.json / tsconfig.json / vite.config.ts / index.html
│   └── src/                  # main.ts、api.ts、types.ts、state.ts、viewer.ts、ui/*.ts、styles.css
├── desktop/                  # desktop-dev 独占
│   ├── package.json / tsconfig.json
│   ├── src/main.ts / src/preload.ts
│   ├── pyinstaller/picturepick-backend.spec
│   └── electron-builder.yml
└── scripts/                  # Lead：dev.ps1 / build.ps1 / make_sample_photos.py / e2e 验证
```

## 4. 运行时数据流

1. 桌面端：Electron 选空闲端口 → spawn `picturepick-backend.exe serve --port <port> --no-open` → 轮询 `/api/health` → `loadURL("http://127.0.0.1:<port>/")`。
2. 浏览器模式：CLI 启动 uvicorn（默认 8000）→ 可选 `--dir` 预扫描 → 打开浏览器。
3. 前端加载：`GET /api/sessions/current`，有 session 直接进入选片，否则进入目录选择页。
4. 选择目录：桌面端走 preload 暴露的原生对话框；浏览器走 `/api/fs/list` 目录浏览器（可选 `/api/dialog/pick-directory`）。
5. 选片：前端按轮次分页拉取 `rounds/{i}/photos`，逐张 `image?variant=original` 展示；
   浏览上报 `viewed`，点击 pick 调 `PUT .../pick`。
6. 结束本轮 → 弹窗；用户选择进入下一轮 → `POST rounds/next`；或结束 → `POST finish`。
7. 最终结果：桌面端 `saveJson`（原生另存为）或后端 `GET result?download=1`。

## 5. 协作规则

- 每个模块目录只有一个 owner，禁止跨目录写入；跨模块改动通过共享任务留言协调。
- 接口以 `docs/API.md` 为唯一准绳；如发现契约缺陷，先发消息给 Lead，由 Lead 修改契约并广播。
- 提交前必须自测：后端 `pytest`；前端 `npx tsc --noEmit` + `npm run build`；桌面端 `npx tsc` + 实际启动验证。
- 网络/缓存约定（沙箱内 HOME 不可写）：
  - npm：`npm_config_cache=E:\picturePick\.cache\npm-<role>`、`npm_config_logs_dir=E:\picturePick\.cache\npm-<role>\_logs`
  - Electron 下载缓存：`ELECTRON_CACHE=E:\picturePick\.cache\electron`
  - electron-builder 缓存：`ELECTRON_BUILDER_CACHE=E:\picturePick\.cache\electron-builder`
  - 临时文件放 `E:\picturePick\.tmp\<role>\`，不要写系统临时目录以外的地方。
- Python 依赖已全局安装（fastapi/uvicorn/pillow/pyinstaller/pytest/httpx），无需 venv。
