"""把鸟类识别写入照片 EXIF（docs/BIRDID-CONTRACT.md §7）。

实现方式（不新增第三方依赖）：

1. 用 Pillow ``Image.Exif`` 读取/合并已有 EXIF（IFD0、Exif IFD、GPS、MakerNote
   等标签会被重新序列化保留；内嵌缩略图预览可能丢失）；
2. 追加/覆盖写入 ``ImageDescription``、``XPTitle``、``XPSubject``、``XPKeywords``、
   ``XPComment``（后四者为 Windows 资源管理器「属性 → 详细信息」可读的 UTF-16LE 标签）；
3. 把新的 APP1(Exif) 段**原位替换**进原 JPEG 字节流（图像数据字节不变、不重编码）；
4. 内存校验新文件可正常打开后，写同目录临时文件并 ``os.replace`` 原子替换，
   最后恢复原文件的访问/修改时间。

任一步失败都返回 ``ExifOutcome(written=False, error=...)``，不向 API 层抛异常。
"""

from __future__ import annotations

import io
import os
import struct
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image

from ..models import BirdObservation

TAG_IMAGE_DESCRIPTION = 0x010E
TAG_XP_TITLE = 0x9C9B
TAG_XP_COMMENT = 0x9C9C
TAG_XP_KEYWORDS = 0x9C9E
TAG_XP_SUBJECT = 0x9C9F

MAX_EXIF_BYTES = 0xFFFF - 2
MAX_TEXT = 120

JPEG_SOI = b"\xff\xd8"
MARKER_APP0 = 0xE0
MARKER_APP1 = 0xE1
MARKER_SOS = 0xDA
MARKER_EOI = 0xD9
APP_MARKERS = range(0xE0, 0xF0)


@dataclass
class ExifOutcome:
    written: bool
    path: str
    #: 仅在 written=False 时给出失败原因
    error: Optional[str] = None
    #: written=True 但发生降级（如原 EXIF 读取/序列化失败、已写入新的 EXIF）
    warning: Optional[str] = None


# ---- JPEG 段解析 -----------------------------------------------------------


def _iter_segments(data: bytes) -> List[Tuple[int, int, int]]:
    """返回 [(marker, start, end)]；end 为该段结束位置（SOS 时为文件尾）。"""
    if not data.startswith(JPEG_SOI):
        raise ValueError("不是 JPEG 文件")
    segments: List[Tuple[int, int, int]] = []
    total = len(data)
    pos = 2
    while pos < total:
        start = pos
        if data[pos] != 0xFF:
            break
        while pos < total and data[pos] == 0xFF:
            pos += 1
        if pos >= total:
            break
        marker = data[pos]
        pos += 1
        if marker in (0x01,) or 0xD0 <= marker <= 0xD9:
            segments.append((marker, start, pos))
            if marker == MARKER_EOI:
                break
            continue
        if pos + 2 > total:
            break
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        end = pos + length
        if length < 2 or end > total:
            break
        segments.append((marker, start, end))
        if marker == MARKER_SOS:
            break
        pos = end
    return segments


def _find_exif_segment(data: bytes) -> Optional[Tuple[int, int]]:
    for marker, start, end in _iter_segments(data):
        if marker != MARKER_APP1:
            continue
        if data[start + 4 : start + 10] == b"Exif\x00\x00":
            return start, end
    return None


def _insertion_point(data: bytes, segments: List[Tuple[int, int, int]]) -> int:
    """没有 EXIF 时的插入位置：所有 APPn 之后、第一个非 APP 段之前。"""
    for marker, start, _end in segments:
        if marker not in APP_MARKERS:
            return start
    return 2


def _splice_exif(data: bytes, payload: bytes) -> bytes:
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    found = _find_exif_segment(data)
    if found is not None:
        start, end = found
        return data[:start] + app1 + data[end:]
    segments = _iter_segments(data)
    insert_at = _insertion_point(data, segments)
    return data[:insert_at] + app1 + data[insert_at:]


# ---- EXIF 序列化 -----------------------------------------------------------


def _clean(value: object, limit: int = MAX_TEXT) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _utf16(text: str) -> bytes:
    return _clean(text).encode("utf-16-le", "replace")


def _ascii_description(observation: BirdObservation) -> str:
    parts = []
    if observation.speciesLatin:
        parts.append(f"Bird: {observation.speciesLatin}")
    if observation.familyLatin:
        parts.append(f"Family: {observation.familyLatin}")
    if observation.genusLatin:
        parts.append(f"Genus: {observation.genusLatin}")
    if not parts:
        return "PicturePick AI bird identification"
    return "; ".join(parts)[:MAX_TEXT]


def _confidence_percent(observation: BirdObservation) -> Optional[str]:
    if observation.confidence is None:
        return None
    return f"{round(observation.confidence * 100)}%"


