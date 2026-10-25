"""缩略图生成与 LRU 内存缓存（docs/API.md §4 image?variant=thumb）。

* ``variant=original``：直接返回磁盘上的 JPEG 原始字节；
* ``variant=thumb``：Pillow 缩小到最长边 ≤ 512 的 JPEG（EXIF 方向已纠正），
  按（绝对路径, mtime, 文件大小, 边长）做 LRU 缓存，避免反复解码大图。
"""

from __future__ import annotations

import io
import os
import threading
from collections import OrderedDict
from typing import Dict, Optional, Tuple

from PIL import Image, ImageOps

from .errors import ApiError, PHOTO_NOT_FOUND

THUMB_MAX_SIDE = 512
THUMB_QUALITY = 85
CACHE_MAX_ENTRIES = 128

_RESAMPLING = getattr(Image, "Resampling", None)
if _RESAMPLING is not None:
    _LANCZOS = _RESAMPLING.LANCZOS
else:  # pragma: no cover - Pillow < 9.1
    _LANCZOS = Image.LANCZOS


CacheKey = Tuple[str, int, int, int]


class ThumbnailCache:
    """线程安全的 LRU 缓存：``OrderedDict`` + 上限条目数。"""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._items: "OrderedDict[CacheKey, bytes]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: CacheKey) -> Optional[bytes]:
        with self._lock:
            data = self._items.get(key)
            if data is None:
                return None
            self._items.move_to_end(key)
            return data

    def put(self, key: CacheKey, data: bytes) -> None:
        with self._lock:
            self._items[key] = data
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:  # pragma: no cover - 调试/测试辅助
        with self._lock:
            return len(self._items)


_CACHE = ThumbnailCache()


def cache() -> ThumbnailCache:
    """返回全局缩略图缓存（测试可 clear）。"""
    return _CACHE


def _cache_key(path: str, max_side: int) -> CacheKey:
    stat = os.stat(path)
    return (os.path.abspath(path), stat.st_mtime_ns, stat.st_size, int(max_side))


def original_bytes(path: str) -> bytes:
    """读取原图字节；文件不存在/不可读 → 404 PHOTO_NOT_FOUND。"""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ApiError(PHOTO_NOT_FOUND, "照片文件不存在或不可读") from exc


def _render_thumbnail(path: str, max_side: int) -> bytes:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((max_side, max_side), _LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=THUMB_QUALITY, optimize=True)
            return buffer.getvalue()
    except Exception as exc:  # Pillow 解码/编码失败统一视为照片不可用
        raise ApiError(PHOTO_NOT_FOUND, f"照片文件无法解码: {exc}") from exc


def thumbnail_bytes(path: str, max_side: int = THUMB_MAX_SIDE) -> bytes:
    """返回缩略图 JPEG 字节（带 LRU 缓存）。"""
    try:
        key = _cache_key(path, max_side)
    except OSError as exc:
        raise ApiError(PHOTO_NOT_FOUND, "照片文件不存在或不可读") from exc
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    data = _render_thumbnail(path, max_side)
    _CACHE.put(key, data)
    return data
