"""照片目录扫描（docs/API.md §0）。

规则：
* 照片 = 扩展名 ``.jpg`` / ``.jpeg``（大小写不敏感）的普通文件；
* 递归模式下遍历所有子目录；非递归只扫描顶层；
* 按 ``relativePath`` 不区分大小写升序排序（再按原始字符串打破平局），
  与 ``/rounds/{index}/photos`` 的放映顺序一致；
* ``relativePath`` 统一使用 ``/`` 作为分隔符，``absolutePath`` 使用本机路径。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional


PHOTO_EXTENSIONS = (".jpg", ".jpeg")


class ScanError(Exception):
    """扫描相关错误基类。"""


class InvalidDirectoryError(ScanError):
    """目录不存在、不是目录或不可读。"""


class NoPhotosError(ScanError):
    """目录中没有任何 .jpg/.jpeg 照片。"""


@dataclass(frozen=True)
class ScannedPhoto:
    id: str
    name: str
    relative_path: str
    absolute_path: str
    size_bytes: int


@dataclass(frozen=True)
class ScanResult:
    root: str
    recursive: bool
    photos: List[ScannedPhoto]

    @property
    def count(self) -> int:
        return len(self.photos)


def photo_id(absolute_path: str) -> str:
    """照片 ID = ``sha1(绝对路径字符串)[:16]``（十六进制小写）。"""
    return hashlib.sha1(absolute_path.encode("utf-8")).hexdigest()[:16]


def normalize_directory(directory: str) -> str:
    """把用户输入规范为可用的绝对目录路径，失败抛 ``InvalidDirectoryError``。"""
    if directory is None:
        raise InvalidDirectoryError("目录不存在或不可读")
    raw = str(directory).strip()
    if not raw:
        raise InvalidDirectoryError("目录不存在或不可读")
    try:
        expanded = os.path.expanduser(raw)
        absolute = os.path.abspath(expanded)
    except (OSError, ValueError) as exc:  # pragma: no cover - 极少数非法路径
        raise InvalidDirectoryError(f"目录不存在或不可读: {exc}") from exc
    if not os.path.isdir(absolute):
        raise InvalidDirectoryError(f"目录不存在或不可读: {absolute}")
    try:
        with os.scandir(absolute) as entries:
            next(entries, None)
    except OSError as exc:
        raise InvalidDirectoryError(f"目录不存在或不可读: {exc}") from exc
    return absolute


def is_photo_name(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in PHOTO_EXTENSIONS


def _sort_key(text: str):
    return (text.casefold(), text)


def _build_photo(root: str, full_path: str) -> Optional[ScannedPhoto]:
    try:
        if not os.path.isfile(full_path):
            return None
        size = os.path.getsize(full_path)
    except OSError:
        # 扫描过程中被删除 / 无权限的文件直接跳过，不阻断整个扫描。
        return None
    absolute = os.path.abspath(full_path)
    relative = os.path.relpath(absolute, root).replace(os.sep, "/")
    return ScannedPhoto(
        id=photo_id(absolute),
        name=os.path.basename(absolute),
        relative_path=relative,
        absolute_path=absolute,
        size_bytes=size,
    )


def _scan_recursive(root: str) -> List[ScannedPhoto]:
    photos: List[ScannedPhoto] = []
    visited: set[str] = set()

    def _on_error(_error: OSError) -> None:  # 忽略不可读子目录
        return None

    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_error, followlinks=False):
        real_dir = os.path.realpath(dirpath)
        if real_dir in visited:  # 防御 junction / symlink 造成的环
            dirnames[:] = []
            continue
        visited.add(real_dir)
        dirnames.sort(key=_sort_key)
        for filename in sorted(filenames, key=_sort_key):
            if not is_photo_name(filename):
                continue
            photo = _build_photo(root, os.path.join(dirpath, filename))
            if photo is not None:
                photos.append(photo)
    return photos


def _scan_flat(root: str) -> List[ScannedPhoto]:
    photos: List[ScannedPhoto] = []
    try:
        with os.scandir(root) as entries:
            candidates = [
                entry.name
                for entry in entries
                if is_photo_name(entry.name) and entry.is_file()
            ]
    except OSError as exc:
        raise InvalidDirectoryError(f"目录不存在或不可读: {exc}") from exc
    for filename in sorted(candidates, key=_sort_key):
        photo = _build_photo(root, os.path.join(root, filename))
        if photo is not None:
            photos.append(photo)
    return photos


def scan(directory: str, recursive: bool = True) -> ScanResult:
    """扫描目录，返回按放映顺序排序的照片列表。"""
    root = normalize_directory(directory)
    photos = _scan_recursive(root) if recursive else _scan_flat(root)
    photos.sort(key=lambda photo: _sort_key(photo.relative_path))
    if not photos:
        raise NoPhotosError(f"目录中没有 .jpg/.jpeg 照片: {root}")
    return ScanResult(root=root, recursive=bool(recursive), photos=photos)


def is_within(root: str, path: str) -> bool:
    """判断 ``path`` 是否位于 ``root`` 目录之内（防御路径逃逸）。"""
    try:
        root_abs = os.path.normcase(os.path.abspath(root))
        path_abs = os.path.normcase(os.path.abspath(path))
        if not root_abs or not path_abs:
            return False
        return os.path.commonpath([root_abs, path_abs]) == root_abs
    except (OSError, ValueError):
        return False
