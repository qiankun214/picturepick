# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：PicturePick 桌面版（PyInstaller + pywebview/WebView2）。

产物：build/webview/PicturePick/PicturePick.exe（onedir）
内嵌：
  * Python 后端 picturepick 包（FastAPI/uvicorn/Pillow/pydantic）
  * 前端构建产物 backend/picturepick/webui → _MEIPASS/webui
  * pywebview + pythonnet（Edge WebView2 后端）
"""

from pathlib import Path

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# 调试时可设置 PICTUREPICK_SPEC_CONSOLE=1 构建带控制台的版本，便于查看 traceback
CONSOLE = os.environ.get("PICTUREPICK_SPEC_CONSOLE") == "1"
COLLECT_NAME = "PicturePickConsole" if CONSOLE else "PicturePick"

ROOT = Path(SPECPATH).resolve().parent          # E:\picturePick
BACKEND = ROOT / "backend"
WEBUI = BACKEND / "picturepick" / "webui"

datas = []
binaries = []
hiddenimports = []

# 前端静态资源：后端在 PyInstaller 环境下从 sys._MEIPASS/webui 读取
if not (WEBUI / "index.html").exists():
    raise SystemExit(f"前端尚未构建：缺少 {WEBUI / 'index.html'}，请先执行 npm run build（frontend/）")
datas.append((str(WEBUI), "webui"))

# pywebview / pythonnet 运行时
for package in ("webview", "pythonnet", "clr_loader", "proxy_tools", "bottle"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception as exc:  # noqa: BLE001 - 缺少可选依赖时继续（构建会暴露问题）
        print(f"[spec] collect_all({package}) 跳过：{exc}")

hiddenimports += collect_submodules("webview.platforms")
hiddenimports += [
    "clr",
    "pythonnet",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    # 后端（FastAPI/uvicorn 动态导入）
    "picturepick",
    "picturepick.app",
    "picturepick.api",
    "picturepick.cli",
    "picturepick.export",
    "picturepick.fsbrowse",
    "picturepick.imaging",
    "picturepick.models",
    "picturepick.scanner",
    "picturepick.session",
    "picturepick.errors",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "fastapi",
    "starlette",
    "pydantic",
    "PIL.Image",
    "PIL.ImageDraw",
]

a = Analysis(
    [str(ROOT / "desktop_webview" / "picturepick_desktop" / "__main__.py")],
    pathex=[str(BACKEND), str(ROOT / "desktop_webview")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "unittest", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PicturePick",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "desktop" / "resources" / "icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=COLLECT_NAME,
)
