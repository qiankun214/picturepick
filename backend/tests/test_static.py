"""SPA 静态资源与 API 路由优先级测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from picturepick.app import create_app, default_webui_dir


@pytest.fixture
def webui_app(tmp_path):
    webui = tmp_path / "webui"
    (webui / "assets").mkdir(parents=True)
    (webui / "index.html").write_text("<html><body>PICTUREPICK_INDEX</body></html>", encoding="utf-8")
    (webui / "app.js").write_text("console.log('root-app');", encoding="utf-8")
    (webui / "assets" / "main.js").write_text("console.log('asset-app');", encoding="utf-8")
    # 与 webui 同级放置的机密文件，用于验证路径穿越不可读取
    (tmp_path / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
    return create_app(webui_dir=webui)


def test_index_and_static_files(webui_app):
    with TestClient(webui_app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "PICTUREPICK_INDEX" in response.text

        response = client.get("/app.js")
        assert response.status_code == 200
        assert "root-app" in response.text

        response = client.get("/assets/main.js")
        assert response.status_code == 200
        assert "asset-app" in response.text


def test_spa_fallback_for_unknown_paths(webui_app):
    with TestClient(webui_app) as client:
        for path in ("/viewer", "/deep/spa/route", "/some.css"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "PICTUREPICK_INDEX" in response.text


def test_api_routes_take_priority_over_spa(webui_app):
    with TestClient(webui_app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "PICTUREPICK_INDEX" not in response.text

        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["detail"]["code"] == "NOT_FOUND"
        assert "PICTUREPICK_INDEX" not in response.text


def test_path_traversal_does_not_escape_webui(webui_app):
    with TestClient(webui_app) as client:
        response = client.get("/..%2Fsecret.txt")
        assert response.status_code in (200, 404)
        assert "TOPSECRET" not in response.text


def test_missing_webui_returns_json_hint(tmp_path):
    app = create_app(webui_dir=tmp_path / "not-built")
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        payload = response.json()
        assert payload["status"] == "ok"
        assert "not been built" in payload["message"]

        response = client.get("/anything")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_default_webui_dir_points_inside_package():
    path = default_webui_dir()
    assert path.name == "webui"
    assert "picturepick" in str(path).lower()


def test_meipass_webui_location(monkeypatch, tmp_path):
    """PyInstaller 打包后 webui 必须在 sys._MEIPASS/webui 下。"""
    import sys

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert default_webui_dir() == tmp_path / "webui"

    (tmp_path / "webui").mkdir()
    (tmp_path / "webui" / "index.html").write_text("MEIPASS_UI", encoding="utf-8")
    app = create_app()
    with TestClient(app) as client:
        assert "MEIPASS_UI" in client.get("/").text
