"""Pydantic 数据模型：字段与 docs/API.md（第 3 版）§2/§3/§4 的 JSON 契约一一对应。

第 2 版加入 ``origin`` / ``sourceLabel`` / ``lastRound``；
第 3 版新增 ``SessionSummary.restored``（是否从结果 JSON 恢复）与 export-photos 模型。

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


# ---- AI 识鸟（第 5 版） ----------------------------------------------------


class BirdAlternative(BaseModel):
    """备选识别结果（最多 3 条）。"""

    species: str = ""
    speciesLatin: str = ""
    confidence: Optional[float] = None


class BirdResult(BaseModel):
    """模型识别结果（任务完成后的原始结构化结果）。"""

    identified: bool = False
    family: str = ""
    familyLatin: str = ""
    genus: str = ""
    genusLatin: str = ""
    species: str = ""
    speciesLatin: str = ""
    commonNameEn: str = ""
    confidence: Optional[float] = None
    note: str = ""
    #: identified=false 时的原因
    reason: str = ""
    alternatives: List[BirdAlternative] = Field(default_factory=list)
    referenceSource: str = ""
    referenceNote: str = ""


class BirdObservation(BaseModel):
    """已采纳的鸟类识别（写入会话 / PhotoItem / 结果 JSON）。"""

    family: str = ""
    familyLatin: str = ""
    genus: str = ""
    genusLatin: str = ""
    species: str = ""
    speciesLatin: str = ""
    commonNameEn: str = ""
    confidence: Optional[float] = None
    note: str = ""
    referenceSource: str = ""
    model: str = ""
    adoptedAt: str = ""
    exifWritten: bool = False
    exifError: Optional[str] = None


class BirdTaskError(BaseModel):
    code: str
    message: str


class BirdTask(BaseModel):
    """AI 识鸟后台任务（队列 / 进度 / 结果）。"""

    id: str
    sessionId: str
    photoId: str
    photoName: str
    photoRelativePath: str
    thumbnailUrl: str
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    queuePosition: Optional[int] = None
    createdAt: str
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None
    elapsedMs: int = 0
    model: str = ""
    result: Optional[BirdResult] = None
    error: Optional[BirdTaskError] = None
    adopted: bool = False
    referenceAvailable: bool = False
    referenceUrl: Optional[str] = None


class BirdQueueInfo(BaseModel):
    queued: int = 0
    running: int = 0


class BirdTaskCreateRequest(BaseModel):
    photoId: str


class BirdTaskListResponse(BaseModel):
    tasks: List[BirdTask] = Field(default_factory=list)
    queue: BirdQueueInfo = Field(default_factory=BirdQueueInfo)


class BirdStatusResponse(BaseModel):
    configured: bool = False
    model: str = ""
    #: 完整 API 端点 URL（后端原样 POST，不做路径拼接）
    endpoint: str = ""
    #: 端点协议：chat（Chat Completions）/ responses（Responses API）/ null（不支持）
    endpointProtocol: Optional[str] = None
    #: 端点不是受支持的完整接口 URL 时的提示（提交任务会返回 400 INVALID_CONFIG）
    endpointWarning: Optional[str] = None
    keyMasked: Optional[str] = None
    keySource: Optional[str] = None
    mock: bool = False
    queue: BirdQueueInfo = Field(default_factory=BirdQueueInfo)


class BirdConfigRequest(BaseModel):
    """PUT /api/bird/config：字段缺省 = 保持；null / 空字符串 = 清除。

    端点为**完整接口 URL**（以 ``/chat/completions`` 或 ``/responses`` 结尾），
    后端直接向它发请求，不做任何隐藏拼接。
    ``mock`` 使用 ``Any``，由 config.update_config 做严格的布尔校验
    （pydantic 1.x 的 bool 会把 "yes"/"on" 等字符串静默转成 True，不符合契约）。
    """

    apiKey: Optional[str] = None
    endpoint: Optional[str] = None
    model: Optional[str] = None
    mock: Any = None


class BirdVerifyResponse(BaseModel):
    ok: bool
    message: str
    latencyMs: Optional[int] = None
    model: str = ""


class BirdExifOutcome(BaseModel):
    written: bool
    path: str = ""
    error: Optional[str] = None


class BirdAdoptResponse(BaseModel):
    task: BirdTask
    observation: BirdObservation
    exif: BirdExifOutcome


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
    #: 是否通过 POST /api/sessions/restore 从结果 JSON 恢复（默认 false）
    restored: bool = False
    roundIndex: int
    roundCount: int
    libraryTotal: int
    roundPhotoCount: int
    viewedCount: int
    pickCount: int
    #: 本会话已采纳 AI 识鸟的照片数（第 5 版）
    birdIdentifiedCount: int = 0
    rounds: List[RoundSummary] = Field(default_factory=list)


class PhotoItem(BaseModel):
    id: str
    name: str
    relativePath: str
    sizeBytes: int
    pickCount: int
    picked: bool
    viewed: bool
    #: 已采纳的鸟类识别（未识别为 None，第 5 版）
    bird: Optional[BirdObservation] = None


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
    #: 已采纳的鸟类识别（未识别为 None，第 5 版）
    bird: Optional[BirdObservation] = None


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
    #: 已采纳 AI 识鸟的照片数（第 5 版）
    birdIdentifiedCount: int = 0
    lastRound: LastRound
    photos: List[PickResultPhoto] = Field(default_factory=list)


class FinishResponse(BaseModel):
    result: PickResult
    savedPath: Optional[str] = None


class ExportRequest(BaseModel):
    path: str


class ExportResponse(BaseModel):
    path: str


# ---- 导出最后一轮保留照片（第 3 版 §4 export-photos） ----------------------


class ExportPhotosRequest(BaseModel):
    destination: str


class ExportPhotosFile(BaseModel):
    """一条复制成功的照片记录。"""

    name: str
    relativePath: str
    sizeBytes: int


class ExportPhotosResponse(BaseModel):
    destination: str
    copied: int
    files: List[ExportPhotosFile] = Field(default_factory=list)
