"""从保存的 PickResult JSON 恢复选片现场（docs/API.md 第 3 版 §4 POST /api/sessions/restore）。

恢复策略：

* 请求体 = 之前保存的结果 JSON（顶层对象）；**向后兼容旧版（v1）JSON**：
  - ``schemaVersion`` 缺失按 1 处理，存在且 != 1 → ``UNSUPPORTED_SCHEMA``；
  - ``origin`` 缺失按 ``"server"`` 处理，显式非 server（如 ``"browser"``）→ ``INVALID_SOURCE``；
  - ``roundsPlayed`` 缺失时取 ``max(pickedInRounds)``（至少 1）；
  - ``lastRound`` 缺失时按 ``roundsPlayed`` 从 ``pickedInRounds`` 推导。
* 优先重新扫描 ``sourceDirectory``（沿用 JSON 的 ``recursive``），按 ``relativePath``
  （大小写不敏感）与 JSON 中照片匹配：目录里新增的文件会进入第 1 轮，
  JSON 中已不存在的照片直接忽略（目录总数的口径以当前扫描为准）；
* **源目录不可扫描时的回退**：目录不存在/不可读，或扫描后一张 JSON 照片都匹配不到时，
  用 JSON ``photos[].absolutePath`` 中仍然存在的文件重建照片列表
  （relativePath 用 JSON 值，ID 仍按绝对路径 sha1 计算；仅接受 .jpg/.jpeg 普通文件）。
  最终一张可用照片都没有 → ``INVALID_PATH``，message 含 sourceDirectory 原文与提示。
* 轮次重建：第 1 轮 = 恢复后的照片全集；第 i 轮（i >= 2）= 上一轮中
  ``pickedInRounds`` 包含 ``i-1`` 的照片（保持上一轮顺序）；
* ``pickCount`` / ``pickedInRounds`` 由各轮 picked 集合推导，与 JSON 记录一致；
  ``viewed`` 一律重置为 0；
* 最后一轮保留照片优先取 JSON ``lastRound.pickedPhotoIds``（映射到恢复后的照片），
  缺失时按 ``pickedInRounds`` 包含 ``roundsPlayed`` 推导；
* 恢复后 ``phase="finished"``、``roundIndex=roundsPlayed``、``SessionSummary.restored=true``，
  可继续下载 JSON / 导出照片，也可调用 ``POST /api/sessions/{sid}/resume`` 继续筛选。

错误码：
* ``UNSUPPORTED_SCHEMA``：schemaVersion 存在且不是 1；
* ``INVALID_SOURCE``：origin 显式非 server；
* ``INVALID_PATH``：sourceDirectory 缺失，或源目录与 JSON absolutePath 都不可用；
* ``INVALID_RESTORE``：JSON 结构非法；
* ``NO_PHOTOS``：扫描成功但目录中确实没有 .jpg/.jpeg（回退也无照片时归为 INVALID_PATH）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from . import scanner
from .errors import (
    INVALID_PATH,
    INVALID_RESTORE,
    INVALID_SOURCE,
    UNSUPPORTED_SCHEMA,
    ApiError,
)
from .session import ORIGIN_SERVER, Round, Session, SessionStore
from .scanner import ScannedPhoto

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
    #: casefold(relativePath) -> JSON 原始 relativePath
    relative_path_by_key: Dict[str, str] = field(default_factory=dict)
    #: casefold(relativePath) -> 展示名
    name_by_key: Dict[str, str] = field(default_factory=dict)
    #: casefold(relativePath) -> JSON absolutePath（v1 JSON 回退时使用，可能为空）
    absolute_path_by_key: Dict[str, str] = field(default_factory=dict)
    #: JSON photos 的原始顺序（回退重建时作为第 1 轮顺序）
    ordered_keys: List[str] = field(default_factory=list)
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


@dataclass
class _PhotoEntry:
    key: str
    relative_path: str
    name: str
    absolute_path: str
    picked_rounds: List[int]
    json_id: str


def _parse_photo_entries(photos_raw: List[Any]) -> List[_PhotoEntry]:
    entries: List[_PhotoEntry] = []
    seen_paths: Set[str] = set()
    for index, raw in enumerate(photos_raw):
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
                _require_int(item, f"photos[{index}].pickedInRounds[]", minimum=1)
            )
        if picked_rounds != sorted(set(picked_rounds)):
            raise ApiError(INVALID_RESTORE, f"photos[{index}].pickedInRounds 必须升序且不重复")

        # pickCount 由 pickedInRounds 推导，但仍校验其类型（若存在）。
        if "pickCount" in raw:
            _require_int(raw.get("pickCount"), f"photos[{index}].pickCount", minimum=0)

        absolute_raw = raw.get("absolutePath")
        absolute_path = absolute_raw.strip() if isinstance(absolute_raw, str) else ""
        name_raw = raw.get("name")
        name = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else os.path.basename(relative_path)
        json_id = raw.get("id")
        entries.append(
            _PhotoEntry(
                key=key,
                relative_path=relative_path,
                name=name,
                absolute_path=absolute_path,
                picked_rounds=picked_rounds,
                json_id=json_id if isinstance(json_id, str) else "",
            )
        )
    return entries


def parse_pick_result(payload: Any) -> RestorePlan:
    """校验结果 JSON 并提取恢复所需数据；兼容 v1（无 origin/lastRound/schemaVersion）格式。"""
    if not isinstance(payload, dict):
        raise ApiError(INVALID_RESTORE, "恢复请求体必须是结果 JSON 对象")

    schema_version = payload.get("schemaVersion", SUPPORTED_SCHEMA_VERSION)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ApiError(
            UNSUPPORTED_SCHEMA,
            f"不支持的结果 JSON 版本: {schema_version!r}（仅支持 {SUPPORTED_SCHEMA_VERSION}）",
        )

    # 旧版 JSON 没有 origin 字段，按 v1 语义视为 server；只有显式非 server 才拒绝。
    origin = payload.get("origin")
    if origin is None:
        origin = ORIGIN_SERVER
    if origin != ORIGIN_SERVER:
        raise ApiError(INVALID_SOURCE, "仅支持从 server 来源的结果 JSON 恢复现场")

    source_directory = payload.get("sourceDirectory")
    if not isinstance(source_directory, str) or not source_directory.strip():
        raise ApiError(INVALID_PATH, "结果 JSON 缺少有效的 sourceDirectory")

    recursive = payload.get("recursive", True)
    if not isinstance(recursive, bool):
        recursive = True

    photos = payload.get("photos")
    if not isinstance(photos, list):
        raise ApiError(INVALID_RESTORE, "photos 必须是数组")
    entries = _parse_photo_entries(photos)

    # roundsPlayed 缺失（旧版/被裁剪）时按 pickedInRounds 的最大轮次推导。
    rounds_played_raw = payload.get("roundsPlayed")
    if rounds_played_raw is None:
        derived = max(
            (max(entry.picked_rounds) for entry in entries if entry.picked_rounds),
            default=1,
        )
        rounds_played = max(1, derived)
        if rounds_played > MAX_RESTORE_ROUNDS:
            raise ApiError(
                INVALID_RESTORE,
                f"roundsPlayed 推导值 {rounds_played} 超过上限 {MAX_RESTORE_ROUNDS}",
            )
    else:
        rounds_played = _require_int(
            rounds_played_raw,
            "roundsPlayed",
            minimum=1,
            maximum=MAX_RESTORE_ROUNDS,
        )
        for index, entry in enumerate(entries):
            if any(round_number > rounds_played for round_number in entry.picked_rounds):
                raise ApiError(
                    INVALID_RESTORE,
                    f"photos[{index}].pickedInRounds 超出 roundsPlayed={rounds_played} 范围",
                )

    plan = RestorePlan(
        source_directory=source_directory,
        recursive=recursive,
        rounds_played=rounds_played,
    )
    for entry in entries:
        plan.picked_rounds_by_path[entry.key] = entry.picked_rounds
        plan.relative_path_by_key[entry.key] = entry.relative_path
        plan.name_by_key[entry.key] = entry.name
        if entry.absolute_path:
            plan.absolute_path_by_key[entry.key] = entry.absolute_path
        plan.ordered_keys.append(entry.key)
        if entry.json_id:
            plan.relative_path_by_id[entry.json_id] = entry.relative_path

    last_round = payload.get("lastRound")
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
            plan.last_round_ids.append(item)
        plan.has_last_round = True

    return plan


def _try_scan(plan: RestorePlan) -> Optional[scanner.ScanResult]:
    """尝试扫描源目录；不可读/无照片时返回 None（交给 absolutePath 回退）。"""
    try:
        return scanner.scan(plan.source_directory, recursive=plan.recursive)
    except (scanner.InvalidDirectoryError, scanner.NoPhotosError):
        return None


def _count_matched(plan: RestorePlan, scan_result: scanner.ScanResult) -> int:
    scanned_keys = {photo.relative_path.casefold() for photo in scan_result.photos}
    return sum(1 for key in plan.picked_rounds_by_path if key in scanned_keys)


def _photos_from_absolute_paths(plan: RestorePlan) -> List[ScannedPhoto]:
    """源目录不可扫描时，用 JSON absolutePath 中仍存在的 .jpg/.jpeg 文件重建照片。

    顺序按 JSON photos 的顺序；relativePath 使用 JSON 值；ID 按绝对路径 sha1 计算。
    v1 JSON 的 absolutePath 可能指向网络盘/移动盘，不存在的文件会跳过。
    """
    photos: List[ScannedPhoto] = []
    seen_absolute: Set[str] = set()
    for key in plan.ordered_keys:
        raw_absolute = plan.absolute_path_by_key.get(key)
        if not raw_absolute:
            continue
        try:
            absolute = os.path.abspath(os.path.expanduser(raw_absolute))
        except (OSError, ValueError):
            continue
        if not scanner.is_photo_name(os.path.basename(absolute)):
            continue
        try:
            if not os.path.isfile(absolute):
                continue
            size_bytes = os.path.getsize(absolute)
        except OSError:
            continue
        absolute_key = os.path.normcase(absolute)
        if absolute_key in seen_absolute:
            continue
        seen_absolute.add(absolute_key)
        photos.append(
            ScannedPhoto(
                id=scanner.photo_id(absolute),
                name=plan.name_by_key.get(key) or os.path.basename(absolute),
                relative_path=plan.relative_path_by_key[key],
                absolute_path=absolute,
                size_bytes=size_bytes,
            )
        )
    return photos


def _common_root(photos: List[ScannedPhoto]) -> str:
    """回退重建时的会话根目录：所有照片所在目录的公共父目录（用于图片/导出的路径安全）。"""
    parents = sorted({os.path.dirname(photo.absolute_path) for photo in photos})
    if not parents:
        return ""
    try:
        common = os.path.commonpath(parents)
    except ValueError:  # 不同盘符：退化为第一张照片所在目录
        common = parents[0]
    return os.path.abspath(common)


def _rebuild_rounds(plan: RestorePlan, photos: List[ScannedPhoto]) -> List[Round]:
    """按 JSON 的 pickedInRounds 重建各轮照片顺序与 picked 集合。"""
    photos_by_key = {photo.relative_path.casefold(): photo for photo in photos}
    picked_rounds_by_id: Dict[str, Set[int]] = {}
    for key, picked_rounds in plan.picked_rounds_by_path.items():
        photo = photos_by_key.get(key)
        if photo is None:  # JSON 中已不存在的照片忽略
            continue
        picked_rounds_by_id[photo.id] = set(picked_rounds)

    if not picked_rounds_by_id:
        raise ApiError(
            INVALID_PATH,
            f"无法访问 JSON 中的源目录或照片：{plan.source_directory}"
            "；请确认网络盘/移动盘已连接，或照片未被移动",
        )

    rounds: List[Round] = [Round(1, [photo.id for photo in photos])]
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

    # 最后一轮保留照片：优先取 JSON lastRound.pickedPhotoIds 映射后的照片；
    # 旧版 JSON 没有 lastRound 时保留上面的推导结果（pickedInRounds 含 roundsPlayed）。
    if plan.has_last_round:
        mapped: List[str] = []
        for json_photo_id in plan.last_round_ids:
            relative_path = plan.relative_path_by_id.get(json_photo_id)
            if relative_path is None:
                continue
            photo = photos_by_key.get(relative_path.casefold())
            if photo is None or photo.id not in rounds[-1]:
                continue
            mapped.append(photo.id)
        if mapped:
            rounds[-1].picked = set(mapped)

    return rounds


def restore_session(payload: Any, store: SessionStore) -> Session:
    """从结果 JSON 恢复现场并注册为 current session（201 响应体为 summary）。"""
    plan = parse_pick_result(payload)

    scan_result = _try_scan(plan)
    if scan_result is not None and _count_matched(plan, scan_result) > 0:
        photos = scan_result.photos
        root = scan_result.root
        source_label = scan_result.root
        recursive = scan_result.recursive
    else:
        # 源目录不可扫描，或扫描结果里一张 JSON 照片都匹配不到 → 用 absolutePath 回退
        photos = _photos_from_absolute_paths(plan)
        if not photos:
            raise ApiError(
                INVALID_PATH,
                f"无法访问 JSON 中的源目录或照片：{plan.source_directory}"
                "；请确认网络盘/移动盘已连接，或照片未被移动",
            )
        root = _common_root(photos)
        source_label = plan.source_directory
        recursive = plan.recursive

    rounds = _rebuild_rounds(plan, photos)
    session = Session.from_restore(
        root=root,
        recursive=recursive,
        photos=photos,
        rounds=rounds,
        source_label=source_label,
    )
    store.add(session)
    return session
