"""pytest 共享夹具。

所有测试都使用 tmp_path 下的真实 JPEG 文件（Pillow 生成），不依赖外部样例。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from helpers import make_jpeg  # noqa: E402
from picturepick.app import create_app  # noqa: E402
from picturepick.session import SessionStore  # noqa: E402

#: library 夹具中的照片（含大写扩展名与子目录）
LIBRARY_FILES = (
    "a.jpg",
    "b.JPG",
    "c.jpeg",
    "sub/d.jpg",
    "sub/e.JPEG",
    "sub/nested/f.jpg",
)
LIBRARY_COUNT_RECURSIVE = len(LIBRARY_FILES)  # 6
LIBRARY_COUNT_FLAT = 3  # 只有顶层 a/b/c


@pytest.fixture(autouse=True)
def disable_native_dialog(monkeypatch):
    """防止测试环境真的弹出 tkinter 目录选择框而挂住。"""
    monkeypatch.setenv("PICTUREPICK_DISABLE_DIALOG", "1")


_BIRD_ENV_VARS = (
    "PICTUREPICK_ARK_API_KEY",
    "ARK_API_KEY",
    "PICTUREPICK_ARK_ENDPOINT",
    "PICTUREPICK_ARK_BASE_URL",
    "PICTUREPICK_ARK_MODEL",
    "PICTUREPICK_BIRD_MOCK",
    "PICTUREPICK_BIRD_MOCK_DELAY",
    "PICTUREPICK_BIRD_MOCK_FAIL",
)


@pytest.fixture(autouse=True)
def isolate_bird_config(tmp_path, monkeypatch):
    """AI 识鸟配置隔离：不读真实用户配置、不受外部环境变量影响。"""
    monkeypatch.setenv("PICTUREPICK_CONFIG_DIR", str(tmp_path / "picturepick-config"))
    for name in _BIRD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_bird(monkeypatch):
    """启用模拟识鸟（不访问网络，任务几乎立即完成）。"""
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK", "1")
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_DELAY", "0")
    return monkeypatch


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """6 张照片（3 顶层 + 3 子目录，大小写混合）+ 3 个干扰文件。"""
    root = tmp_path / "photos"
    for index, relative in enumerate(LIBRARY_FILES):
        make_jpeg(root / relative, color=(30 + index * 25, 80, 140))
    (root / "note.txt").write_text("not a photo", encoding="utf-8")
    Image.new("RGB", (20, 20), (10, 200, 10)).save(root / "skip.png", format="PNG")
    (root / "sub" / "readme.md").write_text("skip me", encoding="utf-8")
    return root


@pytest.fixture
def other_library(tmp_path: Path) -> Path:
    root = tmp_path / "other-photos"
    make_jpeg(root / "x.jpg", color=(200, 10, 10))
    make_jpeg(root / "nested" / "y.jpeg", color=(10, 10, 200))
    return root


@pytest.fixture
def app(tmp_path: Path):
    return create_app(webui_dir=tmp_path / "webui-not-built")


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def app_with_store(tmp_path: Path):
    """创建一个使用自定义 TTL 的 store 的 app（用于会话过期测试）。"""
    from datetime import timedelta

    def _factory(ttl: timedelta):
        return create_app(
            webui_dir=tmp_path / "webui-not-built",
            store=SessionStore(ttl=ttl),
        )

    return _factory
