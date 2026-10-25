"""CLI 测试：version / scan / serve 参数与端口占用退出码。"""

from __future__ import annotations

import socket

import pytest

import picturepick.cli as cli_module
from helpers import make_jpeg
from picturepick.cli import build_parser, main


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"


def test_no_command_prints_help_and_returns_2(capsys):
    assert main([]) == 2
    assert "serve" in capsys.readouterr().out


def test_scan_command_outputs_counts(capsys, library):
    assert main(["scan", "--dir", str(library)]) == 0
    out = capsys.readouterr().out
    assert "Directory : " in out
    assert "Recursive : yes" in out
    assert "Photos    : 6" in out

    assert main(["scan", "--dir", str(library), "--no-recursive"]) == 0
    out = capsys.readouterr().out
    assert "Recursive : no" in out
    assert "Photos    : 3" in out


def test_scan_command_errors(capsys, tmp_path):
    assert main(["scan", "--dir", str(tmp_path / "missing")]) == 1
    assert "Error:" in capsys.readouterr().err

    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["scan", "--dir", str(empty)]) == 1
    assert "Error:" in capsys.readouterr().err


def test_parser_defaults():
    parser = build_parser()

    serve = parser.parse_args(["serve"])
    assert serve.recursive is True
    assert serve.no_open is False
    assert serve.host == "127.0.0.1"
    assert serve.port == 8000
    assert serve.log_level == "info"
    assert serve.directory is None

    serve = parser.parse_args(["serve", "--no-recursive", "--no-browser", "--port", "9001"])
    assert serve.recursive is False
    assert serve.no_open is True
    assert serve.port == 9001

    scan_args = parser.parse_args(["scan", "--dir", "x"])
    assert scan_args.recursive is True

    scan_args = parser.parse_args(["scan", "--dir", "x", "--no-recursive"])
    assert scan_args.recursive is False


def test_invalid_port_is_rejected_by_argparse():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["serve", "--port", "70000"])
    with pytest.raises(SystemExit):
        parser.parse_args(["serve", "--port", "abc"])


def test_serve_port_in_use_exits_nonzero(capsys):
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]

        code = main(["serve", "--host", "127.0.0.1", "--port", str(port), "--no-open"])

        assert code != 0
        captured = capsys.readouterr()
        assert "already in use" in captured.err
    finally:
        occupied.close()


def test_serve_invalid_dir_exits_nonzero(capsys, tmp_path):
    code = main(["serve", "--dir", str(tmp_path / "missing"), "--no-open"])
    assert code == 1
    assert "Error:" in capsys.readouterr().err


def test_serve_pre_scan_does_not_start_server_without_dir(capsys, monkeypatch, library):
    """--dir 有效但端口冲突时，应先预扫描再快速失败（不会遗留后台服务）。"""
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        code = main(["serve", "--dir", str(library), "--port", str(port), "--no-open"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Pre-scanned 6 photo(s)" in captured.out
        assert "already in use" in captured.err
    finally:
        occupied.close()


def test_scan_single_file(tmp_path):
    root = tmp_path / "one"
    make_jpeg(root / "only.jpg")
    assert main(["scan", "--dir", str(root)]) == 0


def _patch_serve_for_fake_startup(monkeypatch, opened_urls):
    """用假的 uvicorn Server 替换真实启动，验证启动行与浏览器行为。"""
    monkeypatch.setattr(cli_module, "_port_is_free", lambda host, port: True)
    monkeypatch.setattr(cli_module, "_open_browser", lambda url: opened_urls.append(url))

    class FakeServer:
        def __init__(self, config, on_ready):
            self.config = config
            self.started = True
            self.servers = []
            self._on_ready = on_ready

        def run(self):
            self._on_ready(self)

    monkeypatch.setattr(cli_module, "_PicturePickServer", FakeServer)


def test_serve_prints_contract_line_and_opens_browser_by_default(monkeypatch, capsys):
    opened = []
    _patch_serve_for_fake_startup(monkeypatch, opened)

    assert main(["serve", "--port", "8123"]) == 0
    captured = capsys.readouterr()
    assert "PicturePick server listening on http://127.0.0.1:8123" in captured.out
    assert opened == ["http://127.0.0.1:8123"]


def test_serve_no_open_suppresses_browser(monkeypatch, capsys):
    opened = []
    _patch_serve_for_fake_startup(monkeypatch, opened)

    assert main(["serve", "--port", "8123", "--no-open"]) == 0
    assert opened == []
    assert "PicturePick server listening on http://127.0.0.1:8123" in capsys.readouterr().out

    assert main(["serve", "--port", "8123", "--no-browser"]) == 0
    assert opened == []


def test_serve_binds_loopback_display_for_wildcard_host(monkeypatch, capsys):
    opened = []
    _patch_serve_for_fake_startup(monkeypatch, opened)

    assert main(["serve", "--host", "0.0.0.0", "--port", "8123", "--no-open"]) == 0
    assert "PicturePick server listening on http://127.0.0.1:8123" in capsys.readouterr().out
