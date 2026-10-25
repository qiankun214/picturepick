"""最终结果 JSON、下载与导出测试。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

import pytest

from helpers import (
    create_session,
    error_code,
    finish,
    get_summary,
    list_round_photos,
    make_jpeg,
    next_round,
    pick,
    photos_by_name,
)


def _prepare_two_round_session(client, library):
    session = create_session(client, library)
    sid = session["id"]
    round1 = photos_by_name(list_round_photos(client, sid))
    pick(client, sid, round1["a.jpg"]["id"])
    pick(client, sid, round1["b.JPG"]["id"])
    assert next_round(client, sid).status_code == 200
    round2 = photos_by_name(list_round_photos(client, sid, 2))
    pick(client, sid, round2["a.jpg"]["id"])
    return sid, round1, round2


def test_result_before_finish_is_allowed(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photo = photos_by_name(list_round_photos(client, sid))["a.jpg"]
    pick(client, sid, photo["id"])

    response = client.get(f"/api/sessions/{sid}/result")
    assert response.status_code == 200
    result = response.json()
    assert result["schemaVersion"] == 1
    assert result["totalPhotos"] == 6
    assert result["roundsPlayed"] == 1
    assert result["pickedPhotoCount"] == 1
    # 未 finish 不应改变 phase
    assert get_summary(client, sid)["phase"] == "picking"


def test_finish_result_structure(client, library):
    sid, round1, _round2 = _prepare_two_round_session(client, library)

    response = finish(client, sid)
    assert response.status_code == 200
    payload = response.json()
    assert payload["savedPath"] is None
    result = payload["result"]

    assert result["schemaVersion"] == 1
    assert datetime.fromisoformat(result["generatedAt"]).tzinfo is not None
    assert os.path.normcase(result["sourceDirectory"]) == os.path.normcase(os.path.abspath(str(library)))
    assert result["recursive"] is True
    assert result["totalPhotos"] == 6
    assert result["roundsPlayed"] == 2
    assert result["pickedPhotoCount"] == 2
    assert len(result["photos"]) == 6

    assert get_summary(client, sid)["phase"] == "finished"

    for photo in result["photos"]:
        assert set(photo) == {
            "id",
            "name",
            "relativePath",
            "absolutePath",
            "pickCount",
            "pickedInRounds",
            "bird",
        }
        assert os.path.isabs(photo["absolutePath"])

    by_id = {photo["id"]: photo for photo in result["photos"]}
    first = by_id[round1["a.jpg"]["id"]]
    second = by_id[round1["b.JPG"]["id"]]
    assert first["pickCount"] == 2 and first["pickedInRounds"] == [1, 2]
    assert second["pickCount"] == 1 and second["pickedInRounds"] == [1]
    assert by_id[round1["c.jpeg"]["id"]]["pickCount"] == 0
    assert by_id[round1["c.jpeg"]["id"]]["pickedInRounds"] == []

    counts = [photo["pickCount"] for photo in result["photos"]]
    assert counts == sorted(counts, reverse=True)


def test_result_sorting_count_desc_then_relative_path_asc(client, tmp_path):
    root = tmp_path / "sort-photos"
    for name in ("b.jpg", "a.jpg", "c.jpg", "d.jpg"):
        make_jpeg(root / name)
    session = create_session(client, root)
    sid = session["id"]

    round1 = photos_by_name(list_round_photos(client, sid))
    pick(client, sid, round1["b.jpg"]["id"])
    pick(client, sid, round1["a.jpg"]["id"])
    assert next_round(client, sid).status_code == 200
    round2 = photos_by_name(list_round_photos(client, sid, 2))
    pick(client, sid, round2["a.jpg"]["id"])

    result = finish(client, sid).json()["result"]
    assert [photo["relativePath"] for photo in result["photos"]] == [
        "a.jpg",
        "b.jpg",
        "c.jpg",
        "d.jpg",
    ]
    assert [photo["pickCount"] for photo in result["photos"]] == [2, 1, 0, 0]


def test_finish_is_idempotent(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = photos_by_name(list_round_photos(client, sid))
    pick(client, sid, photos["a.jpg"]["id"])

    first = finish(client, sid).json()
    second = finish(client, sid).json()
    assert first == second
    assert first["result"]["generatedAt"] == second["result"]["generatedAt"]
    # GET result 与 finish 结果一致
    assert client.get(f"/api/sessions/{sid}/result").json() == first["result"]


def test_finish_without_picks(client, library):
    session = create_session(client, library)
    result = finish(client, session["id"]).json()["result"]
    assert result["pickedPhotoCount"] == 0
    assert result["roundsPlayed"] == 1
    assert [photo["pickCount"] for photo in result["photos"]] == [0] * 6
    assert [photo["relativePath"] for photo in result["photos"]] == sorted(
        photo["relativePath"] for photo in result["photos"]
    )


def test_download_attachment(client, library):
    sid, _round1, _round2 = _prepare_two_round_session(client, library)

    response = client.get(f"/api/sessions/{sid}/result", params={"download": 1})
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert re.search(r'filename="picturepick-photos-\d{8}-\d{6}\.json"', disposition), disposition
    assert "filename*=UTF-8''" in disposition

    downloaded = response.json()
    assert downloaded["schemaVersion"] == 1
    assert downloaded["totalPhotos"] == 6
    # 与普通 GET 结果一致
    assert downloaded == client.get(f"/api/sessions/{sid}/result").json()

    # download=true / download=yes 等价
    for value in ("true", "yes"):
        response = client.get(f"/api/sessions/{sid}/result", params={"download": value})
        assert "attachment" in response.headers["content-disposition"]


def test_download_filename_sanitizes_root(tmp_path):
    from picturepick.export import download_filename, root_label

    assert root_label("E:\\photos") == "photos"
    assert root_label("E:\\") == "E"
    name = download_filename("E:\\photos")
    assert re.fullmatch(r"picturepick-photos-\d{8}-\d{6}\.json", name)


def test_export_writes_file(client, library, tmp_path):
    sid, _round1, _round2 = _prepare_two_round_session(client, library)
    finish(client, sid)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "pick-result.json"
    response = client.post(f"/api/sessions/{sid}/export", json={"path": str(target)})
    assert response.status_code == 200, response.text
    assert os.path.normcase(response.json()["path"]) == os.path.normcase(str(target))
    assert target.is_file()

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == client.get(f"/api/sessions/{sid}/result").json()
    assert payload["totalPhotos"] == 6


def test_export_failures(client, library, tmp_path):
    session = create_session(client, library)
    sid = session["id"]

    # 空路径
    response = client.post(f"/api/sessions/{sid}/export", json={"path": ""})
    assert response.status_code == 400
    assert error_code(response) == "WRITE_FAILED"

    # 父路径是文件 → 无法写入
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    response = client.post(
        f"/api/sessions/{sid}/export",
        json={"path": str(blocker / "result.json")},
    )
    assert response.status_code == 400
    assert error_code(response) == "WRITE_FAILED"

    # 父目录不存在
    response = client.post(
        f"/api/sessions/{sid}/export",
        json={"path": str(tmp_path / "no" / "such" / "dir" / "result.json")},
    )
    assert response.status_code == 400
    assert error_code(response) == "WRITE_FAILED"

    # 未知会话
    response = client.post(
        "/api/sessions/nope/export",
        json={"path": str(tmp_path / "r.json")},
    )
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_result_unknown_session(client):
    response = client.get("/api/sessions/nope/result")
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"
