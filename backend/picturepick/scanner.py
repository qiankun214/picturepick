"""照片来源解析（docs/API.md 第 2 版 §0、§4）。

两种来源：

* **server**：后端扫描真实目录（CLI ``--dir`` / 桌面端 ``--dir``），
  规则为扩展名 ``.jpg`` / ``.jpeg``（大小写不敏感）的普通文件；递归模式下遍历
  所有子目录，非递归只扫描顶层；按 ``relativePath`` 不区分大小写升序排序，
  与 ``/rounds/{index}/photos`` 的放映顺序一致；``relativePath`` 统一使用 ``/``
  分隔符，照片 ID = ``sha1(绝对路径)[:16]``。
* **browser**：前端用浏览器原生目录选择框拿到文件后只上传元数据，
  后端不读文件系统；``relativePath`` 必须是安全的相对路径，照片 ID =
  ``sha1(relativePath)[:16]``，``absolutePath`` 为空串。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

PHOTO_EXTENSIONS = (".jpg", ".jpeg")

#: browser 来源单次最多接收的照片元数据条数（docs/API.md §4）
MAX_BROWSER_PHOTOS = 50000

#: browser 来源 relativePath 中不允许出现的字符（Windows 文件名非法字符）
_BROWSER_INVALID_PATH_CHARS = frozenset('<>:"|?*')

#: 形如 ``C:`` / ``c:`` 的盘符前缀
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class ScanError(Exception):
    """扫描/来源解析相关错误基类。"""


class InvalidDirectoryError(ScanError):
    """目录不存在、不是目录或不可读。"""


class NoPhotosError(ScanError):
    """没有任何 .jpg/.jpeg 照片。"""


class InvalidPhotosError(ScanError):
    """browser 来源的照片元数据格式非法。"""


class TooManyPhotosError(ScanError):
    """browser 来源的照片数量超过上限。"""


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


# ---- browser 来源（照片元数据，不读文件系统） ------------------------------


def _normalize_browser_relative_path(value: Any, index: int) -> str:
    """校验并规范化一条 browser 来源的 ``relativePath``，非法则抛 InvalidPhotosError。"""
    if not isinstance(value, str):
        raise InvalidPhotosError(f"photos[{index}].relativePath 必须是字符串")
    if not value.strip():
        raise InvalidPhotosError(f"photos[{index}].relativePath 不能为空")
    if any(ord(char) < 32 for char in value):
        raise InvalidPhotosError(f"photos[{index}].relativePath 含非法控制字符")

    # 兼容误用反斜杠的客户端；契约要求正斜杠，统一后再校验。
    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        raise InvalidPhotosError(f"photos[{index}].relativePath 不允许绝对路径: {value!r}")
    if _DRIVE_PREFIX.match(normalized):
        raise InvalidPhotosError(f"photos[{index}].relativePath 不允许盘符: {value!r}")

    segments = normalized.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise InvalidPhotosError(
                f"photos[{index}].relativePath 含非法路径段（空段 / . / ..）: {value!r}"
            )
        if any(char in _BROWSER_INVALID_PATH_CHARS for char in segment):
            raise InvalidPhotosError(f"photos[{index}].relativePath 含非法字符: {value!r}")

    filename = segments[-1]
    if os.path.splitext(filename)[1].lower() not in PHOTO_EXTENSIONS:
        raise InvalidPhotosError(
            f"photos[{index}].relativePath 不是 .jpg/.jpeg 文件: {value!r}"
        )
    return normalized


def _browser_size_bytes(raw: Dict[str, Any], index: int) -> int:
    value = raw.get("sizeBytes", 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidPhotosError(f"photos[{index}].sizeBytes 必须是非负整数")
    return int(value)


def _validate_last_modified(raw: Dict[str, Any], index: int) -> None:
    """``lastModified`` 仅做类型校验，不参与后端状态（展示与导出由前端负责）。"""
    value = raw.get("lastModified")
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidPhotosError(f"photos[{index}].lastModified 必须是非负整数")


def build_browser_photos(raw_photos: Any) -> List[ScannedPhoto]:
    """把 browser 来源的照片元数据转换为 ``ScannedPhoto`` 列表。

    * 空列表 → :class:`NoPhotosError`；格式非法 → :class:`InvalidPhotosError`；
    * 超过 :data:`MAX_BROWSER_PHOTOS` → :class:`TooManyPhotosError`；
    * 相同 ``relativePath`` 去重（保留首次出现的一条）；
    * 返回结果按 ``relativePath`` 不区分大小写升序排序；
    * ``id = sha1(relativePath)[:16]``，``absolute_path = ""``。
    """
    if raw_photos is None:
        raise InvalidPhotosError("browser 来源必须提供 photos 列表")
    if not isinstance(raw_photos, list):
        raise InvalidPhotosError("photos 必须是数组")
    if len(raw_photos) > MAX_BROWSER_PHOTOS:
        raise TooManyPhotosError(
            f"照片数量 {len(raw_photos)} 超过上限 {MAX_BROWSER_PHOTOS}"
        )
    if not raw_photos:
        raise NoPhotosError("照片列表为空，没有 .jpg/.jpeg 照片")

    photos: List[ScannedPhoto] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_photos):
        if not isinstance(raw, dict):
            raise InvalidPhotosError(f"photos[{index}] 必须是对象")
        relative_path = _normalize_browser_relative_path(raw.get("relativePath"), index)
        size_bytes = _browser_size_bytes(raw, index)
        _validate_last_modified(raw, index)
        if relative_path in seen:  # 同一 relativePath 只保留一条
            continue
        seen.add(relative_path)
        photos.append(
            ScannedPhoto(
                id=photo_id(relative_path),
                name=relative_path.rsplit("/", 1)[-1],
                relative_path=relative_path,
                absolute_path="",
                size_bytes=size_bytes,
            )
        )

    if not photos:  # pragma: no cover - 上面的空列表检查已覆盖
        raise NoPhotosError("照片列表为空，没有 .jpg/.jpeg 照片")
    photos.sort(key=lambda photo: _sort_key(photo.relative_path))
    return photos
