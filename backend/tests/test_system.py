"""系统接口测试：健康检查、盘符、目录浏览、目录对话框。"""

from __future__ import annotations

import os

import pytest

from picturepick import fsbrowse

from helpers import error_code


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "name": "picturepick", "version": "1.0.0"}


def test_fs_roots(client):
    response = client.get("/api/fs/roots")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"home", "startPath", "roots"}
    assert isinstance(payload["home"], str) and payload["home"]
    assert isinstance(payload["startPath"], str) and payload["startPath"]
    assert payload["roots"], "至少应返回一个盘符/根目录"
    for root in payload["roots"]:
        assert root["name"] == root["path"]
        assert os.path.isabs(root["path"])
    if os.name == "nt":
        assert any(root["path"].endswith(":\\") for root in payload["roots"])


def test_fs_list_only_directories_sorted_case_insensitively(client, tmp_path):
    parent = tmp_path / "browse"
    for name in ("beta", "Alpha", "gamma"):
        (parent / name).mkdir(parents=True)
    (parent / "file.txt").write_text("x", encoding="utf-8")

    response = client.get("/api/fs/list", params={"path": str(parent)})
    assert response.status_code == 200
    payload = response.json()
    assert os.path.normcase(payload["path"]) == os.path.normcase(os.path.abspath(str(parent)))
    assert [entry["name"] for entry in payload["entries"]] == ["Alpha", "beta", "gamma"]
    assert all(entry["isDir"] is True for entry in payload["entries"])
    assert all(os.path.isabs(entry["path"]) for entry in payload["entries"])
    assert os.path.normcase(payload["parent"]) == os.path.normcase(str(tmp_path))


def test_fs_list_errors(client, tmp_path):
    missing = tmp_path / "no-such-dir"
    response = client.get("/api/fs/list", params={"path": str(missing)})
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"

    file_path = tmp_path / "plain.txt"
    file_path.write_text("x", encoding="utf-8")
    response = client.get("/api/fs/list", params={"path": str(file_path)})
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"

    # 缺少 path 参数
    response = client.get("/api/fs/list")
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"


def test_dialog_unavailable_in_headless_tests(client):
    response = client.post("/api/dialog/pick-directory")
    assert response.status_code == 501
    assert error_code(response) == "DIALOG_UNAVAILABLE"


def test_dialog_result_cancel_and_selection(client, monkeypatch, tmp_path):
    monkeypatch.setattr(fsbrowse, "pick_directory", lambda: None)
    response = client.post("/api/dialog/pick-directory")
    assert response.status_code == 200
    assert response.json() == {"path": None, "cancelled": True}

    monkeypatch.setattr(fsbrowse, "pick_directory", lambda: str(tmp_path))
    response = client.post("/api/dialog/pick-directory")
    assert response.status_code == 200
    assert response.json() == {"path": str(tmp_path), "cancelled": False}


def test_dialog_disabled_env(monkeypatch):
    monkeypatch.setenv("PICTUREPICK_DISABLE_DIALOG", "1")
    assert fsbrowse.dialog_disabled() is True
    monkeypatch.setenv("PICTUREPICK_DISABLE_DIALOG", "0")
    assert fsbrowse.dialog_disabled() is False
