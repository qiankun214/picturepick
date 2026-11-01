# PicturePick 架构文档

本目录回答三个问题：**它现在长什么样**、**前后端各做什么**、**怎么把它工程化**。

| 文档 | 内容 | 读者 |
| --- | --- | --- |
| [OVERVIEW.md](OVERVIEW.md) | 架构总览：部署拓扑、分层组件、28 个 API、两个状态机、AI 识鸟链路、质量现状、拆分候选 | 接手者 / 评审 / 新人 |
| [../ENGINEERING-ROADMAP.md](../ENGINEERING-ROADMAP.md) | 工程化路线图：成熟度评分、六条主线 × 三阶段、可执行任务与验收标准、CI 骨架 | 负责人 / 执行者 |
| `diagrams/*.png` | 6 张图（见下表），由脚本生成 | 汇报 / 评审 |
| [../API.md](../API.md) | **接口契约（唯一准绳）** —— 本文档不替代它 | 前后端 |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | 工程约定（版本演进记录、目录结构、关键机制） | 贡献者 |

## 六张图

| 图 | 回答什么问题 | 关键信息 |
| --- | --- | --- |
| [D1 系统上下文与部署拓扑](diagrams/PicturePick-D1-系统上下文与部署拓扑.png) | 它由哪些进程/载体组成？三种启动方式什么关系？ | 1 个后端进程 · 3 种入口 · 唯一出网点 |
| [D2 分层组件架构](diagrams/PicturePick-D2-分层组件架构.png) | 前端实现什么、后端实现什么、中间靠什么连接？ | 19 个前端 TS 模块 / 22 个后端模块 / 28 端点契约 |
| [D3 API 域地图](diagrams/PicturePick-D3-API域地图.png) | 到底有哪些 API？分几个域？ | 28 个端点 / 6 个域 / 前端实际用 23 条 |
| [D4 状态机](diagrams/PicturePick-D4-状态机.png) | 会话轮次怎么流转？AI 任务怎么流转？ | picking/finished · queued→running→completed→adopted |
| [D5 AI 识鸟时序](diagrams/PicturePick-D5-AI识鸟时序.png) | 一次识鸟从点击到落库发生了什么？ | 19 步时序 · 进度语义 · 隐私边界 · 失败路径 |
| [D6 工程化路线图](diagrams/PicturePick-D6-工程化路线图.png) | 怎么从 vibe code 变成可交付工程？ | 6 条主线 × P0/P1/P2 |

## 重新生成图示

```powershell
py -3.12 scripts\arch_diagrams.py                       # 全部 6 张
py -3.12 scripts\arch_diagrams.py --only d3-api          # 只生成一张
py -3.12 scripts\arch_diagrams.py --out docs\architecture\diagrams
```

- 依赖：**仅 Pillow**（后端已有依赖）+ Windows 自带字体（微软雅黑 / Consolas）；不需要浏览器、不需要联网。
- 为什么不用 Mermaid：在受限的 Windows 沙箱里 Chromium/Edge headless 会因命名管道与 crashpad 被拒绝（`0x5 拒绝访问`）而无法截图；Pillow 直绘是确定性的，CI 也能跑。
- 输出为 2× 超采样 PNG（逻辑尺寸见脚本内 `Canvas(1800, …)`），缩放查看不失真。

## 维护约定

1. **图与文档里的每个数字都要能溯源**：改代码后同步改 `scripts/arch_diagrams.py` 里对应 `d1…d6` 函数与 `OVERVIEW.md`，然后重新生成。
2. 每张图 footer 标注了生成函数与证据来源（例如 `d5_bird_sequence()` + `docs/ARCHITECTURE.md §4.5`）。
3. 事实与判断分开写：`OVERVIEW.md` 只陈述可验证事实；"应该怎么改"一律写在 `ENGINEERING-ROADMAP.md`。
4. 行数统计口径：**字节级 `\n` 计数**（`pathlib.read_bytes().count(b'\n')`），不要用 PowerShell `Get-Content`（本机会少算）。
