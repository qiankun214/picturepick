"""会话 / 轮次 / pick 计数状态机（docs/API.md §4）。

* 第 1 轮 = 全部照片；之后每一轮 = 上一轮被 pick 的照片（保持上一轮顺序）。
* ``pickCount`` 不单独存储：它等于「历史各轮中被 pick 的次数」，
  这样取消 pick、进入下一轮都天然保持正确（历史轮次保留自己的 picked 集合）。
* ``phase`` 只有 ``picking`` / ``finished``；finished 后拒绝任何修改。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .errors import (
    NOT_ENOUGH_PICKS,
    PHASE_FINISHED as PHASE_FINISHED_CODE,
    PHOTO_NOT_FOUND,
    ROUND_NOT_FOUND,
    SESSION_NOT_FOUND,
    ApiError,
)
from .export import build_result, isoformat, now_local
from .models import (
    FinishResponse,
    PhotoItem,
    PickResult,
    RoundSummary,
    SessionSummary,
)
from .scanner import ScannedPhoto

PHASE_PICKING = "picking"
PHASE_FINISHED = "finished"

#: 会话在内存中保留的时长（``/api/sessions/current`` 只返回未过期会话）。
SESSION_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class Photo:
    id: str
    name: str
    relative_path: str
    absolute_path: str
    size_bytes: int


class Round:
    """一轮选片：照片顺序、本轮 viewed 集合、本轮 picked 集合。"""

    def __init__(self, index: int, photo_ids: List[str]) -> None:
        self.index = index
        self.photo_ids: List[str] = list(photo_ids)
        self._id_set = set(self.photo_ids)
        self.picked: set[str] = set()
        self.viewed: set[str] = set()

    def __contains__(self, photo_id: object) -> bool:
        return photo_id in self._id_set

    def __len__(self) -> int:
        return len(self.photo_ids)


class Session:
    """一个选片会话。"""

    def __init__(
        self,
        session_id: str,
        root: str,
        recursive: bool,
        photos: List[ScannedPhoto],
        created_at: Optional[datetime] = None,
    ) -> None:
        self.id = session_id
        self.root = root
        self.recursive = bool(recursive)
        self.created_at = created_at or now_local()
        self.photos: Dict[str, Photo] = {}
        ordered_ids: List[str] = []
        for scanned in photos:
            if scanned.id in self.photos:  # 理论上不会重复，保险起见
                continue
            self.photos[scanned.id] = Photo(
                id=scanned.id,
                name=scanned.name,
                relative_path=scanned.relative_path,
                absolute_path=scanned.absolute_path,
                size_bytes=scanned.size_bytes,
            )
            ordered_ids.append(scanned.id)
        self.rounds: List[Round] = [Round(1, ordered_ids)]
        self.phase = PHASE_PICKING
        self._finished_result: Optional[PickResult] = None

    # ---- 基本属性 ----------------------------------------------------------

    @property
    def current_round(self) -> Round:
        return self.rounds[-1]

    @property
    def round_index(self) -> int:
        return len(self.rounds)

    @property
    def library_total(self) -> int:
        return len(self.photos)

    # ---- 查询 --------------------------------------------------------------

    def get_round(self, index: int) -> Round:
        if index < 1 or index > len(self.rounds):
            raise ApiError(ROUND_NOT_FOUND, f"轮次 {index} 不存在")
        return self.rounds[index - 1]

    def get_photo(self, photo_id: str) -> Photo:
        photo = self.photos.get(photo_id)
        if photo is None:
            raise ApiError(PHOTO_NOT_FOUND, "照片不存在或不属于该会话")
        return photo

    def pick_count(self, photo_id: str) -> int:
        """历史累计 pick 次数（各轮 picked 集合求和）。"""
        return sum(1 for round_ in self.rounds if photo_id in round_.picked)

    def picked_in_rounds(self, photo_id: str) -> List[int]:
        return [round_.index for round_ in self.rounds if photo_id in round_.picked]

    def photo_item(self, photo_id: str) -> PhotoItem:
        photo = self.get_photo(photo_id)
        current = self.current_round
        return PhotoItem(
            id=photo.id,
            name=photo.name,
            relativePath=photo.relative_path,
            sizeBytes=photo.size_bytes,
            pickCount=self.pick_count(photo_id),
            picked=photo_id in current.picked,
            viewed=photo_id in current.viewed,
        )

    def summary(self) -> SessionSummary:
        current = self.current_round
        return SessionSummary(
            id=self.id,
            root=self.root,
            recursive=self.recursive,
            createdAt=isoformat(self.created_at),
            phase=self.phase,
            roundIndex=self.round_index,
            roundCount=len(self.rounds),
            libraryTotal=len(self.photos),
            roundPhotoCount=len(current.photo_ids),
            viewedCount=len(current.viewed),
            pickCount=len(current.picked),
            rounds=[
                RoundSummary(
                    index=round_.index,
                    photoCount=len(round_.photo_ids),
                    viewedCount=len(round_.viewed),
                    pickedCount=len(round_.picked),
                )
                for round_ in self.rounds
            ],
        )

    # ---- 状态变更 ----------------------------------------------------------

    def _require_picking(self) -> None:
        if self.phase == PHASE_FINISHED:
            raise ApiError(PHASE_FINISHED_CODE, "会话已结束，不能再修改选片状态")

    def _require_current_photo(self, photo_id: str) -> Photo:
        photo = self.get_photo(photo_id)
        if photo_id not in self.current_round:
            raise ApiError(PHOTO_NOT_FOUND, "照片不属于当前轮次")
        return photo

    def mark_viewed(self, photo_id: str) -> None:
        """标记当前轮已浏览（幂等）。"""
        self._require_picking()
        self._require_current_photo(photo_id)
        self.current_round.viewed.add(photo_id)

    def pick(self, photo_id: str) -> None:
        """标记当前轮 pick（幂等，不重复计数）。"""
        self._require_picking()
        self._require_current_photo(photo_id)
        self.current_round.picked.add(photo_id)

    def unpick(self, photo_id: str) -> None:
        """取消当前轮 pick（未 pick 时幂等）。"""
        self._require_picking()
        self._require_current_photo(photo_id)
        self.current_round.picked.discard(photo_id)

    def next_round(self) -> SessionSummary:
        """结束当前轮，用本轮 pick 的照片创建下一轮。"""
        self._require_picking()
        current = self.current_round
        if len(current.picked) < 2:
            raise ApiError(NOT_ENOUGH_PICKS, "当前轮 pick 少于 2 张照片，无法进入下一轮")
        next_ids = [photo_id for photo_id in current.photo_ids if photo_id in current.picked]
        self.rounds.append(Round(len(self.rounds) + 1, next_ids))
        return self.summary()

    def finish(self) -> FinishResponse:
        """结束会话并生成最终结果；重复调用返回相同结果。"""
        if self._finished_result is None:
            self.phase = PHASE_FINISHED
            self._finished_result = build_result(self)
        return FinishResponse(result=self._finished_result, savedPath=None)

    def result(self) -> PickResult:
        """当前状态的结果；finish 后返回冻结的最终结果。"""
        if self._finished_result is not None:
            return self._finished_result
        return build_result(self)


class SessionStore:
    """进程内会话仓库：维护 current session 与过期清理。"""

    def __init__(self, ttl: timedelta = SESSION_TTL) -> None:
        self._ttl = ttl
        self._sessions: Dict[str, Session] = {}
        self._current_id: Optional[str] = None
        self._lock = threading.RLock()

    def create(self, root: str, recursive: bool, photos: List[ScannedPhoto]) -> Session:
        with self._lock:
            self._purge_locked()
            session = Session(uuid.uuid4().hex, root, recursive, photos)
            self._sessions[session.id] = session
            self._current_id = session.id
            return session

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._is_expired(session):
                self._remove_locked(session_id)
                return None
            return session

    def require(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session is None:
            raise ApiError(SESSION_NOT_FOUND, "会话不存在或已过期")
        return session

    def current(self) -> Optional[Session]:
        with self._lock:
            if self._current_id is None:
                return None
            session = self._sessions.get(self._current_id)
            if session is None:
                self._current_id = None
                return None
            if self._is_expired(session):
                self._remove_locked(session.id)
                return None
            return session

    def ttl(self) -> timedelta:  # pragma: no cover - 调试辅助
        return self._ttl

    def _is_expired(self, session: Session) -> bool:
        return now_local() - session.created_at > self._ttl

    def _remove_locked(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        if self._current_id == session_id:
            self._current_id = None

    def _purge_locked(self) -> None:
        expired = [sid for sid, session in self._sessions.items() if self._is_expired(session)]
        for sid in expired:
            self._remove_locked(sid)