def build_tags(observation: BirdObservation) -> dict:
    """构造要写入 EXIF IFD0 的标签字典（值类型与 Pillow 序列化匹配）。"""
    species = _clean(observation.species)
    species_latin = _clean(observation.speciesLatin)
    title = f"{species} {species_latin}".strip()
    family = _clean(observation.family)
    genus = _clean(observation.genus)

    keywords = ["鸟类"]
    for value in (family, genus, species, species_latin):
        if value:
            keywords.append(value)

    comment_parts = [f"PicturePick AI 识别（{_clean(observation.model)}"]
    percent = _confidence_percent(observation)
    if percent:
        comment_parts[-1] += f"，置信度 {percent}"
    comment_parts[-1] += "）"

    return {
        TAG_IMAGE_DESCRIPTION: _ascii_description(observation),
        TAG_XP_TITLE: _utf16(title),
        TAG_XP_SUBJECT: _utf16(" ".join(part for part in (family, genus, species) if part)),
        TAG_XP_KEYWORDS: _utf16(";".join(keywords)),
        TAG_XP_COMMENT: _utf16("".join(comment_parts)),
    }


def _load_existing_exif(path: str) -> Tuple[Image.Exif, Optional[str]]:
    """读取已有 EXIF（尽量把子 IFD 也加载进来）；失败时返回空 Exif + 警告。"""
    exif = Image.Exif()
    warning: Optional[str] = None
    try:
        with Image.open(path) as image:
            existing = image.getexif()
            if existing:
                for tag in (0x8769, 0x8825, 0xA005):
                    try:
                        if tag in existing:
                            existing.get_ifd(tag)
                    except Exception:  # noqa: BLE001 - 损坏的子 IFD 不影响主流程
                        continue
                exif = existing
    except Exception as exc:  # noqa: BLE001 - 读取失败时降级为全新 EXIF
        warning = f"读取原 EXIF 失败，已写入新的 EXIF（{exc}）"
    return exif, warning


def _serialize_exif(path: str, observation: BirdObservation) -> Tuple[bytes, Optional[str]]:
    existing, warning = _load_existing_exif(path)
    tags = build_tags(observation)
    try:
        for tag, value in tags.items():
            existing[tag] = value
        payload = existing.tobytes()
    except Exception as exc:  # noqa: BLE001 - 已有标签异常时降级为全新 EXIF
        fresh = Image.Exif()
        for tag, value in tags.items():
            fresh[tag] = value
        try:
            payload = fresh.tobytes()
        except Exception as inner:  # pragma: no cover - 理论上不会发生
            raise ValueError(f"EXIF 序列化失败: {inner}") from inner
        note = f"原 EXIF 序列化失败，已写入新的 EXIF（{exc}）"
        warning = f"{warning}；{note}" if warning else note
    if not payload.startswith(b"Exif\x00\x00"):
        payload = b"Exif\x00\x00" + payload
    return payload, warning


# ---- 写入 ------------------------------------------------------------------


def _validate_jpeg(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return image.format == "JPEG" and image.size[0] > 0 and image.size[1] > 0
    except Exception:  # noqa: BLE001 - 校验失败即放弃写入
        return False


def _atomic_replace(path: str, data: bytes, stat_result: os.stat_result) -> None:
    directory = os.path.dirname(path) or "."
    descriptor = -1
    temp_path = ""
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".picturepick-exif-", suffix=".jpg.tmp", dir=directory
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = ""
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:  # pragma: no cover
                pass
    try:
        os.utime(path, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns))
    except OSError:  # pragma: no cover - 时间戳恢复失败不视为写入失败
        pass


def write_bird_exif(path: str, observation: BirdObservation) -> ExifOutcome:
    """把识别结果写入照片 EXIF；返回是否成功与失败原因（中文）。"""
    target = str(path or "")
    if not target:
        return ExifOutcome(written=False, path="", error="照片路径为空")
    try:
        stat_result = os.stat(target)
    except OSError as exc:
        return ExifOutcome(written=False, path=target, error=f"照片文件不存在或不可访问（{exc}）")
    if not os.path.isfile(target):
        return ExifOutcome(written=False, path=target, error="目标不是普通文件")

    try:
        with open(target, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return ExifOutcome(written=False, path=target, error=f"读取照片失败（{exc}）")

    try:
        payload, warning = _serialize_exif(target, observation)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        return ExifOutcome(written=False, path=target, error=f"生成 EXIF 失败（{exc}）")

    if len(payload) + 2 > MAX_EXIF_BYTES:
        return ExifOutcome(
            written=False,
            path=target,
            error=f"EXIF 数据过大（{len(payload)} 字节，JPEG 上限约 64KB）",
        )

    try:
        new_data = _splice_exif(data, payload)
    except (ValueError, struct.error) as exc:
        return ExifOutcome(written=False, path=target, error=f"写入 EXIF 失败（{exc}）")

    if not _validate_jpeg(new_data):
        return ExifOutcome(
            written=False,
            path=target,
            error="写入后的 JPEG 校验失败，已保留原文件",
        )

    try:
        _atomic_replace(target, new_data, stat_result)
    except OSError as exc:
        return ExifOutcome(written=False, path=target, error=f"保存照片失败（{exc}）")
    return ExifOutcome(written=True, path=target, error=None, warning=warning)
