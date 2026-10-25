"""FastAPI 应用装配：CORS、API 路由、SPA 静态资源（docs/API.md §5）。

静态资源目录定位规则（PyInstaller 兼容）：

* 打包后：``sys._MEIPASS/webui``
* 源码运行：``picturepick/webui``
* 目录不存在时 ``GET /`` 返回 200 JSON 提示（前端未构建），其余路径 404 JSON。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, api
from .birdid import BirdService
from .errors import ApiError
from .session import SessionStore

#: 仅允许 localhost / 127.0.0.1 任意端口（开发期 Vite 5173 跨域）
CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

_JSON_HEADERS = {"Cache-Control": "no-store"}


def default_webui_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "webui"
    return Path(__file__).resolve().parent / "webui"


def _json_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
        headers=_JSON_HEADERS,
    )


def _safe_static_path(webui_dir: Path, relative: str) -> Optional[Path]:
    """把 URL 相对路径安全地解析到 webui 目录内，越界返回 None。"""
    if not relative:
        return None
    try:
        root = webui_dir.resolve()
        target = (root / relative).resolve()
        root_text = os.path.normcase(str(root))
        target_text = os.path.normcase(str(target))
        if os.path.commonpath([root_text, target_text]) != root_text:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _install_webui(app: FastAPI, webui_dir: Path) -> None:
    webui_dir = Path(webui_dir)
    app.state.webui_dir = webui_dir

    assets_dir = webui_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="webui-assets")

    @app.get("/", include_in_schema=False)
    def webui_index():
        index_file = webui_dir / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "name": "picturepick",
                "version": __version__,
                "message": "PicturePick API is running; the web UI has not been built yet.",
                "webui": str(webui_dir),
            },
            headers=_JSON_HEADERS,
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    def webui_fallback(full_path: str):
        # API 前缀的未知路径不能被 SPA fallback 吞掉
        if full_path == "api" or full_path.startswith("api/"):
            return _json_error(404, "NOT_FOUND", f"接口不存在: /{full_path}")
        candidate = _safe_static_path(webui_dir, full_path)
        if candidate is not None and candidate.is_file():
            return FileResponse(str(candidate))
        index_file = webui_dir / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return _json_error(404, "NOT_FOUND", f"资源不存在: /{full_path}")


def create_app(
    webui_dir: Optional[Union[str, Path]] = None,
    store: Optional[SessionStore] = None,
    bird_service: Optional[BirdService] = None,
) -> FastAPI:
    """创建 FastAPI 应用（测试可注入 webui 目录、会话仓库与识鸟服务）。"""
    app = FastAPI(
        title="PicturePick",
        version=__version__,
        description="PicturePick local photo culling service",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.store = store if store is not None else SessionStore()
    #: AI 识鸟服务：后台任务队列 + 配置 + EXIF 写入（docs/BIRDID-CONTRACT.md）
    app.state.bird = bird_service if bird_service is not None else BirdService(app.state.store)
    app.state.webui_dir = Path(webui_dir) if webui_dir is not None else default_webui_dir()

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    app.include_router(api.router, prefix="/api")
    _install_webui(app, app.state.webui_dir)
    return app
