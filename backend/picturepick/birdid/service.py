"""AI 识鸟服务编排（docs/BIRDID-CONTRACT.md §5、§6）。

职责：

* 读取当前配置、构造客户端（真实 / 模拟）；
* 校验会话/照片，把任务放入 :class:`BirdTaskManager`；
* worker 侧：读图 → 调模型 → 取参考图 → 完成任务；
* 采纳：写照片 EXIF + 记录到会话（结果 JSON 立即生效）；
* 忽略、查询、参考图字节读取。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional, Tuple

from .. import scanner
from ..errors import (
    BIRD_NOT_CONFIGURED,
    BIRD_RESULT_UNAVAILABLE,
    BIRD_TASK_NOT_COMPLETED,
    INVALID_CONFIG,
    INVALID_SOURCE,
    PHASE_FINISHED,
    PHOTO_NOT_FOUND,
    SESSION_NOT_FOUND,
    ApiError,
)
from ..export import isoformat, now_local
from ..models import (
    BirdAdoptResponse,
    BirdExifOutcome,
    BirdObservation,
    BirdQueueInfo,
    BirdResult,
    BirdStatusResponse,
    BirdTask,
    BirdVerifyResponse,
)
from ..session import ORIGIN_SERVER, SessionStore
from . import exif
from .client import BirdClientError, build_client
from .config import BirdConfig, resolve_config, update_config
from .reference import (
    ReferenceImage,
    is_probably_webpage_url,
    resolve_reference,
)
from .tasks import BirdTaskManager, TaskRecord

#: 允许测试注入的客户端工厂 / 参考图解析器类型
ClientFactory = Callable[[BirdConfig], Any]
ReferenceResolver = Callable[[Any], Optional[ReferenceImage]]


class BirdService:
    def __init__(
        self,
        session_store: SessionStore,
        config_resolver: Callable[[], BirdConfig] = resolve_config,
        client_factory: ClientFactory = build_client,
        reference_resolver: ReferenceResolver = resolve_reference,
    ) -> None:
        self._store = session_store
        self._config_resolver = config_resolver
        self._client_factory = client_factory
        self._reference_resolver = reference_resolver
        self.manager = BirdTaskManager(self._process_task)
        #: 参考图按「科/属/种」缓存，同一鸟种多次识别不再重复联网（最多 16 条，仅内存）
        self._reference_cache: Dict[Tuple[str, str], ReferenceImage] = {}

    # ---- 配置 --------------------------------------------------------------

    def current_config(self) -> BirdConfig:
        return self._config_resolver()

    def status(self) -> BirdStatusResponse:
        config = self.current_config()
        counters = self._counters()
        return BirdStatusResponse(
            configured=config.configured,
            model=config.model,
            endpoint=config.endpoint,
            endpointProtocol=config.protocol,
            endpointWarning=config.endpoint_warning,
            keyMasked=config.masked_key(),
            keySource=config.source or None,
            mock=config.mock,
            queue=counters,
        )

    def update_config(self, payload: Dict[str, Any]) -> BirdStatusResponse:
        if not payload:
            return self.status()
        update_config(payload)
        return self.status()

    def verify(self) -> BirdVerifyResponse:
        config = self.current_config()
        if not config.configured:
            return BirdVerifyResponse(
                ok=False,
                message="尚未配置 ARK API Key（或开启模拟模式）",
                model=config.model,
            )
        if not config.endpoint_supported:
            return BirdVerifyResponse(
                ok=False,
                message=config.endpoint_warning or "不支持的 API 端点",
                model=config.model,
            )
        try:
            client = self._client_factory(config)
            ok, message, latency = client.verify()
        except Exception as exc:  # noqa: BLE001 - 验证接口永远返回结构化结果
            return BirdVerifyResponse(ok=False, message=f"验证失败：{exc}", model=config.model)
        return BirdVerifyResponse(
            ok=bool(ok), message=str(message), latencyMs=latency, model=config.model
        )

    # ---- 任务 --------------------------------------------------------------

    def submit(self, session_id: str, photo_id: str) -> BirdTask:
        session = self._require_session(session_id)
        self._require_server_origin(session)
        if session.phase == "finished":
            raise ApiError(PHASE_FINISHED, "会话已结束，不能再提交 AI 识鸟任务")
        photo = session.get_photo(photo_id)
        if not photo.absolute_path or not os.path.isfile(photo.absolute_path):
            raise ApiError(PHOTO_NOT_FOUND, "照片文件不存在或不可读")
        config = self.current_config()
        if not config.configured:
            raise ApiError(
                BIRD_NOT_CONFIGURED,
                "尚未配置 ARK API Key：请点击「AI 识鸟设置」填写，或设置环境变量 ARK_API_KEY",
            )
        if not config.endpoint_supported:
            raise ApiError(
                INVALID_CONFIG,
                config.endpoint_warning
                or f"不支持的 API 端点：{config.endpoint}",
            )
        record, _reused = self.manager.submit(session_id, photo, config.model)
        return self.manager.model(record.id)

    def list_tasks(self, session_id: str):
        self._require_session(session_id)
        return self.manager.list_for_session(session_id)

    def adopt(self, session_id: str, task_id: str) -> BirdAdoptResponse:
        session = self._require_session(session_id)
        self._require_server_origin(session)
        record = self.manager.get(task_id, session_id=session_id)
        if record.status not in ("completed", "adopted"):
            raise ApiError(BIRD_TASK_NOT_COMPLETED, "识别任务尚未完成，不能采纳")
        result = record.result
        if result is None or not result.identified:
            raise ApiError(BIRD_RESULT_UNAVAILABLE, "该任务没有可采纳的识别结果（画面中没有鸟）")
        photo = session.get_photo(record.photo_id)

        observation = BirdObservation(
            family=result.family,
            familyLatin=result.familyLatin,
            genus=result.genus,
            genusLatin=result.genusLatin,
            species=result.species,
            speciesLatin=result.speciesLatin,
            commonNameEn=result.commonNameEn,
            confidence=result.confidence,
            note=result.note,
            referenceSource=result.referenceSource,
            model=record.model,
            adoptedAt=isoformat(now_local()),
            exifWritten=False,
        )

        exif_outcome = self._write_exif(session, photo, observation)
        observation.exifWritten = exif_outcome.written
        observation.exifError = None if exif_outcome.written else exif_outcome.error

        session.adopt_bird_observation(record.photo_id, observation)
        self.manager.mark_adopted(task_id)
        return BirdAdoptResponse(
            task=self.manager.model(task_id, session_id=session_id),
            observation=observation,
            exif=exif_outcome,
        )

    def dismiss(self, session_id: str, task_id: str) -> BirdTask:
        self._require_session(session_id)
        self.manager.mark_dismissed(task_id, session_id=session_id)
        return self.manager.model(task_id, session_id=session_id)

    def reference(self, session_id: str, task_id: str) -> ReferenceImage:
        self._require_session(session_id)
        return self.manager.reference_bytes(task_id, session_id=session_id)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        return self.manager.wait_idle(timeout)

    def shutdown(self) -> None:
        self.manager.shutdown()

    # ---- worker ------------------------------------------------------------

    def _process_task(self, record: TaskRecord) -> None:
        session = self._store.get(record.session_id)
        if session is None:
            raise ApiError(SESSION_NOT_FOUND, "会话已过期，识别任务无法继续")
        photo = session.get_photo(record.photo_id)
        path = photo.absolute_path
        if not path or not os.path.isfile(path):
            raise ApiError(PHOTO_NOT_FOUND, "照片文件不存在或不可读")
        self.manager.set_stage(record.id, "preparing", 10)
        try:
            with open(path, "rb") as handle:
                image_bytes = handle.read()
        except OSError as exc:
            raise ApiError(PHOTO_NOT_FOUND, f"读取照片失败：{exc}") from exc

        config = self.current_config()
        client = self._client_factory(config)
        self.manager.set_stage(record.id, "identifying", 20)
        seed = record.photo_id
        parsed = client.identify(image_bytes, seed=seed, photo_name=record.photo_name)

        reference: Optional[ReferenceImage] = None
        if parsed.result.identified:
            search_error = getattr(client, "web_search_error", None)
            original_note = parsed.result.referenceNote
            self.manager.set_stage(record.id, "reference", 90)
            # 联网搜索未开通时，模型可能仍凭记忆给出图片直链：用更短超时尝试一次；
            # 下载失败再显示「需要开通联网搜索」的提示，避免长时间等待。
            initial_url = parsed.reference_image_url or ""
            if initial_url and is_probably_webpage_url(initial_url):
                reference = None  # 模型给的是网页，不是图片文件，直接进入二次请求
            else:
                reference = self._resolve_reference(
                    client, parsed, timeout=3.0 if search_error else None
                )
            if reference is None and not search_error:
                # 首轮给的可能是网页/维基/不可下载链接：把已拒绝的 URL 带上，再让模型联网
                # 重新要「图片文件本身的直链」（最多再请求 2 次，要求不能重复不合格链接）。
                if initial_url and not parsed.result.referencePageUrl:
                    parsed.result.referencePageUrl = initial_url
                rejected: list[str] = []
                if initial_url:
                    rejected.append(initial_url)
                requester = getattr(client, "request_reference_url", None)
                if callable(requester):
                    for _attempt in range(2):
                        try:
                            found = requester(parsed.result, rejected=rejected)
                        except TypeError:  # 测试注入的简易 requester 不接受 rejected
                            found = requester(parsed.result)
                        if not found or not found.get("referenceImageUrl"):
                            break
                        url = str(found["referenceImageUrl"]).strip()
                        if not url:
                            break
                        # 保留 AI 提供的参考来源链接：无图片可显示时前端让用户点击查看
                        page = str(found.get("referencePageUrl") or "").strip()
                        if not page:
                            note = str(found.get("referenceNote") or "").strip()
                            if note.lower().startswith(("http://", "https://")):
                                page = note
                        if page:
                            parsed.result.referencePageUrl = page
                        elif not parsed.result.referencePageUrl:
                            parsed.result.referencePageUrl = url
                        if is_probably_webpage_url(url):
                            rejected.append(url)
                            continue
                        parsed.reference_image_url = url
                        if found.get("referenceSource"):
                            parsed.result.referenceSource = str(found["referenceSource"])
                        if found.get("referenceNote"):
                            parsed.result.referenceNote = str(found["referenceNote"])
                        reference = self._resolve_reference(client, parsed, timeout=8.0)
                        if reference is not None:
                            break
                        rejected.append(url)
            if reference is not None:
                if not parsed.result.referenceNote:
                    parsed.result.referenceNote = original_note
            elif search_error:
                parsed.result.referenceNote = (
                    "参考照片需要火山方舟「联网搜索」插件（当前未开通或不可用）。"
                    "请在方舟控制台开通后重试："
                    "https://console.volcengine.com/common-buy/CC_content_plugin"
                )
            else:
                parsed.result.referenceNote = (
                    "AI 未能提供可访问的参考图片文件直链（可能只检索到网页）。"
                    "可稍后重试或换一张更清晰的照片。"
                )
        self.manager.finish_success(
            record.id,
            parsed.result,
            reference,
            reference_source_url=reference.source_url if reference is not None else "",
        )

    def _resolve_reference(
        self, client: Any, parsed: Any, timeout: Optional[float] = None
    ) -> Optional[ReferenceImage]:
        """模拟客户端自带参考图生成；真实客户端走 reference 模块（仅 AI 提供的 URL）。"""
        try:
            if hasattr(client, "reference") and callable(getattr(client, "reference")):
                return client.reference(parsed)
            result = parsed.result
            key = (result.species or "", result.speciesLatin or "")
            cached = self._reference_cache.get(key)
            if cached is not None:
                return cached
            try:
                if timeout is not None:
                    reference = self._reference_resolver(parsed, timeout=timeout)
                else:
                    reference = self._reference_resolver(parsed)
            except TypeError:
                # 测试注入的简易 resolver 只接受一个参数
                reference = self._reference_resolver(parsed)
            if reference is not None:
                if len(self._reference_cache) >= 16:
                    self._reference_cache.pop(next(iter(self._reference_cache)))
                self._reference_cache[key] = reference
            return reference
        except Exception:  # noqa: BLE001 - 参考图失败不影响识别任务
            return None

    def _write_exif(self, session, photo, observation: BirdObservation) -> BirdExifOutcome:
        path = photo.absolute_path
        if not path:
            return BirdExifOutcome(written=False, path="", error="照片没有绝对路径")
        if not scanner.is_within(session.root, path):
            return BirdExifOutcome(
                written=False, path=path, error="照片不在会话目录内，已跳过 EXIF 写入"
            )
        try:
            outcome = exif.write_bird_exif(path, observation)
        except Exception as exc:  # noqa: BLE001 - EXIF 写入失败不阻断采纳
            return BirdExifOutcome(written=False, path=path, error=f"写入 EXIF 失败：{exc}")
        return BirdExifOutcome(
            written=outcome.written, path=outcome.path, error=outcome.error
        )

    # ---- 内部 --------------------------------------------------------------

    def _counters(self) -> BirdQueueInfo:
        return self.manager.counters()

    def _require_session(self, session_id: str):
        session = self._store.get(session_id)
        if session is None:
            raise ApiError(SESSION_NOT_FOUND, "会话不存在或已过期")
        return session

    @staticmethod
    def _require_server_origin(session) -> None:
        if session.origin != ORIGIN_SERVER:
            raise ApiError(INVALID_SOURCE, "仅 server 来源会话支持 AI 识鸟")
