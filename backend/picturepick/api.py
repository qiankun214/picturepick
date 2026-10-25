"""全部 API 路由实现（docs/API.md §3、§4）。"""

from __future__ import annotations

import os
from typing import List, Literal

from fastapi import APIRouter, Query, Request, Response

from . import __version__, export, fsbrowse, imaging, scanner
from .errors import (
    DIALOG_UNAVAILABLE,
    INVALID_PATH,
    NO_PHOTOS,
    PHOTO_NOT_FOUND,
    SESSION_NOT_FOUND,
    ApiError,
)
from .models import (
    CreateSessionRequest,
    CurrentSessionResponse,
    DialogResult,
    ExportRequest,
    ExportResponse,
    FinishResponse,
    FsListResponse,
    FsRootsResponse,
    HealthResponse,
    PhotoListResponse,
    PickResult,
    SessionSummary,
)
from .session import Session, SessionStore

router = APIRouter()

MAX_PAGE_LIMIT = 2000


# ---- 内部辅助 --------------------------------------------------------------


def _store(request: Request) -> SessionStore:
    return request.app.state.store


def _session(request: Request, session_id: str) -> Session:
    store = _store(request)
    session = store.get(session_id)
    if session is None:
        raise ApiError(SESSION_NOT_FOUND, "会话不存在或已过期")
    return session


# ---- 系统接口 --------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, summary="健康检查")
def health() -> HealthResponse:
    return HealthResponse(status="ok", name="picturepick", version=__version__)


@router.get("/fs/roots", response_model=FsRootsResponse, summary="盘符列表")
def fs_roots() -> FsRootsResponse:
    return fsbrowse.get_roots()


@router.get("/fs/list", response_model=FsListResponse, summary="列出子目录")
def fs_list(path: str = "") -> FsListResponse:
    return fsbrowse.list_directories(path)


@router.post("/dialog/pick-directory", response_model=DialogResult, summary="原生目录选择框")
def dialog_pick_directory() -> DialogResult:
    return fsbrowse.pick_directory_result()


# ---- 会话接口 --------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=SessionSummary,
    status_code=201,
    summary="扫描目录并创建会话",
)
def create_session(payload: CreateSessionRequest, request: Request) -> SessionSummary:
    try:
        result = scanner.scan(payload.directory, recursive=payload.recursive)
    except scanner.InvalidDirectoryError as exc:
        raise ApiError(INVALID_PATH, str(exc)) from exc
    except scanner.NoPhotosError as exc:
        raise ApiError(NO_PHOTOS, str(exc)) from exc
    session = _store(request).create(
        root=result.root,
        recursive=result.recursive,
        photos=result.photos,
    )
    return session.summary()


@router.get("/sessions/current", response_model=CurrentSessionResponse, summary="当前会话")
def current_session(request: Request) -> CurrentSessionResponse:
    session = _store(request).current()
    return CurrentSessionResponse(session=session.summary() if session is not None else None)


@router.get("/sessions/{session_id}", response_model=SessionSummary, summary="会话摘要")
def get_session(session_id: str, request: Request) -> SessionSummary:
    return _session(request, session_id).summary()


@router.get(
    "/sessions/{session_id}/rounds/{index}/photos",
    response_model=PhotoListResponse,
    summary="分页获取某轮照片",
)
def list_round_photos(
    session_id: str,
    index: int,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=MAX_PAGE_LIMIT),
) -> PhotoListResponse:
    session = _session(request, session_id)
    round_ = session.get_round(index)
    photo_ids: List[str] = round_.photo_ids[offset : offset + limit]
    return PhotoListResponse(
        roundIndex=index,
        total=len(round_.photo_ids),
        offset=offset,
        limit=limit,
        photos=[session.photo_item(photo_id) for photo_id in photo_ids],
    )


@router.get("/sessions/{session_id}/photos/{photo_id}/image", summary="照片原图 / 缩略图")
def get_photo_image(
    session_id: str,
    photo_id: str,
    request: Request,
    variant: Literal["original", "thumb"] = "original",
) -> Response:
    session = _session(request, session_id)
    photo = session.get_photo(photo_id)
    if not scanner.is_within(session.root, photo.absolute_path) or not os.path.isfile(photo.absolute_path):
        raise ApiError(PHOTO_NOT_FOUND, "照片文件不存在或不可读")
    if variant == "thumb":
        data = imaging.thumbnail_bytes(photo.absolute_path)
    else:
        data = imaging.original_bytes(photo.absolute_path)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post(
    "/sessions/{session_id}/photos/{photo_id}/viewed",
    response_model=SessionSummary,
    summary="标记本轮已浏览（幂等）",
)
def mark_photo_viewed(session_id: str, photo_id: str, request: Request) -> SessionSummary:
    session = _session(request, session_id)
    session.mark_viewed(photo_id)
    return session.summary()


@router.put(
    "/sessions/{session_id}/photos/{photo_id}/pick",
    response_model=SessionSummary,
    summary="pick 照片（幂等）",
)
def pick_photo(session_id: str, photo_id: str, request: Request) -> SessionSummary:
    session = _session(request, session_id)
    session.pick(photo_id)
    return session.summary()


@router.delete(
    "/sessions/{session_id}/photos/{photo_id}/pick",
    response_model=SessionSummary,
    summary="取消 pick（幂等）",
)
def unpick_photo(session_id: str, photo_id: str, request: Request) -> SessionSummary:
    session = _session(request, session_id)
    session.unpick(photo_id)
    return session.summary()


@router.post(
    "/sessions/{session_id}/rounds/next",
    response_model=SessionSummary,
    summary="进入下一轮",
)
def next_round(session_id: str, request: Request) -> SessionSummary:
    return _session(request, session_id).next_round()


@router.post(
    "/sessions/{session_id}/finish",
    response_model=FinishResponse,
    summary="结束并生成结果（幂等）",
)
def finish_session(session_id: str, request: Request) -> FinishResponse:
    return _session(request, session_id).finish()


@router.get("/sessions/{session_id}/result", response_model=PickResult, summary="获取结果 JSON")
def get_result(
    session_id: str,
    request: Request,
    download: str = Query("0", description="download=1 时以附件下载"),
):
    session = _session(request, session_id)
    result = session.result()
    if download.strip().lower() in {"1", "true", "yes", "on"}:
        filename = export.download_filename(session.root)
        return Response(
            content=export.result_json_bytes(result),
            media_type="application/json",
            headers={"Content-Disposition": export.content_disposition(filename)},
        )
    return result


@router.post(
    "/sessions/{session_id}/export",
    response_model=ExportResponse,
    summary="导出结果到本机文件",
)
def export_result(session_id: str, payload: ExportRequest, request: Request) -> ExportResponse:
    session = _session(request, session_id)
    target = export.write_result_file(payload.path, session.result())
    return ExportResponse(path=target)
