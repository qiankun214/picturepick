"""从保存的 PickResult JSON 恢复选片现场（docs/API.md 第 3 版 §4 POST /api/sessions/restore）。

恢复策略：

* 请求体 = 之前保存的结果 JSON（顶层对象），必须 ``schemaVersion == 1`` 且 ``origin == "server"``；
* 重新扫描 ``sourceDirectory``（沿用 JSON 的 ``recursive``），按 ``relativePath``
  （大小写不敏感）与 JSON 中照片匹配：目录里新增的文件会进入第 1 轮，
  JSON 中已不存在的照片直接忽略（目录总数的口径以当前扫描为准）；
* 轮次重建：第 1 轮 = 当前扫描到的全部照片；第 i 轮（i >= 2）= 上一轮中
  ``pickedInRounds`` 包含 ``i-1`` 的照片（保持上一轮顺序）；
* ``pickCount`` / ``pickedInRounds`` 由各轮 picked 集合推导，与 JSON 记录一致；
  ``viewed`` 一律重置为 0；
* 最后一轮保留照片优先取 JSON ``lastRound.pickedPhotoIds``（映射到重新扫描到的照片），
  缺失时按 ``pickedInRounds`` 包含 ``roundsPlayed`` 推导；
* 恢复后 ``phase="finished"``、``roundIndex=roundsPlayed``、``SessionSummary.restored=true``，
  可继续下载 JSON / 导出照片，也可调用 ``POST /api/sessions/{sid}/resume`` 继续筛选。

错误码：
* ``UNSUPPORTED_SCHEMA``：schemaVersion 不是 1；
* ``INVALID_SOURCE``：origin 不是 server；
* ``INVALID_PATH``：sourceDirectory 不存在/不可读；
* ``INVALID_RESTORE``：JSON 结构非法；
* ``NO_PHOTOS``：扫描不到照片，或 JSON 里的照片在当前目录中一个都匹配不上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from . import scanner
from .errors import (
    INVALID_PATH,
    INVALID_RESTORE,
    INVALID_SOURCE,
    NO_PHOTOS,
    UNSUPPORTED_SCHEMA,
    ApiError,
)
from .session import ORIGIN_SERVER, Round, Session, SessionStore

SUPPORTED_SCHEMA_VERSION = 1

#: roundsPlayed 的合理性上限：防止非法 JSON 造成海量空轮次/内存问题
MAX_RESTORE_ROUNDS = 1000


@dataclass
class RestorePlan:
    """从结果 JSON 解析出的、用于重建现场的只读数据。"""

    source_directory: str
    recursive: bool
    rounds_played: int
    #: casefold(relativePath) -> pickedInRounds（升序）
    picked_rounds_by_path: Dict[str, List[int]] = field(default_factory=dict)
    #: JSON 照片 id -> relativePath（用于映射 lastRound.pickedPhotoIds）
    relative_path_by_id: Dict[str, str] = field(default_factory=dict)
    last_round_ids: List[str] = field(default_factory=list)
    has_last_round: bool = False


def _require_int(value: Any, field_name: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(INVALID_RESTORE, f"{field_name} 必须是整数")
    if value < minimum:
        raise ApiError(INVALID_RESTORE, f"{field_name} 不能小于 {minimum}")
    if maximum is not None and value > maximum:
        raise ApiError(INVALID_RESTORE, f"{field_name} 不能大于 {maximum}")
    return value


def parse_pick_result(payload: Any) -> RestorePlan:
    """校验结果 JSON 并提取恢复所需数据；任何结构问题抛 400 INVALID_RESTORE 等。"""
    if not isinstance(payload, dict):
        raise ApiError(INVALID_RESTORE, "恢复请求体必须是结果 JSON 对象")

    schema_version = payload.get("schemaVersion")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ApiError(
            UNSUPPORTED_SCHEMA,
            f"不支持的结果 JSON 版本: {schema_version!r}（仅支持 {SUPPORTED_SCHEMA_VERSION}）",
        )

    if payload.get("origin") != ORIGIN_SERVER:
        raise ApiError(INVALID_SOURCE, "仅支持从 server 来源的结果 JSON 恢复现场")

    source_directory = payload.get("sourceDirectory")
    if not isinstance(source_directory, str) or not source_directory.strip():
        raise ApiError(INVALID_PATH, "结果 JSON 缺少有效的 sourceDirectory")

    recursive = payload.get("recursive", True)
    if not isinstance(recursive, bool):
        recursive = True

    rounds_played = _require_int(
        payload.get("roundsPlayed"),
        "roundsPlayed",
        minimum=1,
        maximum=MAX_RESTORE_ROUNDS,
    )

    photos = payload.get("photos")
    if not isinstance(photos, list):
        raise ApiError(INVALID_RESTORE, "photos 必须是数组")

    picked_rounds_by_path: Dict[str, List[int]] = {}
    relative_path_by_id: Dict[str, str] = {}
    seen_paths: Set[str] = set()
    for index, raw in enumerate(photos):
        if not isinstance(raw, dict):
            raise ApiError(INVALID_RESTORE, f"photos[{index}] 必须是对象")
        relative = raw.get("relativePath")
        if not isinstance(relative, str) or not relative.strip():
            raise ApiError(INVALID_RESTORE, f"photos[{index}].relativePath 必须是非空字符串")
        relative_path = relative.replace("\\", "/")
        key = relative_path.casefold()
        if key in seen_paths:
            raise ApiError(INVALID_RESTORE, f"photos 中 relativePath 重复: {relative_path}")
        seen_paths.add(key)

        picked_raw = raw.get("pickedInRounds", [])
        if picked_raw is None:
            picked_raw = []
        if not isinstance(picked_raw, list):
            raise ApiError(INVALID_RESTORE, f"photos[{index}].pickedInRounds 必须是数组")
        picked_rounds: List[int] = []
        for item in picked_raw:
            picked_rounds.append(
                _require_int(
                    item,
                    f"photos[{index}].pickedInRounds[]",
                    minimum=1,
                    maximum=rounds_played,
                )
            )
        if picked_rounds != sorted(set(picked_rounds)):
            raise ApiError(
                INVALID_RESTORE,
                f"photos[{index}].pickedInRounds 必须升序且不重复",
            )
        # pickCount 由 pickedInRounds 推导，但仍校验其类型（若存在）。
        if "pickCount" in raw:
            _require_int(raw.get("pickCount"), f"photos[{index}].pickCount", minimum=0)

        picked_rounds_by_path[key] = picked_rounds
        photo_id = raw.get("id")
        if isinstance(photo_id, str) and photo_id:
            relative_path_by_id[photo_id] = relative_path

    last_round = payload.get("lastRound")
    last_round_ids: List[str] = []
    has_last_round = False
    if last_round is not None:
        if not isinstance(last_round, dict):
            raise ApiError(INVALID_RESTORE, "lastRound 必须是对象")
        raw_ids = last_round.get("pickedPhotoIds", [])
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            raise ApiError(INVALID_RESTORE, "lastRound.pickedPhotoIds 必须是数组")
        for item in raw_ids:
            if not isinstance(item, str):
                raise ApiError(INVALID_RESTORE, "lastRound.pickedPhotoIds 只能包含字符串 ID")
            last_round_ids.append(item)
        has_last_round = True

    return RestorePlan(
        source_directory=source_directory.strip(),
        recursive=recursive,
        rounds_played=rounds_played,
        picked_rounds_by_path=picked_rounds_by_path,
        relative_path_by_id=relative_path_by_id,
        last_round_ids=last_round_ids,
        has_last_round=has_last_round,
    )


def _scan_for_restore(plan: RestorePlan) -> scanner.ScanResult:
    try:
        return scanner.scan(plan.source_directory, recursive=plan.recursive)
    except scanner.InvalidDirectoryError as exc:
        raise ApiError(INVALID_PATH, str(exc)) from exc
    except scanner.NoPhotosError as exc:
        raise ApiError(NO_PHOTOS, str(exc)) from exc


def _rebuild_rounds(
    plan: RestorePlan,
    scan_result: scanner.ScanResult,
) -> List[Round]:
    """按 JSON 的 pickedInRounds 重建各轮照片顺序与 picked 集合。"""
    scanned_by_path = {photo.relative_path.casefold(): photo for photo in scan_result.photos}
    picked_rounds_by_id: Dict[str, Set[int]] = {}
    for key, picked_rounds in plan.picked_rounds_by_path.items():
        photo = scanned_by_path.get(key)
        if photo is None:  # JSON 中已不存在的照片忽略
            continue
        picked_rounds_by_id[photo.id] = set(picked_rounds)

    if not picked_rounds_by_id:
        raise ApiError(
            NO_PHOTOS,
            f"结果 JSON 中的照片在当前目录中都不存在: {scan_result.root}",
        )

    rounds: List[Round] = [Round(1, [photo.id for photo in scan_result.photos])]
    for round_number in range(2, plan.rounds_played + 1):
        previous = rounds[-1]
        next_ids = [
            photo_id
            for photo_id in previous.photo_ids
            if (round_number - 1) in picked_rounds_by_id.get(photo_id, set())
        ]
        rounds.append(Round(round_number, next_ids))

    # 各轮 picked 集合 = JSON pickedInRounds 与该轮照片的交集
    for round_ in rounds:
        round_.picked = {
            photo_id
            for photo_id in round_.photo_ids
            if round_.index in picked_rounds_by_id.get(photo_id, set())
        }

    # 最后一轮保留照片：优先取 JSON lastRound.pickedPhotoIds 映射后的照片
    if plan.has_last_round:
        mapped: List[str] = []
        for json_photo_id in plan.last_round_ids:
            relative_path = plan.relative_path_by_id.get(json_photo_id)
            if relative_path is None:
                continue
            scanned = scanned_by_path.get(relative_path.casefold())
            if scanned is None or scanned.id not in rounds[-1]:
                continue
            mapped.append(scanned.id)
        if mapped:
            rounds[-1].picked = set(mapped)

    return rounds


def restore_session(payload: Any, store: SessionStore) -> Session:
    """从结果 JSON 恢复现场并注册为 current session（201 响应体为 summary）。"""
    plan = parse_pick_result(payload)
    scan_result = _scan_for_restore(plan)
    rounds = _rebuild_rounds(plan, scan_result)
    session = Session.from_restore(
        root=scan_result.root,
        recursive=scan_result.recursive,
        photos=scan_result.photos,
        rounds=rounds,
    )
    store.add(session)
    return session
