# PicturePick 工程化路线图（vibe code → 可交付工程）

> 配套文档：[架构总览（图示版）](architecture/OVERVIEW.md) · 图 D6：[工程化路线图](architecture/diagrams/PicturePick-D6-工程化路线图.png)
>
> 依据：`docs/` 全量文档 + 源码逐文件核对 + 工程化审计（证据见 [附录 C](#附录-c证据索引)）。
> 行号/行数为 2026-11-01 快照；数字用字节级计数（`\n` 数量）复核，不采信二手数字。

---

## 0. 判断：这不是烂代码，是「没有门禁的好代码」

先把话说清楚，否则改造方向会跑偏。

**已经做对的（不要推翻）**

| 做对的事 | 证据 |
| --- | --- |
| **接口契约先行**：`docs/API.md` 1189 行，28 条端点逐条与实现一致（无缺无多） | `docs/API.md`、`backend/picturepick/api.py` |
| **错误码集中管理**：26 个错误码 + `STATUS_BY_CODE`，`ApiError` 统一出口 | `errors.py:61-109`、`app.py:130-132` |
| **测试真的在写**：25 个测试文件 5136 行，292 个用例；`conftest` 做了配置隔离 | `backend/tests/**` |
| **架构红线守住了**：浏览器不读照片字节、路径越界校验、Key 只存本机且只回掩码 | `docs/API.md` §8.1、`scanner.py:180`、`birdid/config.py` |
| **安全基线高于同类本地工具**：CORS 白名单、SSRF 校验、EXIF 原子写、settings 0600 | `app.py:28`、`birdid/reference.py:52-93`、`birdid/exif.py:235-262` |
| **类型安全起步不错**：前后端 `tsconfig` 均 `strict: true`，前端 0 个 `any` | `frontend/tsconfig.json:14`、`desktop/tsconfig.json:9` |
| **构建脚本失败即停、可诊断** | `scripts/build.ps1`、`scripts/verify-electron.ps1` |

**真正缺的（本路线图要解决的）**

1. **可重复性**：依赖是区间、无 lock、PyInstaller 不在清单、`npm install` 而非 `npm ci`；
2. **可见性**：没有 CI、没有 lint/类型检查、没有覆盖率、前端零测试；
3. **纪律的自动化**：所有质量约定靠「人记得去跑」（`docs/VERIFICATION.md` 就是手工命令清单）；
4. **契约的单向同步**：`docs/API.md` → 人 → `types.ts` → 人 → `api.ts`；
5. **交付状态**：12 个修改 + 11 个未跟踪文件还没提交，版本号字面量另有 11 处。

> 一句话：**它的「正确性」是认真的，它的「可重复性」是随缘的。** 工程化 = 把随缘的部分变成机器的责任。

---

## 1. 成熟度评分（0–5，主观判断，用于对齐，不用于考核）

| 维度 | 现状 | 现在 | 目标 | 关键差距 |
| --- | --- | --- | --- | --- |
| 版本控制与仓库卫生 | 有 .gitignore、产物未入库；但在途交付物未提交、`.pytest-run` 隐形 | **3.0** | 4.5 | 提交纪律 / PR 流程 |
| 依赖与构建可复现 | 区间依赖、无 lock、`npm install`、PyInstaller 无声明 | **1.0** | 4.0 | pin + lock + 可复现打包 |
| CI 与质量门禁 | 无 CI、无 lint、无类型检查流水线 | **0.5** | 4.5 | 一条命令 + 一个 workflow |
| 测试工程 | 后端 292 用例强；前端 0；e2e 只能手工跑且污染工作树 | **2.5** | 4.5 | 前端单测 + e2e 参数化 + 覆盖率 |
| 契约管理 | 文档极详细但对实现是「人肉同步」，无快照/生成 | **2.5** | 4.5 | OpenAPI 快照 + 类型生成 |
| 可观测性 | 有 `/api/health` 与桌面日志；后端无 logging、无轮转、无脱敏规范 | **1.5** | 3.5 | 结构化日志 + 诊断入口 |
| 安全基线 | CORS/SSRF/路径/Key 掩码到位；缺 CSP、Electron sandbox、无鉴权可绑任意网卡 | **3.0** | 4.0 | 桌面加固 + 绑定约束 + SSRF 加固 |
| 文档 | 密度罕见（API 1189 行 + 契约 591 行 + 验证 375 行）；但版本号漂移、无 CHANGELOG/ADR | **4.0** | 4.5 | 版本一致 + 变更记录 |
| 交付发布 | 打包脚本可用、仅 Windows、产物无哈希清单 | **2.0** | 4.0 | manifest + CI 出品 |
| **综合** | — | **2.2** | **4.2** | — |

---

## 2. 六条主线 × 三阶段（可执行任务表）

> 约定：**P0 = 0–2 周（先把地基铺平，不改业务行为）**；**P1 = 2–6 周（门禁与解耦）**；**P2 = 6–12 周（架构与交付）**。
> 每条任务的「验收」都必须是**机器可判定**的（命令 / 测试 / 脚本退出码），不接受「人工确认」。

### W1 · 版本控制与仓库卫生

| 阶段 | 任务 | 具体动作 | 验收 |
| --- | --- | --- | --- |
| P0 | 提交在途交付物 | 按 3 组语义提交：`feat(desktop): electron 外壳与 sidecar 打包`、`docs: 打包/升级/架构文档`、`chore(scripts): 构建与自检脚本`；对照目录里的 12 M + 11 ?? | `git status --porcelain` 只剩草稿目录；`git log --oneline` 有对应提交 |
| P0 | `.gitignore` 补齐 | **只缺 `.pytest-run/`**（`*.log`、`build/`、`release/`、`.tmp/`、`.cache/`、`.userdata/`、`node_modules/` 已在 `.gitignore:1-40` 中被忽略）；顺带解决 `.pytest-run` 目录 ACL 不可读导致的 `git status` 警告 | `git check-ignore -v .pytest-run` 有输出；`git status` 不再出现 `Permission denied`；`git ls-files` 中无 `release/`、`build/`、`.cache/` |
| P1 | PR 流程与提交规范 | `.github/pull_request_template.md`（自检清单）、提交信息规范（Conventional Commits）、分支保护 | 新建 PR 自动带模板；提交信息不合规被拦（hook 或 CI） |
| P2 | 开发环境文档 | `docs/DEV-SETUP.md`：依赖版本、起服务、跑测试、打包、常见坑（含 DSH 沙箱 ACL 导致 Electron 崩溃的已知问题）；清理本地 4.5GB 垃圾 | 新机器按文档从 clone 到跑通 ≤ 15 分钟 |

### W2 · 依赖与构建可复现

| 阶段 | 任务 | 具体动作 | 验收 |
| --- | --- | --- | --- |
| P0 | Python 依赖精确化 | `requirements.txt` 全部 `==`（按 3.12 实际安装版本）；拆 `requirements-dev.txt`（pytest / httpx / **pyinstaller==6.x**） | 两个干净环境 `pip install -r` + `pip check` 均无冲突 |
| P0 | 前端用 `npm ci` | 实际调用点在 `scripts/build.ps1:94`、`:111` 与 `scripts/dev.ps1:32`，形式为 `npm --prefix <dir> install` → 改为 `npm --prefix <dir> ci`（`build-electron.ps1` 只是转调 `build.ps1`） | `Select-String 'npm.*install' scripts\*.ps1` 只剩历史注释，实际命令全为 `npm ci` |
| P1 | lock 策略 | Python：`pip-tools`/`uv` 生成 `requirements.lock`；Node：保留 `package-lock.json` 并在 `package.json` 声明 `engines`（node ≥20、python 3.12 记在 README） | CI 校验「清单与 lock 一致」，不一致即红 |
| P2 | 打包可复现 | 固定 Python/Node/PyInstaller 版本并记录构建环境；产物输出 `sha256` + `manifest.json`（版本/提交哈希/文件列表） | 同一 tag 两次构建的 sha256 一致（或差异被解释） |

### W3 · CI 与质量门禁

| 阶段 | 任务 | 具体动作 | 验收 |
| --- | --- | --- | --- |
| P0 | `scripts/ci.ps1` 一键流水线 | 顺序执行：`pytest -q` → 前端 `tsc --noEmit` → 桌面 `tsc` → `vite build` → 架构图重生成校验（`git diff --exit-code docs/architecture/diagrams`）；失败即停并打印失败段（骨架见 [附录 A](#附录-a可直接复制的-ci-骨架)） | 本地一条命令全绿（预计 2–4 分钟） |
| P0 | GitHub Actions | `.github/workflows/ci.yml` 三 job：backend-test（3.12 + pip cache + pytest）、frontend-check（node20 + npm ci + tsc + build）、desktop-check（tsc）；矩阵锁定版本（骨架见 [附录 B](#附录-b可直接复制的-github-actions-骨架)） | PR 上三个 check 必须绿才能合并 |
| P1 | ruff + eslint + prettier | 只 `--check` 不 `--fix`（避免历史 diff 淹没 review）；配置里先排除历史噪声规则，逐步删掉代码中 22 处 `# noqa` | `ruff check .` 0 error；`npx eslint .` 0 error；`prettier --check` 绿 |
| P1 | mypy 渐进 | 白名单起步：`models.py`、`api.py`、`session.py`；`--ignore-missing-imports`，之后逐步扩到 `birdid/` | 白名单文件 0 error，且不新增 `type: ignore` |
| P2 | 覆盖率门槛 | `pytest-cov` + `--cov-fail-under=70`（后端，先测基线）；前端 Vitest coverage 单独门槛 | CI 打印覆盖率并拦截回退 |

> 已实测（本次改造过程中）：`node frontend/node_modules/typescript/bin/tsc -p frontend/tsconfig.json --noEmit` 与 `desktop` 同命令**均返回 0**。也就是说 W3 的「typecheck」两段可以**立刻**进 `ci.ps1`，不需要先修类型债 —— P0 里真正要新建的只有 `ci.ps1` 与 workflow 文件本身。

### W4 · 测试工程

| 阶段 | 任务 | 具体动作 | 验收 |
| --- | --- | --- | --- |
| P0 | e2e 参数化（先让它能重复跑） | 端口从环境变量读（不再固定 8125/8123）、`EXPECTED_TOTAL` 由样例目录动态计算而非写死 60、截图输出到 `.tmp/` 而不是 `docs/screenshots/`、失败保留 trace | 连续两次运行都 PASS，且运行后 `git status` 无新增文件 |
| P1 | 前端单元测试（Vitest + jsdom） | 只测纯逻辑，不测 DOM 细节：`api.ts` 错误映射、`viewer.ts` 缩放/平移数学、`state.ts` 偏好读写、`queue.ts` 轮询生命周期（fake timers） | `npm test` 绿；上述模块行覆盖率 ≥ 60% |
| P1 | e2e 三层隔离 | L1 mock 后端（`PICTUREPICK_BIRD_MOCK=1`，不联网）→ 每次 PR 跑；L2 真实后端 + 样例照片（不调模型）→ 每次 PR 跑；L3 真 API Key + 真网络 → 手工 checklist，CI 永不跑 | CI 只跑 L1+L2；L3 有 `docs/REAL-KEY-CHECKLIST.md` |
| P2 | 契约测试 | OpenAPI snapshot（见 W5）+ 生成类型参与 `tsc`；关键端点加「请求/响应字段快照」测试 | 任何路由/字段变更必须显式更新 snapshot，否则测试红 |

### W5 · 契约先行与架构解耦

| 阶段 | 任务 | 具体动作 | 验收 |
| --- | --- | --- | --- |
| P0 | OpenAPI 快照 | 应用已暴露 `/api/openapi.json`（`app.py:114`）：用脚本导出到 `docs/contract/openapi.json`，pytest 里做「重新生成 == 文件」比对 | 手改路由/字段后测试失败并打印 diff |
| P1 | 前端类型生成 | `openapi-typescript docs/contract/openapi.json -o frontend/src/types.gen.ts`；手写 `types.ts` 只保留 UI 专属类型 | 契约改名后前端 `tsc` 立即报错（而不是运行时 404） |
| P1 | `api.py` 按域拆 router | 拆成 `routers/{system,session,photos,results,bird_config,bird_tasks}.py`，`api.py` 只做 `include_router`；**路由总数仍为 28**，OpenAPI 快照不变 | 每个 router 文件 ≤150 行；`/api/openapi.json` 与快照零 diff |
| P1 | 前端路径常量 | `api.ts` 使用生成的路径常量，禁止手写 `/api/...` 字符串 | `grep -rn "'/api/" frontend/src` 仅命中生成文件 |
| P2 | `picker.ts` 拆分 + 虚拟列表 | 拆为 `view` / `controller` / `shortcuts` / `slideshow`；胶片条与结果列表虚拟化（当前 5 万张上限会造 5 万个 DOM 节点） | 单文件 ≤300 行；5 万张样例下滚动帧率与首屏时间达标 |

### W6 · 可观测性、安全与发布

| 阶段 | 任务 | 具体动作 | 验收 |
| --- | --- | --- | --- |
| P0 | 版本号单一来源校验 | `scripts/check-version.ps1`：读 `picturepick.__version__`，校验 `desktop/package.json`、打包脚本、`pyproject.toml` 一致；收敛散落在 5 个脚本/2 个 package.json/webview 入口里的 11 处字面量 | 脚本退出码 0；除 `__init__.py` 与 `package.json` 的 `version` 字段外，其余文件 0 处字面量 |
| P0 | 后端日志 | 用 `logging` 替代 `print`，统一 JSON 格式 + 轮转 + **Key/路径脱敏**；`access_log` 按需开启 | `ruff check --select T201` 0；日志样本里无明文 Key |
| P1 | 桌面安全加固 | `sandbox: true`（当前 `desktop/src/main.ts:530` 为 false）+ CSP（`default-src 'self'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:*`） | `verify-electron.ps1` 全流程仍 PASS（截图+健康检查） |
| P1 | SSRF 加固 | DNS 解析后校验解析出的 IP 段、**禁止或复核重定向**、响应体大小上限（当前 `read()` 无上限） | 单测覆盖「302 → 内网」「域名解析到 127.0.0.1」两类攻击 |
| P1 | 绑定与鉴权 | 默认只绑 `127.0.0.1`；若支持 `--host` 非回环，必须显式 `--allow-remote` + 一次性 token，并在启动时警告 | 无 token 时非回环绑定直接拒绝启动 |
| P2 | 会话与任务持久化 | SQLite 存 session / round / bird task；启动时加载未过期会话 | 杀掉后端重启后，未保存的会话仍可继续选片 |
| P2 | CHANGELOG + 发布流水线 | tag 触发构建（Windows 先做、mac/linux 后补），产物带 sha256 与 manifest，`release/` 不入库 | 打 tag 后自动产出 release draft |

---

## 3. 里程碑与验收

| 里程碑 | 时间 | 必须达成（验收即上表 P0/P1/P2 对应项全绿） | 预期成熟度 |
| --- | --- | --- | --- |
| **M0 地基** | 2 周 | 工作树干净、`ci.ps1` 本地全绿、依赖 pin + lock、版本单一来源、`.gitignore` 补齐 | 2.2 → **3.2** |
| **M1 门禁** | 6 周 | CI 强制、ruff/eslint/mypy 0 error、前端单测、OpenAPI 快照 + 类型生成、`api.py` 拆分 | 3.2 → **3.8** |
| **M2 交付** | 12 周 | 覆盖率门槛、会话持久化、桌面安全加固（sandbox+CSP）、e2e 三层隔离、可复现打包 + CHANGELOG | 3.8 → **4.2** |

**M0 的唯一判断标准**：把仓库交给一个没参与过开发的人，他能在 15 分钟内跑起来、跑通测试、并知道「什么地方一改就红」。

---

## 4. 明确不做的事（防止「工程化」变味）

| 不要做 | 为什么 |
| --- | --- |
| 拆微服务 / 上 K8s / 引入消息队列 | 单用户本机工具，进程内调用零成本；拆分只增加部署与调试成本（详见总览 §7） |
| 重写前端为 React/Vue | 问题不是「没有框架」，而是「`picker.ts` 985 行 + 零测试」；先拆文件 + 加测试，收益高一个数量级 |
| 一开始就开严格 lint 并全量 `--fix` | 历史 diff 会淹没 review，等于把一次重构做成黑盒；先 `--check` 建立基线 |
| 为覆盖率写无意义测试 | 先测纯逻辑（缩放数学、错误映射、轮询生命周期），UI 细节交给 e2e |
| 把 L3（真 API Key）跑进 CI | 花钱、不稳定、泄露风险；用 checklist 兜底 |
| 把 `release/`、`build/`、截图产物提交进 git | 会让仓库从 8MB 变成 GB 级；产物只由 CI 生成 |

---

## 5. 今天就能做的 5 件事（30 分钟–2 小时）

1. **把在途交付物提交**（12 M + 11 ??，其中 3 项是本次文档）：`git add -A && git commit -m "feat(desktop): electron 外壳 + 打包/自检脚本"`（建议按 W1 拆 3 个提交）。
2. **`.gitignore` 加 `.pytest-run/`**（其余产物与 `*.log` 已被忽略，审计已确认；顺手清掉该目录的 ACL 问题）。
3. **`requirements.txt` 转精确版本**：`pip freeze` 后手写（保留注释分组），顺手加 `requirements-dev.txt` 里 `pyinstaller==6.x`。
4. **落 `scripts/ci.ps1`**（附录 A）：先跑「pytest + tsc + build」三段，能跑通就是 CI 的雏形。
5. **落 `.github/workflows/ci.yml`**（附录 B）：三 job 矩阵，先允许 red，但**立刻能看到红**。

---

## 附录 A：可直接复制的 CI 骨架

`scripts/ci.ps1`（本地与 CI 共用，避免「CI 脚本只在 CI 里能跑」）：

```powershell
# PicturePick 本地 CI：一条命令做完 lint/type/test/build 中已具备的部分
# 用法: powershell -ExecutionPolicy Bypass -File scripts\ci.ps1 [-SkipBuild]
param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
function Step($name, [scriptblock]$body) {
  Write-Host "`n=== $name ===" -ForegroundColor Cyan
  & $body
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $name (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
  Write-Host "OK: $name" -ForegroundColor Green
}

Step 'backend pytest' { py -3.12 -m pytest -q (Join-Path $root 'backend') }
Step 'frontend tsc'   { node (Join-Path $root 'frontend\node_modules\typescript\bin\tsc') -p (Join-Path $root 'frontend\tsconfig.json') --noEmit }
Step 'desktop tsc'    { node (Join-Path $root 'desktop\node_modules\typescript\bin\tsc') -p (Join-Path $root 'desktop\tsconfig.json') }
Step 'version check'  { powershell -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\check-version.ps1') }
if (-not $SkipBuild) {
  Step 'frontend build' { Push-Location (Join-Path $root 'frontend'); try { npm run build } finally { Pop-Location } }
  Step 'arch diagrams'  { py -3.12 (Join-Path $root 'scripts\arch_diagrams.py'); git -C $root diff --exit-code -- docs/architecture/diagrams }
}
Write-Host "`nALL GREEN" -ForegroundColor Green
```

> 注：`check-version.ps1` 与 `arch diagrams` 两步按 W6/W5 的任务先补脚本，再启用；起步阶段可先用 `-SkipBuild` 只跑前三段。

## 附录 B：可直接复制的 GitHub Actions 骨架

```yaml
name: ci
on:
  push: { branches: [main] }
  pull_request:
jobs:
  backend:
    runs-on: windows-latest   # 项目强依赖 Windows 路径语义（E:\ 绝对路径 / 大小写不敏感）
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: py -3.12 -m pip install -r backend\requirements.txt -r backend\requirements-dev.txt
      - run: py -3.12 -m pytest -q backend
      - run: py -3.12 -m ruff check backend      # P1 启用
  frontend:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: 'npm', cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
        working-directory: frontend
      - run: npx tsc -p tsconfig.json --noEmit
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
  desktop:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: 'npm', cache-dependency-path: desktop/package-lock.json }
      - run: npm ci
        working-directory: desktop
      - run: npx tsc -p tsconfig.json
        working-directory: desktop
```

> 说明：不用 `ubuntu-latest` 是刻意的 —— 后端有大量 Windows 路径语义（`E:\`、`normcase`、tkinter 目录框），在 Linux 上跑测试会得到假绿。

## 附录 C：证据索引

| 结论 | 证据 |
| --- | --- |
| 28 个端点、6 个域 | `backend/picturepick/api.py`（28 个 `@router`）+ `app.py:72,89`（2 个静态路由）；OpenAPI 实测 28 operations |
| 无 CI / 无 lint / 无前端测试 | 仓库无 `.github/`；无 ruff/mypy/eslint/prettier 配置；`frontend/package.json` 无 `test` 脚本 |
| 测试规模 | `backend/tests` 25 文件 5136 行；`pytest --collect-only` = 292；`scripts/e2e_api.py` 915 行；`scripts/ui_e2e.cjs` 700 行 |
| 依赖不可复现 | `backend/requirements.txt` 全为 `>=x,<y` 区间、0 个 `==`；无 lock；PyInstaller 不在清单 |
| 代码体量 | 后端 22 模块 5503 行；前端 19 TS 4626 行 + CSS 3880 行；桌面 3 TS 911 行（均为字节级 `\n` 计数） |
| 在途交付物未提交 | `git status --porcelain`：12 个 M + 11 个 ?? |
| 版本号漂移 | `__version__="1.0.0"` 单源声明，但同一字面量另有 11 处（5 个脚本 + 2 个 `package.json` + webview 入口）；`docs/API.md` 标题「第 5.2 版」而内容含 v5.3 |
| 无鉴权可绑任意网卡 | `app.py:121-128` 只有 CORS；`cli.py:204` 接受 `--host` |
| 会话无锁 | `session.py:267-293` 状态变更无锁；`SessionStore` 锁仅保护字典（`session.py:340`） |
| Electron 安全设置 | `desktop/src/main.ts:530` `sandbox: false`；`frontend/index.html` 无 CSP |
| SSRF 缺口 | `birdid/reference.py:52-93`（DNS 未解析、urllib 自动跟随重定向不复核）；`birdid/client.py:76`（响应体无上限） |

**审计环境限制（如实声明）**：本机 DSH 沙箱对临时目录有 ACL 限制，`pytest` 全量执行会在 `tmp_path` 构造阶段报 `PermissionError`（292 个用例全部 error，**不是用例失败**）。因此本文只把「`collect=292`」当作已核实事实；「292 passed」采信 `docs/VERIFICATION.md` 的历史记录，建议在普通桌面环境用 `scripts/ci.ps1` 复跑确认。
