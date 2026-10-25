"""会话创建 / 查询 / 过期测试。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from helpers import create_session, error_code, get_summary
from conftest import LIBRARY_COUNT_FLAT, LIBRARY_COUNT_RECURSIVE


def test_current_session_is_null_before_any_create(client):
    response = client.get("/api/sessions/current")
    assert response.status_code == 200
    assert response.json() == {"session": None}


def test_create_session_returns_summary(client, library):
    response = client.post("/api/sessions", json={"directory": str(library), "recursive": True})
    assert response.status_code == 201, response.text
    session = response.json()

    assert re.fullmatch(r"[0-9a-f]{32}", session["id"])
    assert os.path.normcase(session["root"]) == os.path.normcase(os.path.abspath(str(library)))
    assert session["recursive"] is True
    assert session["phase"] == "picking"
    assert session["roundIndex"] == 1
    assert session["roundCount"] == 1
    assert session["libraryTotal"] == LIBRARY_COUNT_RECURSIVE
    assert session["roundPhotoCount"] == LIBRARY_COUNT_RECURSIVE
    assert session["viewedCount"] == 0
    assert session["pickCount"] == 0
    assert session["rounds"] == [
        {"index": 1, "photoCount": LIBRARY_COUNT_RECURSIVE, "viewedCount": 0, "pickedCount": 0}
    ]
    created_at = datetime.fromisoformat(session["createdAt"])
    assert created_at.tzinfo is not None


def test_create_session_non_recursive(client, library):
    session = create_session(client, library, recursive=False)
    assert session["recursive"] is False
    assert session["libraryTotal"] == LIBRARY_COUNT_FLAT
    assert session["roundPhotoCount"] == LIBRARY_COUNT_FLAT


def test_current_session_points_to_latest(client, library, other_library):
    first = create_session(client, library)
    response = client.get("/api/sessions/current")
    assert response.status_code == 200
    assert response.json()["session"]["id"] == first["id"]

    second = create_session(client, other_library)
    response = client.get("/api/sessions/current")
    assert response.json()["session"]["id"] == second["id"]


def test_get_session_by_id_matches_create_response(client, library):
    session = create_session(client, library)
    response = client.get(f"/api/sessions/{session['id']}")
    assert response.status_code == 200
    assert response.json() == session


def test_unknown_session_errors(client):
    response = client.get("/api/sessions/does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["detail"]["code"] == "SESSION_NOT_FOUND"
    assert payload["detail"]["message"]

    response = client.get("/api/sessions/does-not-exist/rounds/1/photos")
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_create_session_invalid_directory(client, tmp_path):
    response = client.post("/api/sessions", json={"directory": str(tmp_path / "missing")})
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"


def test_create_session_no_photos(client, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    response = client.post("/api/sessions", json={"directory": str(empty)})
    assert response.status_code == 400
    assert error_code(response) == "NO_PHOTOS"


def test_create_session_empty_directory_string(client):
    response = client.post("/api/sessions", json={"directory": "  "})
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"


def test_expired_session_is_not_current_and_404(app_with_store, library):
    app = app_with_store(timedelta(seconds=-1))
    with TestClient(app) as expired_client:
        created = expired_client.post("/api/sessions", json={"directory": str(library)})
        assert created.status_code == 201
        session_id = created.json()["id"]

        assert expired_client.get("/api/sessions/current").json() == {"session": None}
        response = expired_client.get(f"/api/sessions/{session_id}")
        assert response.status_code == 404
        assert error_code(response) == "SESSION_NOT_FOUND"


def test_valid_session_with_long_ttl(app_with_store, library):
    app = app_with_store(timedelta(hours=24))
    with TestClient(app) as valid_client:
        created = valid_client.post("/api/sessions", json={"directory": str(library)})
        session_id = created.json()["id"]
        assert valid_client.get("/api/sessions/current").json()["session"]["id"] == session_id
        assert get_summary(valid_client, session_id)["id"] == session_id
