"""AI 识鸟后台任务仓库与单线程 worker（docs/BIRDID-CONTRACT.md §5）。

任务状态：``queued → running → completed/failed → adopted/dismissed``。
worker 串行处理（一次只识别一张），因此「队列位置」与「进度」语义清晰。
所有公开方法线程安全；API 层只读快照（``BirdTask`` 模型）。
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..errors import (
    BIRD_API_ERROR,
    BIRD_REFERENCE_UNAVAILABLE,
    BIRD_TASK_ADOPTED,
    BIRD_TASK_NOT_COMPLETED,
    BIRD_TASK_NOT_FOUND,
    ApiError,
)
from ..export import isoformat, now_local
from ..models import (
    BirdQueueInfo,
    BirdResult,
    BirdTask,
    BirdTaskError,
    BirdTaskListResponse,
)
from .reference import ReferenceImage

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ADOPTED = "adopted"
STATUS_DISMISSED = "dismissed"

STAGE_QUEUED = "queued"
STAGE_PREPARING = "preparing"
STAGE_IDENTIFYING = "identifying"
STAGE_REFERENCE = "reference"
STAGE_DONE = "done"

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
FINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_ADOPTED, STATUS_DISMISSED)

#: 单任务估算耗时（秒），用于 running 阶段的百分比插值
ESTIMATED_SECONDS = 15.0


@dataclass
class TaskRecord:
    """内部可变任务记录（不直接暴露给 API）。"""

    id: str
    session_id: str
    photo_id: str
    photo_name: str
    photo_relative_path: str
    model: str
    created_at: datetime
    status: str = STATUS_QUEUED
    stage: str = STAGE_QUEUED
    progress: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[BirdResult] = None
    error: Optional[BirdTaskError] = None
    adopted: bool = False
    reference: Optional[ReferenceImage] = None
    #: 参考图来源 URL（仅调试/文档用，不暴露明文外链也可）
    reference_source_url: str = ""
    #: 用户忽略后置 True，worker 完成时丢弃结果
    cancelled: bool = False


@dataclass
class TaskSnapshot:
    """任务 + 队列位置（API 序列化用）。"""

    record: TaskRecord
    queue_position: Optional[int] = None


class BirdTaskManager:
    """任务队列 + worker。``handler`` 负责真正调用模型并更新 stage。"""

    def __init__(self, handler: Callable[[TaskRecord], None]) -> None:
        self._handler = handler
        self._lock = threading.RLock()
        self._records: Dict[str, TaskRecord] = {}
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._idle = threading.Event()
        self._idle.set()
        self._closed = False

    # ---- 提交 / 查询 -------------------------------------------------------

    def submit(
        self,
        session_id: str,
        photo,
        model: str,
    ) -> Tuple[TaskRecord, bool]:
        """创建任务；同照片已有 active 任务时复用（返回 reused=True）。"""
        with self._lock:
            for record in self._records.values():
                if (
                    record.session_id == session_id
                    and record.photo_id == photo.id
                    and record.status in ACTIVE_STATUSES
                ):
                    return record, True
            record = TaskRecord(
                id=uuid.uuid4().hex,
                session_id=session_id,
                photo_id=photo.id,
                photo_name=photo.name,
                photo_relative_path=photo.relative_path,
                model=model,
                created_at=now_local(),
            )
            self._records[record.id] = record
            self._queue.put(record.id)
            self._idle.clear()
            self._ensure_worker_locked()
            return record, False

    def get(self, task_id: str, session_id: Optional[str] = None) -> TaskRecord:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or (session_id is not None and record.session_id != session_id):
                raise ApiError(BIRD_TASK_NOT_FOUND, "识别任务不存在")
            return record

    def list_for_session(self, session_id: str) -> BirdTaskListResponse:
        with self._lock:
            records = [r for r in self._records.values() if r.session_id == session_id]
            snapshots = [TaskSnapshot(r, self._queue_position_locked(r)) for r in records]
            snapshots.sort(key=self._sort_key)
            tasks = [self._to_model(snapshot) for snapshot in snapshots]
            return BirdTaskListResponse(
                tasks=tasks,
                queue=BirdQueueInfo(
                    queued=sum(1 for r in records if r.status == STATUS_QUEUED),
                    running=sum(1 for r in records if r.status == STATUS_RUNNING),
                ),
            )

    def counters(self) -> BirdQueueInfo:
        with self._lock:
            return BirdQueueInfo(
                queued=sum(1 for r in self._records.values() if r.status == STATUS_QUEUED),
                running=sum(1 for r in self._records.values() if r.status == STATUS_RUNNING),
            )

    def reference_bytes(self, task_id: str, session_id: Optional[str] = None) -> ReferenceImage:
        record = self.get(task_id, session_id=session_id)
        with self._lock:
            reference = record.reference
        if reference is None:
            raise ApiError(
                BIRD_REFERENCE_UNAVAILABLE,
                "该识别任务没有可用的参考照片",
            )
        return reference

    def model(self, task_id: str, session_id: Optional[str] = None) -> BirdTask:
        """返回任务的 API 序列化快照（用于 POST/采纳/忽略的响应）。"""
        with self._lock:
            record = self._records.get(task_id)
            if record is None or (session_id is not None and record.session_id != session_id):
                raise ApiError(BIRD_TASK_NOT_FOUND, "识别任务不存在")
            snapshot = TaskSnapshot(record, self._queue_position_locked(record))
            return self._to_model(snapshot)

    # ---- worker 侧更新（handler 调用） -------------------------------------

    def set_stage(self, task_id: str, stage: str, progress: int) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status != STATUS_RUNNING:
                return
            record.stage = stage
            record.progress = max(record.progress, min(100, int(progress)))

    def finish_success(
        self,
        task_id: str,
        result: BirdResult,
        reference: Optional[ReferenceImage],
        reference_source_url: str = "",
    ) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            if record.cancelled:
                return
            record.result = result
            record.reference = reference
            record.reference_source_url = reference_source_url
            record.status = STATUS_COMPLETED
            record.stage = STAGE_DONE
            record.progress = 100
            record.error = None
            record.finished_at = now_local()
        self._mark_idle_if_drained()

    def finish_failure(self, task_id: str, code: str, message: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            if record.cancelled:
                return
            record.status = STATUS_FAILED
            record.error = BirdTaskError(code=code, message=message)
            record.finished_at = now_local()
        self._mark_idle_if_drained()

    def mark_adopted(self, task_id: str) -> TaskRecord:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise ApiError(BIRD_TASK_NOT_FOUND, "识别任务不存在")
            if record.status not in (STATUS_COMPLETED, STATUS_ADOPTED):
                raise ApiError(BIRD_TASK_NOT_COMPLETED, "识别任务尚未完成，不能采纳")
            record.status = STATUS_ADOPTED
            record.adopted = True
            return record

    def mark_dismissed(self, task_id: str, session_id: Optional[str] = None) -> TaskRecord:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or (session_id is not None and record.session_id != session_id):
                raise ApiError(BIRD_TASK_NOT_FOUND, "识别任务不存在")
            if record.status == STATUS_ADOPTED:
                raise ApiError(BIRD_TASK_ADOPTED, "该识别结果已被采纳，不能忽略")
            # 排队中/识别中/已完成/失败都可以忽略；识别中的任务结果会丢弃。
            record.cancelled = True
            record.status = STATUS_DISMISSED
            record.progress = record.progress if record.progress else 0
        self._mark_idle_if_drained()
        return record

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """等待队列清空（测试/CLI 用）。"""
        return self._idle.wait(timeout=timeout)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._queue.put(None)

    # ---- 内部 --------------------------------------------------------------

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._work_loop, name="picturepick-bird-worker", daemon=True
        )
        self._worker.start()

    def _work_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if task_id is None:
                self._queue.task_done()
                return
            try:
                self._process(task_id)
            finally:
                self._queue.task_done()
                self._mark_idle_if_drained()

    def _process(self, task_id: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status != STATUS_QUEUED:
                return
            record.status = STATUS_RUNNING
            record.stage = STAGE_PREPARING
            record.progress = 5
            record.started_at = now_local()
        try:
            self._handler(record)
        except Exception as exc:  # noqa: BLE001 - 任何异常都转为任务失败
            code = getattr(exc, "code", None) or BIRD_API_ERROR
            message = getattr(exc, "message", None) or str(exc) or "识别失败"
            self.finish_failure(task_id, str(code), str(message))

    def _mark_idle_if_drained(self) -> None:
        with self._lock:
            if self._queue.empty() and not any(
                r.status == STATUS_RUNNING for r in self._records.values()
            ):
                self._idle.set()

    def _queue_position_locked(self, record: TaskRecord) -> Optional[int]:
        if record.status != STATUS_QUEUED:
            return None
        position = 0
        for other in self._records.values():
            if other.status != STATUS_QUEUED:
                continue
            position += 1
            if other.id == record.id:
                return position
        return None

    @staticmethod
    def _sort_key(snapshot: TaskSnapshot):
        record = snapshot.record
        if record.status == STATUS_RUNNING:
            return (0, record.started_at or record.created_at)
        if record.status == STATUS_QUEUED:
            return (1, record.created_at)
        return (2, _negated(record.created_at))

    def _to_model(self, snapshot: TaskSnapshot) -> BirdTask:
        record = snapshot.record
        progress = self._progress_of(record)
        elapsed_ms = self._elapsed_ms(record)
        reference_url = None
        if record.reference is not None:
            reference_url = (
                f"/api/sessions/{record.session_id}/bird-tasks/{record.id}/reference"
            )
        return BirdTask(
            id=record.id,
            sessionId=record.session_id,
            photoId=record.photo_id,
            photoName=record.photo_name,
            photoRelativePath=record.photo_relative_path,
            thumbnailUrl=(
                f"/api/sessions/{record.session_id}/photos/{record.photo_id}/image?variant=thumb"
            ),
            status=record.status,
            stage=record.stage,
            progress=progress,
            queuePosition=snapshot.queue_position,
            createdAt=isoformat(record.created_at),
            startedAt=isoformat(record.started_at) if record.started_at else None,
            finishedAt=isoformat(record.finished_at) if record.finished_at else None,
            elapsedMs=elapsed_ms,
            model=record.model,
            result=record.result,
            error=record.error,
            adopted=record.adopted,
            referenceAvailable=record.reference is not None,
            referenceUrl=reference_url,
        )

    @staticmethod
    def _progress_of(record: TaskRecord) -> int:
        if record.status in (STATUS_COMPLETED, STATUS_ADOPTED):
            return 100
        if record.status != STATUS_RUNNING:
            return int(record.progress)
        started = record.started_at or record.created_at
        elapsed = max(0.0, (now_local() - started).total_seconds())
        if record.stage == STAGE_PREPARING:
            return max(record.progress, 10)
        if record.stage == STAGE_IDENTIFYING:
            interpolated = 20 + int(min(65.0, elapsed / ESTIMATED_SECONDS * 65.0))
            return max(record.progress, min(85, interpolated))
        if record.stage == STAGE_REFERENCE:
            return max(record.progress, 90)
        if record.stage == STAGE_DONE:
            return 100
        return int(record.progress)

    @staticmethod
    def _elapsed_ms(record: TaskRecord) -> int:
        if record.started_at is None:
            return 0
        end = record.finished_at or now_local()
        return max(0, int((end - record.started_at).total_seconds() * 1000))


class _NegatedDatetime:
    """用于把 datetime 倒序排序（避免依赖时间戳数字）。"""

    __slots__ = ("value",)

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __lt__(self, other: "_NegatedDatetime") -> bool:
        return self.value > other.value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NegatedDatetime) and self.value == other.value


def _negated(value: datetime) -> _NegatedDatetime:
    return _NegatedDatetime(value)
