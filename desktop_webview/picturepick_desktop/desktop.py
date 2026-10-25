"""PicturePick 桌面端（WebView2 / pywebview 外壳）。

启动流程：
1. 选一个本机空闲端口；
2. 在后台线程用 uvicorn 启动 FastAPI 后端（与命令行模式同一份代码）；
3. 等待 ``/api/health`` 就绪；
4. 用 pywebview 创建原生窗口加载 ``http://127.0.0.1:<port>/?desktop=webview2``；
5. 通过 pywebview JS API 提供原生「选择目录 / 另存为 JSON」对话框；
6. 窗口关闭后优雅停止后端线程。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

APP_TITLE = "PicturePick 选片"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 840
WINDOW_MIN_WIDTH = 1000
WINDOW_MIN_HEIGHT = 640
BACKGROUND_COLOR = "#0f1115"


# ---------------------------------------------------------------------------
# 路径 / 日志
# ---------------------------------------------------------------------------


def _writable_dir(candidates: list[Optional[str]]) -> Path:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".write-probe-{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return path
        except OSError:
            continue
    fallback = Path(os.environ.get("TEMP") or os.getcwd()) / "picturepick-desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def user_data_dir() -> Path:
    override = os.environ.get("PICTUREPICK_DESKTOP_USER_DATA")
    if override:
        return Path(override).resolve()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return _writable_dir(
            [
                str(Path(local) / "PicturePick") if local else None,
                str(_app_root() / ".userdata"),
                str(Path.home() / "AppData" / "Local" / "PicturePick"),
            ]
        )
    return _writable_dir([str(Path.home() / ".picturepick")])


def log_dir() -> Path:
    override = os.environ.get("PICTUREPICK_DESKTOP_LOG_DIR")
    base = Path(override).resolve() if override else user_data_dir() / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


class Logger:
    def __init__(self) -> None:
        self.file: Optional[Path] = None
        try:
            self.file = log_dir() / "desktop.log"
        except OSError:
            self.file = None

    def __call__(self, *parts: object) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + " ".join(str(p) for p in parts)
        print(line, flush=True)
        if self.file is not None:
            try:
                with self.file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass


log = Logger()


# ---------------------------------------------------------------------------
# 端口 / 后端
# ---------------------------------------------------------------------------


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error: Optional[BaseException] = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    if payload.get("status") == "ok":
                        return
        except BaseException as exc:  # noqa: BLE001 - 轮询期间所有异常都视为未就绪
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"后端在 {timeout:.0f}s 内未就绪：{last_error}")


class BackendServer:
    """在后台线程运行 uvicorn，供 WebView2 窗口访问。"""

    def __init__(self, port: int, log_level: str = "warning") -> None:
        import uvicorn

        from picturepick.app import create_app

        self.port = port
        self.app = create_app()
        self.config = uvicorn.Config(
            app=self.app,
            host="127.0.0.1",
            port=port,
            log_level=log_level,
            access_log=False,
        )
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, name="picturepick-backend", daemon=True)

    def start(self) -> None:
        self.thread.start()
        wait_for_health(f"http://127.0.0.1:{self.port}/api/health")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# pywebview JS API（原生对话框）
# ---------------------------------------------------------------------------


class DesktopApi:
    """暴露给前端的原生能力：window.pywebview.api.*"""

    def __init__(self) -> None:
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def _dialog_path(self, result) -> Optional[str]:
        if not result:
            return None
        if isinstance(result, str):
            return result
        if isinstance(result, (list, tuple)) and result:
            first = result[0]
            return first if isinstance(first, str) else None
        return None

    def pick_directory(self) -> Optional[str]:
        """打开系统目录选择框，返回绝对路径；取消返回 None。"""
        import webview

        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=str(Path.home()))
            return self._dialog_path(result)
        except Exception as exc:  # noqa: BLE001 - 对话框失败时让前端回退
            log("系统目录选择框失败:", exc)
            return None

    def save_json(self, default_name: str, content: str) -> Optional[str]:
        """打开系统「另存为」对话框并写入 JSON，返回保存路径；取消返回 None。"""
        import webview

        if self._window is None or not isinstance(content, str):
            return None
        name = str(default_name or "picturepick-result.json")
        safe_name = "".join(ch for ch in name if ch not in '\\/:*?"<>|') or "picturepick-result.json"
        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=safe_name,
                file_types=("JSON 文件 (*.json)",),
            )
            path = self._dialog_path(result)
            if not path:
                return None
            target = Path(path)
            if target.suffix.lower() != ".json":
                target = target.with_suffix(".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            log("结果 JSON 已保存:", target)
            return str(target)
        except Exception as exc:  # noqa: BLE001
            log("保存结果失败:", exc)
            return None

    def open_path(self, path: str) -> bool:
        if not path:
            return False
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        except Exception as exc:  # noqa: BLE001
            log("打开路径失败:", exc)
            return False


# ---------------------------------------------------------------------------
# 截图 / 自动化验证钩子（仅设置环境变量时启用）
# ---------------------------------------------------------------------------


def _window_rect(title: str):
    """按窗口标题取窗口矩形（用于只截窗口区域，避免截屏到无关内容）。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left, top = rect.left, rect.top
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height
    except Exception:  # noqa: BLE001 - 非 Windows / 失败时退回整屏截图
        return None


