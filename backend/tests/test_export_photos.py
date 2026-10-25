"""后端 export-photos 测试（docs/API.md 第 3 版 §4）。

POST /api/sessions/{sid}/export-photos 把最后一轮保留照片由后端复制到目标目录：
仅 server 来源；同名自动 -1/-2 不覆盖；错误码 INVALID_SOURCE / NO_PICKED_PHOTOS /
INVALID_PATH / WRITE_FAILED。
"""

from __future__ import annotations

import os

import pytest

from helpers import (
    create_session,
    error_code,
    finish,
    list_round_photos,
    make_jpeg,
    next_round,
    pick,
    photos_by_name,
)
from picturepick import export as export_module


def _photos(client, session_id, index=1):
    return photos_by_name(list_round_photos(client, session_id, index)["photos"])


def _export(client, session_id, destination):
    return client.post(
        f"/api/sessions/{session_id}/export-photos",
        json={"destination": str(destination)},
    )


def _dir_names(directory):
    return sorted(entry.name for entry in directory.iterdir())


# ---- 正常复制 --------------------------------------------------------------


def test_export_last_round_copies_files_in_round_order(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)

    # pick 顺序与照片顺序相反，验证导出顺序 = 最后一轮照片顺序
    pick(client, sid, photos["d.jpg"]["id"])
    pick(client, sid, photos["a.jpg"]["id"])
    finish(client, sid)

    destination = tmp_path / "导出目录"
    destination.mkdir()
    response = _export(client, sid, destination)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert os.path.normcase(payload["destination"]) == os.path.normcase(str(destination))
    assert payload["copied"] == 2
    assert [item["name"] for item in payload["files"]] == ["a.jpg", "d.jpg"]
    assert [item["relativePath"] for item in payload["files"]] == ["a.jpg", "sub/d.jpg"]
    assert [item["sizeBytes"] for item in payload["files"]] == [
        (library / "a.jpg").stat().st_size,
        (library / "sub/d.jpg").stat().st_size,
    ]

    # 响应 files 与磁盘实际文件一致
    assert _dir_names(destination) == ["a.jpg", "d.jpg"]
    for item in payload["files"]:
        source = library / item["relativePath"]
        target = destination / item["name"]
        assert target.read_bytes() == source.read_bytes()


def test_export_preserves_extensions_case_insensitive(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["b.JPG"]["id"])
    pick(client, sid, photos["c.jpeg"]["id"])
    finish(client, sid)

    destination = tmp_path / "export-ext"
    destination.mkdir()
    payload = _export(client, sid, destination).json()
    assert [item["name"] for item in payload["files"]] == ["b.JPG", "c.jpeg"]
    assert _dir_names(destination) == ["b.JPG", "c.jpeg"]


def test_export_only_last_round_photos(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    round1 = _photos(client, sid)
    pick(client, sid, round1["a.jpg"]["id"])
    pick(client, sid, round1["b.JPG"]["id"])
    pick(client, sid, round1["c.jpeg"]["id"])
    assert next_round(client, sid).status_code == 200

    round2 = _photos(client, sid, 2)
    pick(client, sid, round2["a.jpg"]["id"])
    pick(client, sid, round2["c.jpeg"]["id"])
    finish(client, sid)

    destination = tmp_path / "export-last"
    destination.mkdir()
    payload = _export(client, sid, destination).json()
    assert payload["copied"] == 2
    assert [item["name"] for item in payload["files"]] == ["a.jpg", "c.jpeg"]
    assert _dir_names(destination) == ["a.jpg", "c.jpeg"]


def test_export_without_finish_uses_current_round(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])

    destination = tmp_path / "export-picking"
    destination.mkdir()
    response = _export(client, sid, destination)
    assert response.status_code == 200
    assert response.json()["copied"] == 1
    assert _dir_names(destination) == ["a.jpg"]


# ---- 同名文件不覆盖 --------------------------------------------------------


