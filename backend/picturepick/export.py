"""结果导出（docs/API.md 第 3 版 §2、§4）。

* PickResult 生成（含第 2 版加入的 ``origin`` / ``lastRound``）；
* 下载文件名与 JSON 结果文件写入（``POST /api/sessions/{sid}/export``）；
* **第 3 版新增**：``copy_last_round_photos`` —— 把最后一轮保留的照片由后端
  直接复制到用户指定目录（``POST /api/sessions/{sid}/export-photos``），
  同名文件自动追加 ``-1`` / ``-2`` 后缀且绝不覆盖已有文件。
  browser 来源会话不读取本地文件，因此只有 server 来源支持该操作。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from .errors import (
    INVALID_PATH,
    INVALID_SOURCE,
    NO_PICKED_PHOTOS,
    WRITE_FAILED,
    ApiError,
)
from .models import (
    ExportPhotosFile,
    ExportPhotosResponse,
    LastRound,
    PickResult,
    PickResultPhoto,
)
from .scanner import is_within

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
                bird=session.bird_observation(photo.id),
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
        birdIdentifiedCount=session.bird_identified_count,
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


# ---- 第 3 版：导出最后一轮保留照片（后端直接复制文件） ---------------------


def _resolve_export_destination(destination: object) -> str:
    """校验并规范化导出目标目录。

    不存在 / 不是目录 / 空路径 → 400 INVALID_PATH；目录不可写 → 400 WRITE_FAILED。
    """
    if destination is None or not str(destination).strip():
        raise ApiError(INVALID_PATH, "导出目标目录不能为空")
    try:
        target = os.path.abspath(os.path.expanduser(str(destination)))
    except (OSError, ValueError) as exc:
        raise ApiError(INVALID_PATH, f"导出目标目录无效: {exc}") from exc
    if not os.path.isdir(target):
        raise ApiError(INVALID_PATH, f"导出目标目录不存在或不是目录: {target}")
    # 实际写一个探针文件确认可写；比 os.access 在 Windows ACL 场景下更可靠。
    probe_path = None
    try:
        descriptor, probe_path = tempfile.mkstemp(prefix=".picturepick-probe-", dir=target)
        os.close(descriptor)
    except OSError as exc:
        raise ApiError(WRITE_FAILED, f"导出目标目录不可写: {target}（{exc}）") from exc
    finally:
        if probe_path is not None:
            try:
                os.unlink(probe_path)
            except OSError:  # pragma: no cover - 清理尽力而为
                pass
    return target


def _unique_destination_name(directory: str, filename: str, reserved: set) -> str:
    """同名不覆盖：``a.jpg`` 已存在时依次尝试 ``a-1.jpg``、``a-2.jpg``…（保留扩展名）。"""
    stem, extension = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while True:
        key = os.path.normcase(candidate)
        full_path = os.path.join(directory, candidate)
        if key not in reserved and not os.path.exists(full_path):
            reserved.add(key)
            return candidate
        candidate = f"{stem}-{counter}{extension}"
        counter += 1


def _rollback(paths: List[str]) -> None:
    """复制中途失败时，尽力删除本次已写出的文件。"""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover - 清理尽力而为
            pass


def copy_last_round_photos(session: "Session", destination: object) -> ExportPhotosResponse:
    """把最后一轮仍保留（pick）的照片复制到 ``destination``。

    * 仅 ``origin="server"`` 会话可用，browser 来源 → 400 INVALID_SOURCE；
    * 最后一轮没有 pick → 400 NO_PICKED_PHOTOS；
    * 按 ``lastRound.pickedPhotoIds`` 的轮内顺序复制；
    * 目标目录必须已存在且可写；同名文件自动追加 ``-1`` / ``-2`` 后缀，绝不覆盖；
      （因此目标即使指向会话源目录也不会覆盖原图）；
    * 复制失败 → 400 WRITE_FAILED，并尽力回滚本次已复制的文件。
    """
    from .session import ORIGIN_SERVER  # 延迟导入，避免 session <-> export 循环依赖

    if session.origin != ORIGIN_SERVER:
        raise ApiError(
            INVALID_SOURCE,
            "仅 server 来源会话支持导出照片（browser 会话不读取本地文件）",
        )

    last_round = build_last_round(session)
    if not last_round.pickedPhotoIds:
        raise ApiError(NO_PICKED_PHOTOS, "最后一轮没有保留照片，无需导出")

    target_directory = _resolve_export_destination(destination)
    reserved: set[str] = set()
    created: List[str] = []
    files: List[ExportPhotosFile] = []
    try:
        for photo_id in last_round.pickedPhotoIds:
            photo = session.get_photo(photo_id)
            source_path = photo.absolute_path
            if (
                not source_path
                or not is_within(session.root, source_path)
                or not os.path.isfile(source_path)
            ):
                raise ApiError(
                    WRITE_FAILED,
                    f"源照片文件不存在或不可读: {photo.relative_path}",
                )
            try:
                source_size = os.path.getsize(source_path)
            except OSError as exc:
                raise ApiError(
                    WRITE_FAILED,
                    f"无法读取源照片大小: {photo.relative_path}（{exc}）",
                ) from exc
            target_name = _unique_destination_name(target_directory, photo.name, reserved)
            target_path = os.path.join(target_directory, target_name)
            try:
                shutil.copy2(source_path, target_path)
            except OSError as exc:
                try:
                    os.unlink(target_path)
                except OSError:  # pragma: no cover - 清理尽力而为
                    pass
                raise ApiError(
                    WRITE_FAILED,
                    f"复制照片失败: {photo.relative_path} → {target_name}（{exc}）",
                ) from exc
            created.append(target_path)
            files.append(
                ExportPhotosFile(
                    name=target_name,
                    relativePath=photo.relative_path,
                    sizeBytes=source_size,
                )
            )
    except Exception:
        _rollback(created)
        raise
    return ExportPhotosResponse(
        destination=target_directory,
        copied=len(files),
        files=files,
    )
