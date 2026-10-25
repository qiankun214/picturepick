"""Pydantic 数据模型：字段与 docs/API.md §2/§3 的 JSON 契约一一对应。

注意：运行环境安装的是 pydantic 1.10（FastAPI 0.115 同时兼容 v1/v2），
因此这里只使用 v1/v2 共有的写法（``BaseModel``、``Field``、``.dict()``）。
字段名直接使用契约中的 camelCase，避免别名转换带来的歧义。
"""

from __future__ import annotations

from typing import List, Optional

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
    directory: str
    recursive: bool = True


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


class PickResult(BaseModel):
    schemaVersion: int = 1
    generatedAt: str
    sourceDirectory: str
    recursive: bool
    totalPhotos: int
    roundsPlayed: int
    pickedPhotoCount: int
    photos: List[PickResultPhoto] = Field(default_factory=list)


class FinishResponse(BaseModel):
    result: PickResult
    savedPath: Optional[str] = None


class ExportRequest(BaseModel):
    path: str


class ExportResponse(BaseModel):
    path: str
