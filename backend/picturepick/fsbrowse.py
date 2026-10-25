"""文件系统浏览与原生目录选择框（docs/API.md §3）。

* ``/api/fs/roots``：盘符列表 + home + startPath；
* ``/api/fs/list``：只列目录（目录选择器用），名称不区分大小写排序，最多 500 条；
* ``/api/dialog/pick-directory``：尽量用 tkinter 调起原生目录选择框，
  无 GUI / 打包环境不可用时抛 ``DIALOG_UNAVAILABLE``（501），前端回退到目录浏览。
"""

from __future__ import annotations

import os
from typing import List, Optional

from .errors import DIALOG_UNAVAILABLE, INVALID_PATH, ApiError
from .models import DialogResult, FsEntry, FsListResponse, FsRoot, FsRootsResponse

MAX_FS_ENTRIES = 500
DIALOG_DISABLE_ENV = "PICTUREPICK_DISABLE_DIALOG"
_DISABLED_VALUES = {"1", "true", "yes", "on"}


def _windows_drives() -> List[FsRoot]:
    drives: List[FsRoot] = []
    try:  # Windows：一次调用拿到所有逻辑盘
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            if bitmask & (1 << index):
                letter = chr(ord("A") + index)
                root = f"{letter}:\\"
                drives.append(FsRoot(name=root, path=root))
        return drives
    except Exception:  # pragma: no cover - 非 Windows / ctypes 不可用
        pass
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(FsRoot(name=root, path=root))
    return drives


def _posix_roots() -> List[FsRoot]:
    return [FsRoot(name="/", path="/")]


def list_roots() -> List[FsRoot]:
    if os.name == "nt":
        roots = _windows_drives()
        if roots:
            return roots
    return _posix_roots()


def home_directory() -> str:
    try:
        home = os.path.abspath(os.path.expanduser("~"))
    except (OSError, ValueError):  # pragma: no cover
        home = ""
    if home and os.path.isdir(home):
        return home
    roots = list_roots()
    return roots[0].path if roots else ""


def get_roots() -> FsRootsResponse:
    roots = list_roots()
    home = home_directory()
    start_path = home or (roots[0].path if roots else "")
    return FsRootsResponse(home=home, startPath=start_path, roots=roots)


def list_directories(path: str) -> FsListResponse:
    """只列目录；路径不存在 / 不是目录 / 不可读 → 400 INVALID_PATH。"""
    raw = (path or "").strip()
    if not raw:
        raise ApiError(INVALID_PATH, "目录不存在或不可读")
    try:
        absolute = os.path.abspath(os.path.expanduser(raw))
    except (OSError, ValueError) as exc:
        raise ApiError(INVALID_PATH, f"目录不存在或不可读: {exc}") from exc
    if not os.path.isdir(absolute):
        raise ApiError(INVALID_PATH, f"目录不存在或不可读: {absolute}")

    try:
        names = os.listdir(absolute)
    except OSError as exc:
        raise ApiError(INVALID_PATH, f"目录不存在或不可读: {exc}") from exc

    entries: List[FsEntry] = []
    for name in sorted(names, key=lambda item: (item.casefold(), item)):
        if len(entries) >= MAX_FS_ENTRIES:
            break
        full_path = os.path.join(absolute, name)
        try:
            if not os.path.isdir(full_path):
                continue
        except OSError:  # pragma: no cover - 权限/竞态
            continue
        entries.append(FsEntry(name=name, path=full_path, isDir=True))

    parent: Optional[str] = os.path.dirname(absolute)
    if not parent or os.path.normcase(parent) == os.path.normcase(absolute):
        parent = None
    return FsListResponse(path=absolute, parent=parent, entries=entries)


def dialog_disabled() -> bool:
    return os.environ.get(DIALOG_DISABLE_ENV, "").strip().lower() in _DISABLED_VALUES


def pick_directory() -> Optional[str]:
    """调起原生目录选择框；取消返回 ``None``，不可用抛 501 DIALOG_UNAVAILABLE。"""
    if dialog_disabled():
        raise ApiError(DIALOG_UNAVAILABLE, "当前环境不支持原生目录选择框")
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise ApiError(DIALOG_UNAVAILABLE, f"当前环境不支持原生目录选择框: {exc}") from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:  # pragma: no cover - 个别平台不支持 topmost
            pass
        root.update()
        selected = filedialog.askdirectory(parent=root, title="选择照片目录", mustexist=True)
    except Exception as exc:
        raise ApiError(DIALOG_UNAVAILABLE, f"无法显示原生目录选择框: {exc}") from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:  # pragma: no cover
                pass

    if not selected:
        return None
    return os.path.abspath(selected)


def pick_directory_result() -> DialogResult:
    selected = pick_directory()
    return DialogResult(path=selected, cancelled=selected is None)
