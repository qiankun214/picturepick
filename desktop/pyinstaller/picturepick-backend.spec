# -*- mode: python ; coding: utf-8 -*-
"""PicturePick 后端 sidecar 的 PyInstaller 打包配置（onedir）。

用法（必须从 desktop/ 目录执行，保证 --distpath 相对路径正确）：
    npm run pack:backend
即：
    python -m PyInstaller --noconfirm --clean \
        --distpath ../build --workpath ../build/.pyinstaller-work \
        pyinstaller/picturepick-backend.spec

产物布局：
    E:\\picturePick\\build\\backend\\picturepick-backend.exe        <- 主程序
    E:\\picturePick\\build\\backend\\_internal\\...                 <- Python 运行时与依赖

说明：
  * 入口使用 pyinstaller/backend_entry.py（而不是直接分析 cli.py）。
    因为 cli.py 内部使用相对导入，若作为顶层脚本执行会抛
    "attempted relative import with no known parent package"，
    所以用一个只做 `from picturepick.cli import main` 的薄入口，语义与
    `python -m picturepick` 完全一致。
  * datas 内嵌 backend/picturepick/webui；前端尚未构建时跳过并打印警告，
    此时后端 / 只会返回“前端未构建”的 JSON 提示。
  * hiddenimports 覆盖 uvicorn 的工厂式动态导入（loops/protocols/lifespan），
    这是 PyInstaller 打包 uvicorn 应用最常见的坑。
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# PyInstaller 在 spec 执行时注入这些全局变量
SPEC_DIR = SPECPATH  # noqa: F821  # type: ignore[name-defined]
DESKTOP_DIR = os.path.dirname(SPEC_DIR)
ROOT_DIR = os.path.dirname(DESKTOP_DIR)
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
ENTRY_SCRIPT = os.path.join(SPEC_DIR, "backend_entry.py")
WEBUI_DIR = os.path.join(BACKEND_DIR, "picturepick", "webui")
ICON_PATH = os.path.join(DESKTOP_DIR, "resources", "icon.ico")
VERSION_FILE = os.path.join(SPEC_DIR, "version_info.txt")

# ---------------------------------------------------------------- datas ----
datas = []
if os.path.isdir(WEBUI_DIR) and os.listdir(WEBUI_DIR):
    datas.append((WEBUI_DIR, "webui"))
    print(f"[picturepick-backend] 内嵌前端构建产物：{WEBUI_DIR}")
else:
    print(
        "[picturepick-backend] 警告：前端构建产物不存在或为空 "
        f"({WEBUI_DIR})，本次打包后端将只提供 API，/ 返回 JSON 提示。"
        " 请先在 frontend/ 执行 npm run build 后重新 pack:backend。"
    )

# ------------------------------------------------------------- hiddenimports
hiddenimports = [
    # uvicorn 动态加载的循环 / 协议 / 生命周期实现
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    "uvicorn.config",
    "uvicorn.main",
    "uvicorn.server",
    "uvicorn.importer",
    # FastAPI / Starlette / Pydantic 常见的延迟导入
    "fastapi",
    "fastapi.responses",
    "fastapi.staticfiles",
    "fastapi.middleware.cors",
    "starlette",
    "starlette.staticfiles",
    "starlette.middleware",
    "starlette.middleware.cors",
    "pydantic",
    "pydantic.deprecated.decorator",
    "anyio",
    "anyio._backends._asyncio",
    # 解释器 / 点号命令行
    "email.mime.multipart",
    "email.mime.text",
    "email.utils",
    "uuid",
    "hashlib",
    "secrets",
    # fsbrowse.py 使用 tkinter 调原生目录选择框（缺失时后端会返回 501，属于尽力而为）
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    # Pillow 可选编解码器（缩略图）
    "PIL",
    "PIL.Image",
    "PIL.ImageOps",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL.JpegImagePlugin",
]

# uvicorn 各版本模块布局略有差异，按实际安装内容补充全部子模块；
# 找不到的可选模块（httptools/websockets/wsproto 未安装时）只产生警告，不阻断构建。
hiddenimports += collect_submodules("uvicorn")
hiddenimports = sorted(set(hiddenimports))

# ------------------------------------------------------------------ build --
a = Analysis(  # noqa: F821
    [ENTRY_SCRIPT],
    pathex=[BACKEND_DIR, ROOT_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "httpx",
        "test",
        "tests",
        "unittest",
        "tkinter.test",
        "pydoc",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="picturepick-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    version=VERSION_FILE if os.path.exists(VERSION_FILE) else None,
    # 资源目录默认 _internal；后端通过 sys._MEIPASS 定位 webui
    contents_directory="_internal",
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    # --distpath 为 ../build，因此输出恰好是 build/backend/picturepick-backend.exe
    name="backend",
)