def capture_screenshot(window, path: Path) -> bool:
    try:
        from PIL import ImageGrab

        rect = _window_rect(APP_TITLE)
        image = ImageGrab.grab(bbox=rect) if rect else ImageGrab.grab()
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        log("窗口截图已保存:", path, image.size)
        return True
    except Exception as exc:  # noqa: BLE001
        log("窗口截图失败:", exc)
        return False


DEFAULT_EVAL_JS = """
(() => {
  const q = (id) => document.querySelector('[data-testid="' + id + '"]');
  const img = q('viewer-image');
  return JSON.stringify({
    title: document.title,
    url: location.href,
    startView: !!q('start-view'),
    pickerView: !!q('picker-view'),
    resultView: !!q('result-view'),
    total: q('stats-total') ? q('stats-total').textContent.trim() : null,
    index: q('stats-index') ? q('stats-index').textContent.trim() : null,
    picked: q('stats-picked') ? q('stats-picked').textContent.trim() : null,
    imageLoaded: img ? img.dataset.loaded === 'true' : null,
    imageWidth: img ? img.naturalWidth : null,
    pywebview: typeof window.pywebview,
    bridge: !!(window.picturePickDesktop || window.pywebview)
  });
})()
"""


def run_test_hooks(window, title: str) -> None:
    """截图/断言/自动退出：仅供自动化验收使用。"""
    shot = os.environ.get("PICTUREPICK_DESKTOP_SCREENSHOT")
    eval_js = os.environ.get("PICTUREPICK_DESKTOP_EVAL") or DEFAULT_EVAL_JS
    exit_after = os.environ.get("PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT") == "1"
    delay_ms = int(os.environ.get("PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS") or "4500")

    def worker() -> None:
        try:
            time.sleep(delay_ms / 1000.0)
            if eval_js:
                try:
                    result = window.evaluate_js(eval_js)
                    log("EVAL_RESULT:", result)
                    if shot:
                        result_path = Path(shot).with_suffix(".json")
                        result_path.parent.mkdir(parents=True, exist_ok=True)
                        result_path.write_text(str(result), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    log("EVAL_FAILED:", exc)
            if shot:
                capture_screenshot(window, Path(shot))
            if exit_after:
                log("截图/断言完成，自动关闭窗口")
                window.destroy()
        except Exception as exc:  # noqa: BLE001
            log("测试钩子异常:", exc)
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=worker, name="picturepick-desktop-hook", daemon=True).start()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="PicturePick", description="PicturePick 桌面版（WebView2）")
    parser.add_argument("--dir", dest="directory", default=None, help="启动前预扫描的照片目录")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", default=True, help="只扫描顶层目录")
    parser.add_argument("--port", type=int, default=0, help="后端端口（默认自动选择空闲端口）")
    parser.add_argument("--debug", action="store_true", help="开启 pywebview 调试模式")
    parser.add_argument("--minimized", action="store_true", help="以最小化方式启动")
    return parser


def prescan(server: BackendServer, directory: str, recursive: bool) -> None:
    from picturepick.scanner import InvalidDirectoryError, NoPhotosError, scan

    try:
        result = scan(directory, recursive=recursive)
    except (InvalidDirectoryError, NoPhotosError) as exc:
        log("预扫描失败:", exc)
        return
    server.app.state.store.create(root=result.root, recursive=result.recursive, photos=result.photos)
    log(f"预扫描完成：{len(result.photos)} 张照片（{result.root}）")


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    log("PicturePick 桌面版启动", "frozen=" + str(getattr(sys, "frozen", False)))
    log("userData:", user_data_dir())

    port = args.port or find_free_port()
    server = BackendServer(port, log_level="debug" if args.debug else "warning")
    try:
        server.start()
    except Exception as exc:  # noqa: BLE001
        log("后端启动失败:", exc)
        _show_fatal("PicturePick 启动失败", f"本地后端服务启动失败：\n{exc}")
        return 1
    log(f"后端就绪：http://127.0.0.1:{port}/")

    if args.directory:
        prescan(server, args.directory, args.recursive)

    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        log("pywebview 导入失败:", exc)
        _show_fatal(
            "PicturePick 启动失败",
            "缺少 WebView2 运行库或 pywebview 依赖：\n"
            f"{exc}\n\nWindows 11 / 已安装 Edge 的 Windows 10 自带 WebView2 运行时。",
        )
        server.stop()
        return 1

    api = DesktopApi()
    url = f"http://127.0.0.1:{port}/?desktop=webview2"
    window = webview.create_window(
        APP_TITLE,
        url,
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
        background_color=BACKGROUND_COLOR,
        text_select=False,
        minimized=args.minimized,
        on_top=bool(os.environ.get("PICTUREPICK_DESKTOP_SCREENSHOT")),
    )
    if window is None:
        log("窗口创建失败")
        server.stop()
        return 1
    api.attach(window)
    window.events.loaded += lambda: run_test_hooks(window, APP_TITLE)

    storage = user_data_dir() / "webview"
    try:
        webview.start(
            debug=args.debug,
            private_mode=False,
            storage_path=str(storage),
            icon=str(_app_root() / "resources" / "icon.ico") if (_app_root() / "resources" / "icon.ico").exists() else None,
        )
    finally:
        log("窗口已关闭，正在停止后端…")
        server.stop()
        log("已退出")
    return 0


def _show_fatal(title: str, message: str) -> None:
    log(title + ":", message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