def test_export_renames_on_name_conflict(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    finish(client, sid)

    destination = tmp_path / "export-conflict"
    destination.mkdir()
    (destination / "a.jpg").write_bytes(b"OLD-CONTENT")
    (destination / "a-1.jpg").write_bytes(b"ALSO-OLD")

    payload = _export(client, sid, destination).json()
    assert payload["copied"] == 1
    assert payload["files"][0]["name"] == "a-2.jpg"
    assert payload["files"][0]["relativePath"] == "a.jpg"
    # 既有文件完全不被覆盖
    assert (destination / "a.jpg").read_bytes() == b"OLD-CONTENT"
    assert (destination / "a-1.jpg").read_bytes() == b"ALSO-OLD"
    assert (destination / "a-2.jpg").read_bytes() == (library / "a.jpg").read_bytes()


def test_export_duplicate_basenames_in_batch_are_renamed(client, tmp_path):
    root = tmp_path / "dup-src"
    make_jpeg(root / "x" / "a.jpg", color=(200, 10, 10))
    make_jpeg(root / "y" / "a.jpg", color=(10, 10, 200))
    session = create_session(client, root)
    sid = session["id"]
    listed = list_round_photos(client, sid)["photos"]
    assert [photo["relativePath"] for photo in listed] == ["x/a.jpg", "y/a.jpg"]
    for photo in listed:  # 两张同名文件都 pick
        pick(client, sid, photo["id"])
    finish(client, sid)

    destination = tmp_path / "dup-out"
    destination.mkdir()
    payload = _export(client, sid, destination).json()
    assert [item["name"] for item in payload["files"]] == ["a.jpg", "a-1.jpg"]
    assert [item["relativePath"] for item in payload["files"]] == ["x/a.jpg", "y/a.jpg"]
    assert (destination / "a.jpg").read_bytes() == (root / "x" / "a.jpg").read_bytes()
    assert (destination / "a-1.jpg").read_bytes() == (root / "y" / "a.jpg").read_bytes()


def test_export_to_source_root_never_overwrites_originals(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    finish(client, sid)

    original_bytes = (library / "a.jpg").read_bytes()
    exported = _export(client, sid, library)
    assert exported.status_code == 200, exported.text
    assert exported.json()["files"][0]["name"] == "a-1.jpg"
    # 原图未被覆盖
    assert (library / "a.jpg").read_bytes() == original_bytes
    assert (library / "a-1.jpg").read_bytes() == original_bytes


# ---- 错误码 ----------------------------------------------------------------


def test_export_destination_errors(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])

    missing = tmp_path / "no-such-dir"
    response = _export(client, sid, missing)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"

    file_path = tmp_path / "plain.txt"
    file_path.write_text("x", encoding="utf-8")
    response = _export(client, sid, file_path)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"

    response = client.post(f"/api/sessions/{sid}/export-photos", json={"destination": "   "})
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PATH"


def test_export_browser_origin_rejected(client, tmp_path):
    created = client.post(
        "/api/sessions",
        json={
            "source": "browser",
            "label": "浏览器相册",
            "photos": [
                {"relativePath": "a.jpg", "sizeBytes": 1},
                {"relativePath": "b.jpg", "sizeBytes": 1},
            ],
        },
    )
    assert created.status_code == 201
    sid = created.json()["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    finish(client, sid)

    destination = tmp_path / "browser-out"
    destination.mkdir()
    response = _export(client, sid, destination)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_SOURCE"
    assert _dir_names(destination) == []


def test_export_no_picked_photos(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    destination = tmp_path / "empty-out"
    destination.mkdir()

    # 选片阶段没有 pick
    response = _export(client, sid, destination)
    assert response.status_code == 400
    assert error_code(response) == "NO_PICKED_PHOTOS"

    # finish 后同样没有保留照片
    finish(client, sid)
    response = _export(client, sid, destination)
    assert response.status_code == 400
    assert error_code(response) == "NO_PICKED_PHOTOS"

    # 无 pick 优先于目标目录校验（无效目录也返回 NO_PICKED_PHOTOS）
    response = _export(client, sid, tmp_path / "still-missing")
    assert response.status_code == 400
    assert error_code(response) == "NO_PICKED_PHOTOS"


def test_export_unknown_session(client, tmp_path):
    destination = tmp_path / "out"
    destination.mkdir()
    response = _export(client, "no-such-session", destination)
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_export_missing_source_file_is_write_failed(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    finish(client, sid)
    (library / "a.jpg").unlink()

    destination = tmp_path / "missing-src-out"
    destination.mkdir()
    response = _export(client, sid, destination)
    assert response.status_code == 400
    assert error_code(response) == "WRITE_FAILED"
    assert _dir_names(destination) == []


def test_export_copy_failure_rolls_back(client, library, tmp_path, monkeypatch):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    pick(client, sid, photos["b.JPG"]["id"])
    finish(client, sid)

    def _boom(_src, _dst):
        raise PermissionError("simulated denial")

    monkeypatch.setattr(export_module.shutil, "copy2", _boom)
    destination = tmp_path / "failed-out"
    destination.mkdir()
    response = _export(client, sid, destination)
    assert response.status_code == 400
    assert error_code(response) == "WRITE_FAILED"
    assert _dir_names(destination) == []


def test_dialog_endpoint_reusable_for_export_destination(client):
    """第 3 版要求导出目标目录复用 /api/dialog/pick-directory（无 GUI → 501）。"""
    response = client.post("/api/dialog/pick-directory")
    assert response.status_code == 501
    assert error_code(response) == "DIALOG_UNAVAILABLE"
