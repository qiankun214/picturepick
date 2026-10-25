"""Pydantic 数据模型：字段与 docs/API.md（第 2 版）§2/§3/§4 的 JSON 契约一一对应。

第 2 版要点：
* ``SessionSummary`` 增加 ``origin``（server/browser）与 ``sourceLabel``；
* ``PickResult`` 增加 ``origin`` 与 ``lastRound``（最后一轮仍保留的照片，供一键导出）；
* ``POST /api/sessions`` 请求体同时支持 browser 来源（照片元数据）与 server 来源（目录扫描）。

注意：运行环境安装的是 pydantic 1.10（FastAPI 0.115 同时兼容 v1/v2），
因此这里只使用 v1/v2 共有的写法（``BaseModel``、``Field``、``.dict()``）。
字段名直接使用契约中的 camelCase，避免别名转换带来的歧义。
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ---- 系统接口 --------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = "ok"
    name: str = "picturepick"
    version: str


class FsRoot(BaseModel):
    name: str
    path: str


class FsRootsResponse(BaseModel):
    home: str
    startPath: str
    roots: List[FsRoot] = Field(default_factory=list)


class FsEntry(BaseModel):
    name: str
    path: str
    isDir: bool


class FsListResponse(BaseModel):
    path: str
    parent: Optional[str] = None
    entries: List[FsEntry] = Field(default_factory=list)


class DialogResult(BaseModel):
    path: Optional[str] = None
    cancelled: bool


# ---- 会话 ------------------------------------------------------------------


class RoundSummary(BaseModel):
    index: int
    photoCount: int
    viewedCount: int
    pickedCount: int


class SessionSummary(BaseModel):
    id: str
    origin: str
    sourceLabel: str
    root: str
    recursive: bool
    createdAt: str
    phase: str
    roundIndex: int
    roundCount: int
    libraryTotal: int
    roundPhotoCount: int
    viewedCount: int
    pickCount: int
    rounds: List[RoundSummary] = Field(default_factory=list)


class PhotoItem(BaseModel):
    id: str
    name: str
    relativePath: str
    sizeBytes: int
    pickCount: int
    picked: bool
    viewed: bool


class PhotoListResponse(BaseModel):
    roundIndex: int
    total: int
    offset: int
    limit: int
    photos: List[PhotoItem] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    """POST /api/sessions 请求体（两种来源共用）。

    * ``source="server"``（默认）：``directory`` 必填，后端扫描真实目录；
    * ``source="browser"``：``label`` + ``photos`` 元数据，后端不读文件系统。

    ``photos`` 故意使用宽松的 ``Any``：具体格式校验由 scanner 完成，
    以便返回契约错误码 ``INVALID_PHOTOS`` 而不是 FastAPI 的 422。
    """

    source: str = "server"
    directory: Optional[str] = None
    label: Optional[str] = None
    recursive: bool = True
    photos: Optional[Any] = None


class CurrentSessionResponse(BaseModel):
    session: Optional[SessionSummary] = None


# ---- 最终结果 --------------------------------------------------------------


class PickResultPhoto(BaseModel):
    id: str
    name: str
    relativePath: str
    absolutePath: str
    pickCount: int
    pickedInRounds: List[int] = Field(default_factory=list)


class LastRound(BaseModel):
    """最后一轮仍被保留（pick）的照片，供前端「一键导出保留照片」使用。"""

    index: int
    pickedCount: int
    pickedPhotoIds: List[str] = Field(default_factory=list)


class PickResult(BaseModel):
    schemaVersion: int = 1
    generatedAt: str
    origin: str
    sourceDirectory: str
    recursive: bool
    totalPhotos: int
    roundsPlayed: int
    pickedPhotoCount: int
    lastRound: LastRound
    photos: List[PickResultPhoto] = Field(default_factory=list)


class FinishResponse(BaseModel):
    result: PickResult
    savedPath: Optional[str] = None


class ExportRequest(BaseModel):
    path: str


class ExportResponse(BaseModel):
    path: str
