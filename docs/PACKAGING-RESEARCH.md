# PicturePick 跨平台打包调研（Windows / macOS / Linux）

- 调研日期：**2026-10-25**（文中版本号、链接均以该日为准）
- 范围：在**尽量不改技术栈**（Python/FastAPI 后端 + TypeScript/Vite 静态前端）的前提下，找出能覆盖 Windows / macOS / Linux 的打包方式；本轮只做调研与选型，不改代码。
- 证据等级：**[官方]** 官方文档/官方仓库 · **[实测]** 本机或本项目实测 · **[社区]** 社区 issue/实例 · **[估计]** 经验推算，落地前需实测。
- 详细调研笔记（含全部来源 URL）：
  [`01-pyinstaller-pywebview.md`](../.scratch/research/01-pyinstaller-pywebview.md)、
  [`02-tauri-sidecar.md`](../.scratch/research/02-tauri-sidecar.md)、
  [`03-electron-alternatives-ci.md`](../.scratch/research/03-electron-alternatives-ci.md)

---

> **2026-10-25 决策更新（重要）**：Windows 上 pywebview + pythonnet 在 Python 3.12 下出现无法绕过的问题
> （`Failed to resolve Python.Runtime.Loader.Initialize`，源码模式与冻结包均复现），**桌面壳已正式切换为 Electron**：
> 复用 `desktop/`（Electron 33 + electron-builder）+ PyInstaller 打包的 Python 后端 sidecar；
> 构建入口改为 `build.ps1` 默认 Electron，pywebview 方案降级为兼容方案（`build.ps1 -WebView2`）。
> 本报告的方案对比、CI 矩阵、签名/体积结论继续有效，可据此规划 mac/Linux 构建。

## 0. 结论先行（TL;DR）

