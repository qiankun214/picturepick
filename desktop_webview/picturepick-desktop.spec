# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：PicturePick 桌面版（PyInstaller + pywebview/WebView2）。

产物：build/webview/PicturePick/PicturePick.exe（onedir）
内嵌：
  * Python 后端 picturepick 包（FastAPI/uvicorn/Pillow/pydantic）
  * 前端构建产物 backend/picturepick/webui → _MEIPASS/webui
  * pywebview（仅 Edge WebView2 后端）+ pythonnet 运行时

注意：pywebview 支持 Qt/GTK/CEF 等多种后端，本应用只用 Windows 的 Edge WebView2。
为避免把 PySide6（约 430MB）等无关可选依赖打进包里，这里**不使用 collect_all('webview')**，
只收集 webview 的 lib 数据 + 显式 hiddenimports，并在 excludes 中排除其它后端与无关大包。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent          # E:\picturePick
BACKEND = ROOT / "backend"
WEBUI = BACKEND / "picturepick" / "webui"
VERSION_FILE = ROOT / "desktop_webview" / "pyinstaller" / "version_info.txt"

CONSOLE = os.environ.get("PICTUREPICK_SPEC_CONSOLE") == "1"
COLLECT_NAME = "PicturePickConsole" if CONSOLE else "PicturePick"

datas = []
binaries = []
hiddenimports = []

# 前端静态资源：后端在 PyInstaller 环境下从 sys._MEIPASS/webui 读取
if not (WEBUI / "index.html").exists():
    raise SystemExit(f"前端尚未构建：缺少 {WEBUI / 'index.html'}，请先执行 npm run build（frontend/）")
datas.append((str(WEBUI), "webui"))

# pywebview：只需要 lib 数据（Microsoft.Web.WebView2.*.dll / WebView2Loader 等）
datas += collect_data_files("webview", includes=["lib/**", "lib/*"])
hiddenimports += [
    "webview",
    "webview.util",
    "webview.window",
    "webview.http",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

# pythonnet / clr_loader：.NET 运行时桥接（Python.Runtime.dll 等）
for package in ("pythonnet", "clr_loader"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden
hiddenimports += ["clr", "clr_loader", "pythonnet", "proxy_tools", "bottle"]

# 后端（FastAPI/uvicorn 动态导入）
hiddenimports += [
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

# 排除无关的可选后端与大依赖（WebView2 桌面壳不需要）
excludes = [
    "PySide6",
    "shiboken6",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "gi",
    "cairo",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "cryptography",
    "tkinter",
    "_tkinter",
    "tcl8",
    "tk8",
    "webview.platforms.qt",
    "webview.platforms.gtk",
    "webview.platforms.cef",
    "webview.platforms.android",
    "webview.platforms.mshtml",
    "webview.platforms.cocoa",
    "test",
    "unittest",
    "pytest",
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
    excludes=excludes,
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
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
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
