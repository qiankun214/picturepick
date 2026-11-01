# 升级 Python：3.10 → 3.12（Windows）

> 适用场景：本机只有 Python 3.10.11（`C:\Users\qiankun\AppData\Local\Programs\Python\Python310`），
> 希望**直接卸载 3.10、改装 3.12**，并把 `backend/` + `desktop_webview/` 重新跑通。
> 背景：Python 3.10 已于 **2026-10-01 EOL**；桌面版 pythonnet 3.2+ 要求 Python ≥ 3.11。
> 相关：跨平台打包调研见 [`PACKAGING-RESEARCH.md`](PACKAGING-RESEARCH.md)。

## 0. 影响面

| 会变 | 不变 |
| --- | --- |
| 全局 Python 解释器与全部全局 pip 包（fastapi/uvicorn/Pillow/pydantic/pywebview/pythonnet/PyInstaller/pytest 等需重装） | 前端 `frontend/`、Node/npm、`node_modules` |
| `build/`、`.pyinstaller-*` 等 3.10 时代的打包中间产物（需重新生成） | `release/PicturePick-WebView2/` 已打好的桌面版（自带 3.10 运行时，仍可作回退） |
| 用户 PATH 里指向 `Python310` 的两条路径 | 项目源码、照片、用户配置（`%APPDATA%\PicturePick\settings.json`） |

> **更稳的顺序**：先装 3.12 → 装依赖并跑通 `pytest` → 再卸 3.10。
> 先卸后装也能走通，只是中间会有一段「没有 Python」的窗口，且 `py` 启动器可能随 3.10 一起消失（装 3.12 时会补回）。

## 1. 卸载 Python 3.10

### 1.1 卸载程序

任选一种：

```powershell
# A. Windows 设置 → 应用 → 已安装的应用 → “Python 3.10.11” → 卸载（最直观）

# B. winget（若 winget 可用）
winget uninstall --id Python.Python.3.10 -e

# C. 用当初的安装包 python-3.10.11-amd64.exe → Modify/Repair 页面 → Uninstall
```

### 1.2 清理 PATH 残留（重要）

卸载器通常不会删干净用户 PATH。打开 **系统属性 → 环境变量 → 用户变量 Path → 编辑**，删除类似：

```
C:\Users\qiankun\AppData\Local\Programs\Python\Python310\Scripts\
C:\Users\qiankun\AppData\Local\Programs\Python\Python310\
```

保留 `C:\Users\qiankun\AppData\Local\Microsoft\WindowsApps` 之外，不要新增任何 Python 相关条目（3.12 安装器会自己加）。
也可用 PowerShell 查看当前用户 PATH：

```powershell
[Environment]::GetEnvironmentVariable('Path','User') -split ';' | Where-Object { $_ -match 'Python' }
```

### 1.3 清理残留目录（可选）

```powershell
# 主程序目录（卸载后可能残留）
Remove-Item 'C:\Users\qiankun\AppData\Local\Programs\Python\Python310' -Recurse -Force -ErrorAction SilentlyContinue

# pip --user 安装的包（3.10 专属，留着也没用）
Remove-Item 'C:\Users\qiankun\AppData\Roaming\Python\Python310' -Recurse -Force -ErrorAction SilentlyContinue

# pip 下载缓存（可选，删了会重新下载）
# Remove-Item 'C:\Users\qiankun\AppData\Local\pip\Cache' -Recurse -Force
```

`C:\Windows\py.exe`（py 启动器）可能随 3.10 卸载被移除；不用手动处理，装 3.12 时重新装即可。

## 2. 安装 Python 3.12

任选一种：

```powershell
# A. winget（推荐，装到当前用户）
winget install -e --id Python.Python.3.12

# B. Chocolatey（需要管理员）
choco install python312 -y

# C. 官网安装包
# 下载 https://www.python.org/downloads/windows/ 的 “Windows installer (64-bit)” 3.12.x
```

官网安装包界面里**务必勾选**：

- ☑ **Add python.exe to PATH**
- ☑ **py launcher**（Install launcher for all users 可勾可不勾）
- ☑ **tcl/tk and IDLE**（浏览器模式的「选择文件夹」用 tkinter，后续打包也要）

安装完成后**新开一个终端**（旧终端不会刷新 PATH）。

## 3. 验证解释器

```powershell
py -0p                         # 只应看到 3.12（3.10 已消失）
py -3.12 -V                    # Python 3.12.x
python -V                      # 也应是 3.12.x
where.exe python               # 第一行应是 ...\Programs\Python\Python312\python.exe
python -c "import sys; print(sys.executable)"
python -m pip -V               # 应指向 Python312 的 pip
```

**若 `python` 打开 Microsoft Store / 无输出**：说明命中了 `WindowsApps\python.exe` 别名。处理：
设置 → 应用 → 高级应用设置 → 应用执行别名 → 关闭 `python.exe`、`python3.exe`；或把 3.12 路径移到 PATH 最前。
（日常也可直接用 `py -3.12`，不依赖 PATH。）

## 4. 重装项目依赖

```powershell
cd E:\picturePick
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend\requirements.txt
python -m pip install -r desktop_webview\requirements.txt
python -m pip install pyinstaller
python -m pip check
```