1. **当前技术栈本身是跨平台的，没有必须换技术栈的理由。** 两个真正的硬约束是：
   - **打包器不能交叉编译**：Windows 包只能在 Windows 构建，mac 包只能在 mac 构建，Linux 包只能在 Linux 构建 → **三平台构建矩阵（CI）是必经之路**，与选哪个壳无关。[官方：PyInstaller 明确 "not a cross-compiler"](https://pyinstaller.org/en/stable/)
   - **macOS 分发需要 Apple 开发者账号（$99/年）+ 签名 + 公证**，否则用户首次打开要被 Gatekeeper 拦；Windows 代码签名在中国大陆主体基本走不通（见 §2.4）。
2. **推荐主路线：把现有 `desktop_webview/`（PyInstaller + pywebview）扩成三平台**，产物 Windows 绿色版 / Linux deb+tar.gz / macOS .app+dmg，CI 三平台矩阵。代价约 **10–13 人日**（到"三平台可安装、可启动，不签名"）。
3. **先修 Windows pythonnet 冻结问题（P0，现在就坏）**：上游修复是 pywebview PR [#1832](https://github.com/r0x0r/pywebview/pull/1832)，**2026-10-19 已合入 master 但未发版**（PyPI 最新仍是 6.2.1，2026-04-15）。同时建议把构建 Python 从 3.10 升到 3.12：**Python 3.10 已于 2026-10-01 EOL**，且 [pythonnet 3.2.x 要求 Python ≥3.11](https://pypi.org/project/pythonnet/)，3.10 只能停在 3.1.0。
4. **最快跨平台兜底：浏览器模式 + 后端 PyInstaller 包（1–2 人日）**。项目「方式二：命令行 + 浏览器」本来就能跑三平台，只差一个免装 Python 的分发包；适合先让 mac/Linux 用户用起来，原生窗口后续再补。
5. **Electron（项目内已有历史实现）作为次选保留**：打包链路实测是通的（portable 94.8 MB / NSIS 106.5 MB / 解包 346 MB），本机"Chromium 启动即崩"是 DSH 沙箱禁命名管道导致的，**不是 Electron 技术缺陷**；真正的代价是体积 3–6 倍 + 约每 16 周跟一次大版本。
6. **Tauri 2 + Python sidecar 最"正规"**（官方把 PyInstaller 打的 Python API server 列为 sidecar 典型用例），但引入 Rust 工具链 + 重写外壳，估 **6–10 人日**，且 Linux 一样依赖系统 WebKitGTK；仅在"正式三平台发行版（安装器/更新/一致体验）"时才值得。

---

## 1. 现状与可复用资产

| 层 | 现状 | 跨平台影响 |
| --- | --- | --- |
| 后端 | Python 3.10 + FastAPI/uvicorn/Pillow/pydantic，只监听 `127.0.0.1`，静态托管前端产物 | 纯 Python，三平台可运行；唯一平台分支是 `fsbrowse` 的盘符枚举 + tkinter 对话框 |
| 前端 | TypeScript + Vite 纯原生 DOM，产物固定输出到 `backend/picturepick/webui` | 与平台无关 |
| 桌面壳 | `desktop_webview/`：pywebview 6.2.1 + PyInstaller 6.22.3，onedir，Windows WebView2 + pythonnet | 可复用；三平台需要各自的 spec 分支与隐藏依赖 |
| 历史方案 | `desktop/`：Electron 33 + electron-builder 26.18.0 + PyInstaller sidecar | 打包链路已验证，本机因沙箱无法启动 Electron |
| 构建脚本 | `scripts/build.ps1`（前端 → PyInstaller → release + zip），**Windows-only** | 需抽象成跨平台脚本 |
| 现有产物 | `release/PicturePick-WebView2/` = **33.2 MB / 873 文件 / zip 17.2 MB**（实测） | 体积基准 |

桌面壳的关键代码（`desktop_webview/picturepick_desktop/desktop.py`）约 90% 与平台无关：空闲端口 → 后台线程起 uvicorn → 轮询 `/api/health` → pywebview 开窗（加载 `http://127.0.0.1:<port>/?desktop=webview2`）→ 退出清理。真正需要平台化的只有：

- `user_data_dir()` 的 win32 分支（mac 应 `~/Library/Application Support`，Linux 应 `$XDG_DATA_HOME`/`~/.local/share`）；
- `open_path()` 的 `os.startfile`（mac 用 `open`，Linux 用 `xdg-open`）；
- Windows 专用自检/截图钩子（`webview.platforms.edgechromium`、`User32.FindWindowW`、`ImageGrab`）；
- 前端 `?desktop=webview2` 标记（建议改成通用 `?desktop=pywebview`，桌面判定仍靠 `window.pywebview`）。

---

## 2. 四条必须先破除的幻想 / 硬约束

### 2.1 「一台 Windows 开发机产出三平台安装包」不成立

| 目标产物 | Windows 开发机 | 现实路径 |
| --- | --- | --- |
| Windows exe / zip / nsis | ✅ 直接构建 | 本机（现状） |
| Linux deb / tar.gz / AppImage | ⚠️ 只能经 WSL2/Docker 或 CI | GitHub Actions `ubuntu-22.04` |
| macOS .app / dmg | ❌ 必须在 macOS 上构建 | GitHub Actions `macos-26`（arm64）/ `macos-26-intel`（x64）或自备 Mac |

PyInstaller、Nuitka、PyOxidizer、py2app 全都不支持交叉编译；electron-builder / Tauri 也明确 mac 产物必须 mac 构建（electron-builder 官方原文 "Don't expect that you can build an app for all platforms on one platform"，签名 "Cannot be fixed"）。**结论：三平台构建矩阵是基础设施，不是可选项。**

### 2.2 三平台是三种 WebView 内核，不是一套

- Windows：Edge WebView2 + **pythonnet/.NET**（本项目当前阻塞点，见 §4.3）
- macOS：系统 WKWebView + **PyObjC**
- Linux：系统 **WebKitGTK 4.1**（GTK 后端，pip 装不上，必须系统包）或 QtWebEngine（可自包含，但体积 150–250 MB）[官方：pywebview Installation](https://pywebview.flowrl.com/guide/installation)

→ 同一个前端要在三套引擎上做兼容性回归（CSS/JS 特性、缩放、全屏、键盘事件）。这是任何"系统 WebView 方案"（pywebview / Tauri / Wails / Neutralino）的共同成本；Electron 是唯一"自带内核、三平台一致"的选项，代价是体积。

### 2.3 macOS 分发门槛

- 技术链路：`PyInstaller BUNDLE()` 或 py2app 产出 `.app` → `codesign`（Developer ID Application）→ `xcrun notarytool submit` → `xcrun stapler staple`。
- pywebview 官方《Freezing》文档至今仍写 **macOS 用 py2app**、Windows/Linux 用 PyInstaller；PyInstaller 打 mac 属于**社区成熟实践**（有完整 BUNDLE spec 实例，需显式补 pyobjc hiddenimports）。
- 现实选择：**不签名**（用户需"右键打开"或 `xattr -dr com.apple.quarantine`，体验差但自用工具可接受）或**买 Apple Developer $99/年**。

### 2.4 Windows 代码签名 / Python 版本

- Windows 的 Azure Artifact Signing（原 Trusted Signing）Public Trust：个人仅限美国/加拿大，组织仅限 US/CA/EU/UK/AU/NZ/JP/KR/SG/CH/NO/IL —— **中国大陆主体基本不可用**；只能买 OV/EV 证书（EV 多为硬件 token，不便 CI）。**建议把"不签名 + 首次运行放行说明"当成正式一档**，而不是隐藏问题。
- **Python 3.10 已于 2026-10-01 EOL**（[python.org devguide](https://devguide.python.org/versions/)），PyInstaller 6.22.3 虽仍支持 3.8–3.15，但 3.10 的轮子/安全更新会逐步消失；且 pythonnet 3.2+ 要求 ≥3.11。**新的打包基线建议 Python 3.12**（安全支持到 2028-10）。

### 2.5 当前就坏的一个点：Windows 冻结后 pythonnet 失效

`release/PicturePick-WebView2` 在部分机器报
`Failed to resolve Python.Runtime.Loader.Initialize`（README / `docs/VERIFICATION.md` 已记录）。
根因与社区一致：冻结后 pythonnet 默认走 .NET Framework，失败后 pywebview 只设置了 `PYTHONNET_RUNTIME=coreclr` 却**没有 runtimeconfig**，CLR 起不来 `Microsoft.WindowsDesktop.App`，下一行 `clr.AddReference('System.Windows.Forms')` 必失败；且旧的 WebView2 程序集是 net462 版，强开 coreclr 会 `TypeLoadException`。
上游修复 **PR #1832**（coreclr 优先 + netfx 回退 + `netcoreapp3.0` 程序集 + `pywebview-runtimeconfig.json` + 冻结路径解析，作者注明在 PyInstaller bundle 内实测通过）已于 **2026-10-19 合入 master**，但 **6.2.1 之后没有新 release**。→ 升级 `pip install pywebview` 解决不了，需要固定 git commit / 手工移植 / 等下一版。

---

## 3. 候选方案总表

| # | 方案 | 三平台 | 技术栈改动 | 产物体积（本项目量级） | 签名门槛 | 估计工作量 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **A** | **PyInstaller + pywebview**（沿用现有壳） | ✅ 需三平台各自构建 | **最小**（壳代码已有，补平台分支与 spec） | Win 33 MB 目录 / 17 MB zip；mac ~40–60 MB；Linux deb ~40–60 MB | mac 需 Apple 账号（可选不签）；Win 可选 | **10–13 人日** | **推荐主线** |
| **B** | **浏览器模式 + PyInstaller 后端包** | ✅ | 极小（无 webview 依赖，后端已有） | onefile/onedir ~20–35 MB/平台 | 无（不涉及 .app） | **1–2 人日** | **最快兜底，建议先做** |
| C | Electron + electron-builder（已有代码） | ✅ 需三平台构建 | 中（已有 `desktop/` 可复用；mac/linux 需补配置） | portable 94.8 MB / NSIS 106.5 MB / 解包 346 MB（实测） | mac 同上；Win 可选 | 3.5–5 人日 | 次选；体积/维护税是主要阻力 |
| D | Tauri 2 + Python sidecar | ✅ 需三平台构建 | 大（新增 Rust 外壳 + 进程管理 + 桥接重写） | Win ~40–60 MB；mac ~40–70 MB；Linux AppImage 100–140 MB | mac 同上 | 6–10 人日 | 长期"正规军"，非当前必需 |
| E | Nuitka 替换 PyInstaller | ✅ | 中（换构建器 + C 工具链） | 与 PyInstaller 同量级 | 同 A | 1–2 人日 Spike | 暂不采用（不解决交叉编译，收益非刚需） |
| F | uv / python-build-standalone 绿色目录 | ✅ | 中（自建运行时 + 启动器） | ~30–50 MB/平台 | mac 需 .app + 签名 | 3.5–5 人日 | 备选兜底（无自动更新） |
| G | Neutralinojs / Wails v2 / Briefcase / PyOxidizer | 部分 | 大 | — | — | — | **排除**（不解决 Python 分发 / 需自写模板 / 已停滞） |

> 说明：所有方案共享同一张「三平台构建矩阵」；壳的选择只改变产物形态与体积，不会减少 CI 矩阵规模。

---

## 4. 方案 A（推荐）：PyInstaller + pywebview 三平台

### 4.1 三平台矩阵

| 维度 | Windows x64 | macOS（arm64 / x64） | Linux x64 |
| --- | --- | --- | --- |
| 渲染引擎 | Edge WebView2（系统） | WKWebView（系统） | WebKitGTK 4.1（系统）或 QtWebEngine |
| Python 依赖 | pythonnet（+ .NET Framework/CoreCLR） | PyObjC 框架（Cocoa/WebKit/Quartz/UTI） | PyGObject + 系统 `gir1.2-webkit2-4.1`（pip 装不上）或 PySide6 |
| 构建机 | Windows 本机 / `windows-2022`·`windows-2025` | **必须 mac**：`macos-26`（arm64）、`macos-26-intel`（x64）；或 universal2 需 universal2 Python | `ubuntu-22.04`（glibc 2.35 基线）或 Docker |
| 产物 | `PicturePick-windows-x64.zip`（绿色版），可选 NSIS | `PicturePick-macos-arm64.dmg` / `.zip`；Intel 单独一份 | `PicturePick-linux-x64.deb` + `.tar.gz`；AppImage 后置 |
| 运行时依赖 | Win10/11 自带 WebView2（可选用 `WEBVIEW2_RUNTIME_PATH` 内嵌固定版） | 系统自带 WKWebView | **必须**系统 `libwebkit2gtk-4.1`/`gir1.2-webkit2-4.1`（deb 用 Depends 声明） |
| 体积（估计） | 33 MB 目录 / 17 MB zip（实测） | 40–60 MB | 40–60 MB（不含 WebKitGTK） |
| 签名 | 可选（不签名=SmartScreen 警告） | **建议** Developer ID + 公证；不签名需用户手动放行 | 不强制 |
| 当前状态 | 已可用，但 pythonnet 冻结需修（P0） | 未做（spec + BUNDLE + CI） | 未做（spec + deb + CI） |

### 4.2 需要改的东西（按文件）

| 文件/模块 | 改动 |
| --- | --- |
| `desktop_webview/picturepick-desktop.spec` | 按 `sys.platform` 分支：Windows 保留 pythonnet/WebView2 收集并去掉重复 `collect_all`；macOS 移除 `cocoa` 排除、补 pyobjc `hiddenimports` + `BUNDLE()` + Info.plist/.icns；Linux 移除 `gi`/`gtk` 排除、用 GTK 后端、`--add-binary libpython3.x.so` |
| `desktop_webview/requirements.txt` | 按平台拆分（win: pywebview+pythonnet；mac: pyobjc 框架；linux: PyGObject 或 PySide6） |
| `scripts/build.ps1` | 抽象为跨平台 `scripts/build.py`（前端 → 按平台 PyInstaller → 产物组装/压缩），`build.ps1` 保留为 Windows 包装 |
| `desktop_webview/picturepick_desktop/desktop.py` | `user_data_dir`/`open_path` 平台化；Windows 专用自检/截图钩子加平台守卫；`?desktop=webview2` 改通用标记 |
| `backend/picturepick/fsbrowse.py` | **macOS Tk 主线程限制**：该接口是同步 `def`，会在 anyio 线程池里调 `tkinter` → mac 上可能失败。浏览器模式需改为主线程调度或改用平台原生对话框（`osascript`/`zenity`），或文档要求 `--dir` 启动 |
| `.github/workflows/`（新增） | 三平台矩阵：`windows-2025`、`ubuntu-22.04`、`macos-26` + `macos-26-intel`；缓存 pip/npm；产物 artifact 上传 |
| 文档 | README 三平台安装/放行/已知限制 |

### 4.3 Windows P0：修 pythonnet 冻结（1–3 人日）

推荐顺序：

1. **升级构建 Python 到 3.12**（3.10 已 EOL；顺带解锁 pythonnet 3.2+）。
2. **固定 pywebview 到含 #1832 的 master commit**：`pywebview @ git+https://github.com/r0x0r/pywebview@ec11e828642069eb9b946c1e56dbf0fd7b6e7299`（merge commit），或等下一版 release 后改回 PyPI。
3. spec 里确保收集 `webview/lib/netcoreapp3.0/*.dll` 与 `pywebview-runtimeconfig.json`；**去掉 `collect_all('pythonnet')` 造成的多份 DLL 风险**，改用官方 hook + runtime hook（设 `PYTHONNET_PYDLL`、`os.add_dll_directory`，参考社区 `pyi_rth_pythonnet.py`）。
4. 真机验收：`PicturePick.exe --selftest` PASS + 窗口截图 + 文件夹对话框可用；在"只有 .NET Framework"和"只有 .NET 6/8/9 桌面运行时"两类机器上各测一次。

### 4.4 macOS（3–4 人日）

- PyInstaller `BUNDLE()` 产出 `.app`；`Info.plist` 设 `CFBundleIdentifier`/`CFBundleShortVersionString`/`LSMinimumSystemVersion`/`NSHighResolutionCapable`；图标 `.icns`；hiddenimports 至少 `objc, AppKit, Foundation, WebKit, Cocoa, Quartz, Security, UniformTypeIdentifiers, webview.platforms.cocoa`（可参考社区完整 spec）。
- 先用 **ad-hoc 签名**跑通（Apple Silicon 未签名应用无法启动，ad-hoc 是底线）；正式分发再加 Developer ID + notarize + staple。
- 架构：优先 `macos-26`（arm64）+ `macos-26-intel`（x64）各出一份，避免 universal2 对 Python/依赖 fat 二进制的严苛要求（任一依赖缺 slice 直接构建失败）。

### 4.5 Linux（3–4 人日起步）

- **首选 deb + tar.gz**：PyInstaller onedir + `dpkg-deb` 生成包，`Depends: libwebkit2gtk-4.1-0 | gir1.2-webkit2-4.1, python3-gi`（按实际），安装即用；tar.gz 给非 Debian 系手动解压。
- 构建固定在 **`ubuntu-22.04`**（glibc 2.35）以兼容 Ubuntu 22.04+/Debian 12+；不要在 24.04 上构建后指望老系统能跑。
- **未验证点（先做 0.5–1 天 Spike）**：CI 里 Python 3.12（setup-python）+ pip 编译 PyGObject + 系统 `libwebkit2gtk-4.1-dev` 的组合能否稳定产出可运行包；apt 的 `python3-gi` 与 3.12 解释器 ABI 不匹配，需 pip 路线或改用系统 Python。
- **AppImage 自包含是另一个量级**（5–10 人日）：要把 WebKitWebProcess/NetworkProcess/GPUProcess + injected bundle 一起打包，并在 AppRun 里设 `WEBKIT_EXEC_PATH`、`WEBKIT_INJECTED_BUNDLE_PATH`、`GI_TYPELIB_PATH`、`GSETTINGS_SCHEMA_DIR`、`GIO_MODULE_DIR` 并打 RPATH（社区先例体积 64→107 MB）；**建议后置**，除非"零依赖单文件"是硬需求。
- 备选：Qt WebEngine 后端（`PYWEBVIEW_GUI=qt` + PySide6）可自包含，代价 150–250 MB。

### 4.6 方案 A 小结

- 优点：复用现有壳与 Windows 已验证路径；体积最小；三平台功能一致（同一份前端 + 同一套原生对话框 API）；不引入新语言。
- 缺点：三套 WebView 引擎 = 三份兼容性测试；Linux 依赖系统 WebKitGTK（或体积暴涨）；mac 需签名/公证；pythonnet 冻结需先修。
- 不改变"三平台各自构建"的成本结构。

---

## 5. 方案 B（最快兜底）：浏览器模式 + PyInstaller 后端包

项目本来就支持「`python -m picturepick serve` + 浏览器」且功能完整（照片不出本机，后端托管前端）。把后端打成免装 Python 的可执行包，就是跨平台最小交付：

- 构建：PyInstaller（或 PBS 绿色目录）打 `picturepick` CLI，**包含 tkinter**（浏览器模式的 `/api/dialog/pick-directory` 需要；现有 webview spec 把 tkinter 排除了）；前端产物内嵌到 `sys._MEIPASS/webui`（与现有 webview spec 一致）。
- 产物：`PicturePick-<platform>-<arch>.zip`（解压后双击 `PicturePick` / `PicturePick.exe` / `PicturePick.command` 自动开浏览器）。
- 体积估计 20–35 MB/平台；工作量 1–2 人日（含三平台 CI）。
- 已知限制：
  - 无原生窗口，是浏览器标签页；
  - **macOS 上 tkinter 不能在非主线程调用**，当前 `fsbrowse.pick_directory()` 在 uvicorn 线程池里执行 → mac 浏览器模式的"选择文件夹"很可能返回 `DIALOG_UNAVAILABLE`（前端提示改用 `--dir` / 桌面版）。要彻底解决需把对话框改到主线程或改用 `osascript`（+0.5–1 人日）。
- 定位：**先让 mac/Linux 用户能用**的过渡交付；也可作为 CI 冒烟测试的载体（不依赖 GUI）。

---

## 6. 方案 C（次选）：Electron + electron-builder

- 现状：`desktop/` 已有完整实现（Electron 33.4.11 + electron-builder 26.18.0 + PyInstaller sidecar），**Windows 打包链路实测已通**：portable 94.8 MB / NSIS 106.5 MB / 解包 346.1 MB。
- 本机"Electron 启动即崩"的根因是 **DSH 沙箱禁止命名管道（Chromium Mojo）**，不是 Electron 缺陷；正常 Windows 上可用。
- 三平台：electron-builder 支持 nsis/portable、dmg/zip、AppImage/deb/rpm；mac 仍需 mac 构建 + 签名/公证；Linux 可用 Docker 构建。
- 代价：体积 3–6 倍于 pywebview 方案、内存更高、**只有最新 3 个 stable 大版本受支持（约每 16 周要跟一次大版本）**（[Electron timelines](https://www.electronjs.org/docs/latest/tutorial/electron-timelines)）。
- 何时值得切：需要三平台完全一致的 Chromium 渲染、需要 `electron-updater` 静默自动更新、或出现系统 WebView 兼容性事故。

---

## 7. 方案 D（长期正规军）：Tauri 2 + Python sidecar

- 版本基线：Tauri **2.12.5**（2026-10-21，MSRV Rust 1.90）；官方 [sidecar 文档](https://v2.tauri.app/develop/sidecar/)直接把 "Python API server bundled using pyinstaller" 列为典型用例。
- bundle 目标：msi/nsis、app/dmg、deb/rpm/AppImage；mac 构建 + 公证、Linux 依赖 `libwebkit2gtk-4.1` 与 Tauri 一致。
- 必须自己写的部分：sidecar 生命周期（空闲端口、`--health` 轮询、退出 kill；**PyInstaller onefile 只拿得到 bootloader PID**，要用 `Command::set_process_group(true)` 或 `taskkill /T` 兜底）、前端与 sidecar 的端口/CORS 桥接、原生对话框换 `plugin-dialog`。
- 估 **6–10 人日**（不含 Rust 学习曲线、不含 Apple 账号等待）；团队纯 Python，长期维护 Rust 外壳是新增负担。
- 何时值得：确定要正式三平台发版（安装器、自动更新、统一体验），且愿意承担 Rust 维护。

---

## 8. 其它候选（结论）

| 候选 | 结论 | 理由 |
| --- | --- | --- |
| Nuitka（替换 PyInstaller） | 暂不采用，可留 1–2 天 Spike | 不解决交叉编译；新增 C 工具链与更长构建；收益（启动/防逆向）非本项目痛点；仍要面对 pythonnet 冻结 |
| uv / python-build-standalone 绿色目录 | 备选兜底 | 三平台可用、解压即用；但无自动更新、mac 仍需 `.app` 壳与签名；适合"不想用打包器"的场景 |
| Neutralinojs | 排除 | 只解决壳，不解决 Python 分发；生态与系统 WebView 兼容问题同 Tauri |
| Wails v2 | 排除 | 引入 Go 工具链；对本项目无收益 |
| Briefcase | 排除 | 面向 Toga/PySide 这类 Python GUI，需自写模板；无内建 webview 支持 |
| PyOxidizer | 排除 | 最新 release 停在 2022-12-30，仓库 2024-12 后无 push |

---

## 9. 推荐路线图（含工作量与验收）

> 估算口径：1 人、熟悉本项目；不含 Apple 账号审核/证书等待、不含 CI 配额审批。

### Phase 0 — Windows 修复 + 构建基线升级（2–3 人日，P0）

- 升构建 Python 3.12；固定含 #1832 的 pywebview；spec 去重收集 + runtime hook。
- 验收：`--selftest` PASS；窗口/目录框/另存为/导出全流程回归；在两类 .NET 环境机器上各跑一次。
- 产物不变：`PicturePick-WebView2-win-x64.zip`。

### Phase 1 — 跨平台构建脚本 + CI 骨架（1–2 人日）

- `scripts/build.py` + `.github/workflows/build.yml`（`windows-2025`、`ubuntu-22.04`、`macos-26`、`macos-26-intel`）。
- 验收：四个 job 各自产出 artifact（先允许 mac/linux 产物是"后端包"或失败可容忍，作为基线）。

### Phase 2 — Linux deb + tar.gz（3–4 人日）

- spec 平台分支 + GTK 依赖 + `libpython3.x.so`；`dpkg-deb` 元数据；`xvfb-run` 冒烟（健康接口 + 首页 + 截图）。
- 验收：Ubuntu 22.04 / 24.04 真机安装 deb 后启动窗口，完成"选目录 → 选片 → 导出"。
- 产物：`PicturePick-linux-x64.deb`、`PicturePick-linux-x64.tar.gz`。

### Phase 3 — macOS .app / dmg（3–4 人日）

- `BUNDLE()` spec + pyobjc hiddenimports + Info.plist/.icns；ad-hoc 签名先跑通；`macos-26`/`macos-26-intel` 各出包。
- 验收：Finder 双击 `.app` 启动，完成全流程；记录 Gatekeeper 行为。
- 产物：`PicturePick-macos-arm64.dmg`、`PicturePick-macos-x64.dmg`（或 universal2，视依赖 fat 情况）。

### Phase 4 — 可选增强

- 浏览器模式兜底包（若 Phase 2/3 延期，先发这个让用户能用）：1–2 人日。
- macOS Developer ID + 公证（2–3 人日 + $99/年）。
- Linux AppImage 自包含 / Qt 后端（5–10 人日）。
- 正式安装器（Windows NSIS/Inno、mac dmg 美化、Linux deb 仓库）：2–3 人日。
- Tauri 评估（若确定要长期三平台 + 自动更新）：6–10 人日另立项。

**合计到"三平台可安装可启动（不签名、Linux=deb）"：约 9–13 人日**；加签名与自包含另计。

---

## 10. 风险登记表

| # | 风险 | 概率 | 影响 | 缓解 |
| --- | --- | --- | --- | --- |
| 1 | Windows pythonnet 冻结修不好 / 等不到 pywebview 发版 | 中 | **高（现有用户直接不可用）** | 固定 #1832 commit；必要时手工移植 runtimeconfig+程序集；兜底切 Qt 后端或浏览器模式 |
| 2 | 无 mac 构建机 / 无 Apple 账号 → mac 版无限期拖延 | 高 | 高 | 先上 GH Actions mac runner（无需自有 Mac）；账号 $99/年先评估；短期发"未签名 + 放行说明" |
| 3 | Linux WebKitGTK 依赖/版本问题（尤其 CI 里 PyGObject 与系统包 ABI） | 中 | 高 | 先做 Linux Spike；deb 用 Depends 声明；固定 ubuntu-22.04；真机验证 Ubuntu 22.04/24.04 + Fedora |
| 4 | mac 上 tkinter 主线程限制 → 浏览器模式选目录失败 | 高（mac 浏览器模式） | 中 | 桌面模式走 pywebview 对话框（不受影响）；浏览器模式改主线程/`osascript`，或文档要求 `--dir` |
| 5 | 三套 WebView 渲染差异导致前端回归 | 中 | 中 | 三平台冒烟截图基线；避免过新 CSS/JS；CI 里跑 DOM 断言 |
| 6 | 不签名导致用户安装受阻（SmartScreen / Gatekeeper） | 高 | 中 | 把"不签名"写成正式档位 + 图文放行步骤；后续再补证书 |
| 7 | CI 成本（mac runner 计费倍率、私有仓配额） | 中 | 中 | 只在 tag/release 触发 mac job；日常只跑 Linux/Windows；缓存依赖 |
| 8 | Python 3.10 EOL 带来的依赖断供 | 中 | 中 | Phase 0 升 3.12 |

---

## 11. 需要你拍板的问题

1. **优先级**：是先要「mac/Linux 用户马上能跑」（→ 先做方案 B，1–2 人日），还是直接做「三平台原生窗口」（→ 方案 A 全流程，约 2 周）？
2. **macOS 分发**：是否愿意买 Apple Developer（$99/年）做签名+公证？还是先接受"未签名、用户手动放行"？
3. **Linux 交付形态**：接受 **deb + 系统 WebKitGTK 依赖**（便宜、3–4 人日），还是必须 **AppImage 零依赖**（+5–10 人日）／Qt 自包含（体积 150–250 MB）？
4. **构建环境**：有可用的 GitHub Actions 吗（mac job 按倍率计费）？没有的话是否有 Mac 机器可用？
5. **是否同意把构建基线升到 Python 3.12**（3.10 已 EOL，且解锁 pythonnet 3.2+）？

---

## 12. 主要来源（均于 2026-10-25 查询）

**打包器 / pywebview**

1. PyInstaller 首页与手册（"not a cross-compiler"、平台矩阵、macOS .app/签名、Linux glibc 前向兼容）— <https://pyinstaller.org/en/stable/>
2. PyPI: pyinstaller 6.22.3 — <https://pypi.org/project/pyinstaller/>
3. pywebview 安装（三平台后端与系统依赖）— <https://pywebview.flowrl.com/guide/installation>
4. pywebview Freezing（Win/Linux→PyInstaller，mac→py2app；excludes 警告；libpython 坑）— <https://pywebview.flowrl.com/guide/freezing.html>
5. pywebview PR #1832（coreclr + netfx 回退、runtimeconfig、netcoreapp3.0 程序集，2026-10-19 merged 未发版）— <https://github.com/r0x0r/pywebview/pull/1832>
6. pywebview issue #1215（同款 `Failed to resolve Python.Runtime.Loader.Initialize`）— <https://github.com/r0x0r/pywebview/issues/1215>
7. pythonnet（3.2.x 要求 Python ≥3.11）— <https://pypi.org/project/pythonnet/>
8. Python 版本状态（3.10 于 2026-10-01 EOL）— <https://devguide.python.org/versions/>
9. GitHub Actions runner 镜像（macos-26/26-intel、macos-15-intel、ubuntu-22.04、windows-2025）— <https://github.com/actions/runner-images>

**Electron / Tauri**

10. electron-builder 多平台构建（"Don't expect ... on one platform"、mac 签名仅 mac）— <https://www.electron.build/v26/docs/features/multi-platform-build/>
11. Electron 支持时间线（最新 3 个 stable、8 周/版）— <https://www.electronjs.org/docs/latest/tutorial/electron-timelines>
12. Tauri sidecar（externalBin、target triple、Python/PyInstaller 用例）— <https://v2.tauri.app/develop/sidecar/>
13. Tauri 前置依赖（Linux webkit2gtk-4.1 等）— <https://v2.tauri.app/start/prerequisites/>
14. Tauri 分发（bundle 目标、签名、AppImage/glibc）— <https://v2.tauri.app/distribute/>

> 逐条证据（含 30+ 条来源、版本号、未核实清单）见 `.scratch/research/01-*.md`、`02-*.md`、`03-*.md`。

---

## 13. 版本基线速查（2026-10-25）

| 组件 | 版本 | 备注 |
| --- | --- | --- |
| Python（构建基线建议） | **3.12** | 当前项目 3.10 已 EOL |
| PyInstaller | 6.22.3（2026-09-12） | 支持 Python 3.8–3.15 |
| pywebview | 6.2.1（2026-04-15） | master 含未发版 #1832 |
| pythonnet | 3.1.0（3.2.x 需 Python ≥3.11） | 项目现为 3.1.0 |
| Pillow | 9.3.0（项目） | — |
| FastAPI / uvicorn / pydantic | 0.115.14 / 0.34.3 / 1.10.11（项目） | — |
| Electron / electron-builder | 45.0.0（2026-10-21）/ 26.18.0（2026-10-20） | 项目历史实现为 33.4.11 |
| Tauri | 2.12.5（2026-10-21，MSRV Rust 1.90） | 3.0 仍是 alpha |
