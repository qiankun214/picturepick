"""命令行入口：``serve`` / ``scan`` / ``version``（docs/API.md §6）。

* ``serve`` 默认启动后自动打开系统浏览器，``--no-open``（别名 ``--no-browser``）关闭（桌面端使用）；
* 启动成功后在 stdout 打印约定行：
  ``PicturePick server listening on http://127.0.0.1:<port>``（桌面端据此判断就绪）；
* 端口被占用时打印清晰错误并以非 0 退出码退出。
"""

from __future__ import annotations

import argparse
import socket
import sys
import webbrowser
from typing import Optional, Sequence

import uvicorn

from . import __version__
from .app import create_app
from .errors import ApiError
from .scanner import InvalidDirectoryError, NoPhotosError, scan

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")

#: 桌面端依赖的启动约定行前缀
READY_PREFIX = "PicturePick server listening on "


class _PicturePickServer(uvicorn.Server):
    """uvicorn Server 的子类：启动成功后触发一次回调（打印约定行 / 打开浏览器）。"""

    def __init__(self, config: uvicorn.Config, on_ready=None) -> None:
        super().__init__(config)
        self._on_ready = on_ready

    async def startup(self, sockets=None) -> None:  # type: ignore[override]
        await super().startup(sockets=sockets)
        if self.started and self._on_ready is not None:
            callback, self._on_ready = self._on_ready, None
            callback(self)


# ---- 辅助 ------------------------------------------------------------------


def _port_type(text: str) -> int:
    try:
        value = int(str(text))
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"无效端口: {text!r}")
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 0-65535 之间")
    return value


def _display_host(host: str) -> str:
    if not host or host in ("0.0.0.0", "::", "::0"):
        return "127.0.0.1"
    return host


def _port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        # 注意：不要设置 SO_REUSEADDR —— Windows 上会允许探测到已被占用的端口。
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _bound_port(server: uvicorn.Server) -> Optional[int]:
    """启动后取实际监听端口（``--port 0`` 时由系统分配）。"""
    try:
        for server_obj in getattr(server, "servers", []):
            for sock in getattr(server_obj, "sockets", None) or ():
                address = sock.getsockname()
                if isinstance(address, tuple) and len(address) >= 2:
                    return int(address[1])
    except Exception:  # pragma: no cover - 防御性
        return None
    return None


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - 无浏览器环境的尽力而为
        pass


def _configure_stdio_utf8() -> None:
    """管道/桌面端场景统一 UTF-8 输出，避免中文在日志里乱码。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):  # pragma: no cover - 已被重定向
            pass


# ---- 子命令实现 ------------------------------------------------------------


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        result = scan(args.directory, recursive=args.recursive)
    except InvalidDirectoryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except NoPhotosError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Directory : {result.root}")
    print(f"Recursive : {'yes' if result.recursive else 'no'}")
    print(f"Photos    : {len(result.photos)}")
    for photo in result.photos[:10]:
        print(f"  {photo.relative_path}")
    if len(result.photos) > 10:
        print(f"  ... 还有 {len(result.photos) - 10} 张")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    app = create_app()
    store = app.state.store

    if args.directory:
        try:
            result = scan(args.directory, recursive=args.recursive)
        except (InvalidDirectoryError, NoPhotosError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        store.create(root=result.root, recursive=result.recursive, photos=result.photos)
        print(f"Pre-scanned {len(result.photos)} photo(s) from {result.root}", flush=True)

    if args.port and not _port_is_free(args.host, args.port):
        print(f"Error: port {args.port} is already in use on {args.host}", file=sys.stderr)
        return 1

    def on_ready(server: uvicorn.Server) -> None:
        port = _bound_port(server) or args.port
        url = f"http://{_display_host(args.host)}:{port}"
        print(f"{READY_PREFIX}{url}", flush=True)
        if not args.no_open:
            _open_browser(url)

    config = uvicorn.Config(
        app=app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )
    server = _PicturePickServer(config, on_ready)
    try:
        server.run()
    except KeyboardInterrupt:
        # 用户 Ctrl+C：uvicorn 已优雅退出，视为正常结束
        return 0
    except SystemExit as exc:
        if not server.started:
            print(
                f"Error: failed to start PicturePick server on "
                f"http://{_display_host(args.host)}:{args.port}",
                file=sys.stderr,
            )
            code = exc.code
            return code if isinstance(code, int) and code != 0 else 1
        raise
    return 0 if server.started else 1


# ---- argparse / main -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="picturepick",
        description="PicturePick 本地选片服务（FastAPI）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"picturepick {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{serve,scan,version}")

    serve = subparsers.add_parser("serve", help="启动本地选片服务")
    serve.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    serve.add_argument(
        "--port",
        type=_port_type,
        default=DEFAULT_PORT,
        help=f"监听端口（默认 {DEFAULT_PORT}）",
    )
    serve.add_argument("--dir", dest="directory", default=None, help="启动前预扫描的照片目录")
    serve.add_argument(
        "--recursive",
        dest="recursive",
        action="store_true",
        default=True,
        help="递归扫描子目录（默认开启）",
    )
    serve.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="只扫描顶层目录",
    )
    serve.add_argument(
        "--no-open",
        "--no-browser",
        dest="no_open",
        action="store_true",
        help="启动后不自动打开浏览器（桌面端使用）",
    )
    serve.add_argument(
        "--log-level",
        dest="log_level",
        default="info",
        choices=list(LOG_LEVELS),
        help="uvicorn 日志级别（默认 info）",
    )
    serve.set_defaults(func=_cmd_serve)

    scan_parser = subparsers.add_parser("scan", help="扫描目录并统计照片")
    scan_parser.add_argument("--dir", dest="directory", required=True, help="照片目录")
    scan_parser.add_argument(
        "--recursive",
        dest="recursive",
        action="store_true",
        default=True,
        help="递归扫描子目录（默认开启，与 serve 一致）",
    )
    scan_parser.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="只扫描顶层目录",
    )
    scan_parser.set_defaults(func=_cmd_scan)

    version_parser = subparsers.add_parser("version", help="打印版本号")
    version_parser.set_defaults(func=_cmd_version)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args) or 0)
    except KeyboardInterrupt:  # pragma: no cover - 交互场景
        print("\nInterrupted", file=sys.stderr)
        return 130
    except ApiError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