> 网络慢/超时可加国内镜像：
> `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend\requirements.txt`
>
> 依赖约束已按 3.12 调整（见 `backend/requirements.txt`）：
> `pydantic>=1.10.15,<2`（锁 v1，避免装成 pydantic 2）、`Pillow>=10.4`（3.12 需要 ≥10.1）、
> `fastapi>=0.115.14,<0.116` / `uvicorn>=0.34.3,<0.35`（新版 FastAPI 不再支持 pydantic v1）。

检查关键版本：

```powershell
python -c "import sys, pydantic, PIL, fastapi, uvicorn; print(sys.version); print('pydantic', pydantic.VERSION); print('Pillow', PIL.__version__); print('fastapi', fastapi.__version__); print('uvicorn', uvicorn.__version__)"
# 期望：pydantic 1.10.x；Pillow >= 10.4；fastapi 0.115.x；uvicorn 0.34.x
python -c "import webview, pythonnet, PyInstaller; print('pywebview/pythonnet/PyInstaller ok')"
```

## 5. 验证项目

```powershell
# 单元/接口测试（当前基线 280 passed）
cd E:\picturePick\backend
python -m pytest -q

# 启动后端 + 浏览器
cd E:\picturePick
python -m picturepick serve --dir .\samples\photos
```

前端构建不变（Node 未动）：

```powershell
cd E:\picturePick\frontend
npm run build        # → backend/picturepick/webui
```

## 6. 重新打包桌面版

```powershell
cd E:\picturePick
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
# 产物：release\PicturePick-WebView2\PicturePick.exe

release\PicturePick-WebView2\PicturePick.exe --selftest-backend   # 应 PASS
```

> **注意**：`--selftest`（加载 pythonnet/WebView2 的深度自检）仍可能失败——这是
> `docs/PACKAGING-RESEARCH.md` 里记录的 Windows pythonnet 冻结问题，**与 Python 版本无关**，
> 需要后续把 pywebview 固定到含 PR #1832 的 commit（或等新版本）才能根治。
> 建议打包前删掉 3.10 时代的中间产物：`build\`、`build\.pyinstaller-*`（`--clean` 也会处理）。

## 7. 回滚

- 3.10 已卸载时：从 python.org 重新装 3.10.x（或 `winget install -e --id Python.Python.3.10`），
  再用旧版 `requirements` 装依赖（git 里可查历史版本）。
- 旧的 `release\PicturePick-WebView2\` 自带 Python 运行时，不受本机 Python 影响，可随时双击使用。
- 若想「不折腾全局环境」：以后可为项目建 `.venv`（`py -3.12 -m venv .venv`），
  但当前项目文档/脚本默认全局 `python`，二选一即可。

## 8. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `python` 打开应用商店 / 无输出 | WindowsApps 的 python 别名 | 关闭「应用执行别名」或调整 PATH 顺序 |
| `pip` 仍指向 `Python310\Scripts` | PATH 残留 / 旧终端 | 清 PATH、重开终端；统一用 `python -m pip` |
| `ModuleNotFoundError: fastapi/pydantic` | 装到了别的解释器 | `python -c "import sys;print(sys.executable)"` 确认后用同一解释器 `-m pip install` |
| `pydantic` 变成 2.x | 用了旧 requirements 或 `pip install pydantic` | `python -m pip install "pydantic<2"` 后复查 |
| pydantic 1.10 源码编译失败 | 3.12 没有对应 wheel | 装最新 1.10.x（`pip install "pydantic==1.10.*"`）；仍失败可临时用 Python 3.11 |
| 打包后报 `Failed to resolve Python.Runtime.Loader.Initialize` | pythonnet 冻结问题（#1832 未发版） | 见 `PACKAGING-RESEARCH.md` §4.3；与 Python 3.12 无关 |
| PyInstaller 报 bootloader/缓存错误 | 混用 3.10 的 build 缓存 | 删除 `build\`、`build\.pyinstaller-*` 后重打 |
| 打包版一启动就报 `ValueError: Unable to configure formatter 'default'`（`'NoneType' object has no attribute 'isatty'`） | PyInstaller 窗口模式（`console=False`）下 `sys.stdout/sys.stderr` 为 `None`，uvicorn 的 logging formatter 调用 `sys.stderr.isatty()` | 已在 `desktop_webview/picturepick_desktop/desktop.py` 用 `_LogStream` 兜底（写入 `desktop.log`）；旧包需重新打包 |
| 打包版能启动但选目录框失败 | WebView2 运行时缺失 / pythonnet 异常 | 装 Edge WebView2 Runtime；看 `%LOCALAPPDATA%\PicturePick\logs\desktop.log` |
| 其他项目依赖原来的 3.10 全局包 | 全局包随卸载丢失 | 给那些项目各建 `.venv`，或重新在 3.12 装依赖 |

## 9. 升级完成后的下一步（可选）

1. 按 `PACKAGING-RESEARCH.md` P0 修 Windows 冻结：把 `pywebview` 固定到含 #1832 的 commit（`ec11e828`），
   并调整 spec 收集 `netcoreapp3.0` 程序集与 `pywebview-runtimeconfig.json`。
2. 三平台构建：`scripts/build.py` + GitHub Actions 矩阵（Windows / Linux / macOS）。
