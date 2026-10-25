"""最终结果（PickResult）生成、下载文件名与导出写盘（docs/API.md 第 2 版 §2、§4）。

第 2 版：
* PickResult 带 ``origin`` 与 ``lastRound``（最后一轮仍保留的照片，供前端一键导出）；
* ``origin=browser`` 时 ``sourceDirectory`` 等于会话 ``sourceLabel``，
  ``photos[].absolutePath`` 为空串；``origin=server`` 保持绝对路径与目录。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from .errors import WRITE_FAILED, ApiError
from .models import LastRound, PickResult, PickResultPhoto

if TYPE_CHECKING:  # 仅类型检查时导入，避免 session <-> export 循环依赖
    from .session import Session


_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def now_local() -> datetime:
    """当前本地时间（带时区偏移，用于 createdAt / generatedAt）。"""
    return datetime.now().astimezone()


def isoformat(moment: datetime) -> str:
    """ISO-8601，秒精度，带时区：``2026-10-24T15:00:00+08:00``。"""
    return moment.isoformat(timespec="seconds")


def build_last_round(session: "Session") -> LastRound:
    """最后一轮仍被 pick 的照片 id，顺序与该轮照片顺序一致。"""
    round_ = session.current_round
    picked_ids = [photo_id for photo_id in round_.photo_ids if photo_id in round_.picked]
    return LastRound(
        index=round_.index,
        pickedCount=len(picked_ids),
        pickedPhotoIds=picked_ids,
    )


def build_result(session: "Session") -> PickResult:
    """由会话当前状态生成 PickResult。

    ``photos`` 包含全部照片，按 ``pickCount desc, relativePath asc`` 排序。
    """
    photos: List[PickResultPhoto] = []
    for photo in session.photos.values():
        photos.append(
            PickResultPhoto(
                id=photo.id,
                name=photo.name,
                relativePath=photo.relative_path,
                absolutePath=photo.absolute_path,
                pickCount=session.pick_count(photo.id),
                pickedInRounds=session.picked_in_rounds(photo.id),
            )
        )
    photos.sort(key=lambda item: (-item.pickCount, item.relativePath))
    return PickResult(
        schemaVersion=1,
        generatedAt=isoformat(now_local()),
        origin=session.origin,
        sourceDirectory=session.source_label,
        recursive=session.recursive,
        totalPhotos=len(photos),
        roundsPlayed=len(session.rounds),
        pickedPhotoCount=sum(1 for item in photos if item.pickCount > 0),
        lastRound=build_last_round(session),
        photos=photos,
    )


def root_label(root: str) -> str:
    """从根目录推导下载文件名中的目录名。

    ``E:\\photos`` -> ``photos``；盘根 ``E:\\`` -> ``E``；非法字符替换为 ``-``。
    """
    try:
        normalized = os.path.normpath(os.path.abspath(root))
    except (OSError, ValueError):  # pragma: no cover - 极端非法 root
        normalized = str(root)
    label = os.path.basename(normalized)
    if not label:
        drive, _ = os.path.splitdrive(normalized)
        label = drive.replace(":", "") or "photos"
    label = _UNSAFE_FILENAME_CHARS.sub("-", label).strip(" .")
    return label or "photos"


def download_filename(root: str, when: Optional[datetime] = None) -> str:
    """``picturepick-<根目录名>-<yyyyMMdd-HHmmss>.json``。"""
    moment = when or now_local()
    return f"picturepick-{root_label(root)}-{moment.strftime('%Y%m%d-%H%M%S')}.json"


def result_json_bytes(result: PickResult) -> bytes:
    """PickResult 的 UTF-8 JSON 字节（下载用）。"""
    return (json.dumps(result.dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def content_disposition(filename: str) -> str:
    """attachment 头；同时提供 ASCII 回退名与 RFC 5987 UTF-8 文件名。"""
    ascii_name = "".join(
        char if 32 <= ord(char) < 127 and char not in '"\\' else "_" for char in filename
    )
    quoted = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


def write_result_file(path: str, result: PickResult) -> str:
    """把结果写入本机文件，返回写出的绝对路径；失败 → 400 WRITE_FAILED。"""
    if path is None or not str(path).strip():
        raise ApiError(WRITE_FAILED, "导出路径不能为空")
    try:
        target = os.path.abspath(os.path.expanduser(str(path)))
    except (OSError, ValueError) as exc:
        raise ApiError(WRITE_FAILED, f"导出路径无效: {exc}") from exc
    data = json.dumps(result.dict(), ensure_ascii=False, indent=2) + "\n"
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
    except OSError as exc:
        raise ApiError(WRITE_FAILED, f"写入文件失败: {exc}") from exc
    return target
