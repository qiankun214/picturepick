"""restore 恢复现场测试（docs/API.md 第 3 版 §4 POST /api/sessions/restore）。"""

from __future__ import annotations

import copy
import json
import os

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
    view,
)

RESTORE = "/api/sessions/restore"


def _photos(client, session_id, index=1):
    return photos_by_name(list_round_photos(client, session_id, index)["photos"])


def _restore(client, payload):
    return client.post(RESTORE, json=payload)


def _make_two_round_result(client, library):
    """两轮流程：第 1 轮 pick a/d；第 2 轮 pick a；finish 后返回结果 JSON。"""
    session = create_session(client, library)
    sid = session["id"]
    round1 = _photos(client, sid)
    pick(client, sid, round1["a.jpg"]["id"])
    pick(client, sid, round1["d.jpg"]["id"])
    assert next_round(client, sid).status_code == 200
    round2 = list_round_photos(client, sid, 2)["photos"]
    assert [photo["name"] for photo in round2] == ["a.jpg", "d.jpg"]
    pick(client, sid, round2[0]["id"])
    result = finish(client, sid).json()["result"]
    return sid, result


def _result_without_generated_at(result):
    clone = copy.deepcopy(result)
    clone.pop("generatedAt", None)
    return clone


# ---- 正常恢复 --------------------------------------------------------------


def test_restore_rebuilds_finished_session(client, library):
    original_sid, result = _make_two_round_result(client, library)

    response = _restore(client, result)
    assert response.status_code == 201, response.text
    summary = response.json()

    sid = summary["id"]
    assert sid != original_sid
    assert summary["restored"] is True
    assert summary["origin"] == "server"
    assert summary["phase"] == "finished"
    assert summary["roundIndex"] == 2
    assert summary["roundCount"] == 2
    assert os.path.normcase(summary["root"]) == os.path.normcase(os.path.abspath(str(library)))
    assert os.path.normcase(summary["sourceLabel"]) == os.path.normcase(summary["root"])
    assert summary["libraryTotal"] == 6
    assert summary["roundPhotoCount"] == 2
    assert summary["viewedCount"] == 0
    assert summary["pickCount"] == 1
    assert summary["rounds"] == [
        {"index": 1, "photoCount": 6, "viewedCount": 0, "pickedCount": 2},
        {"index": 2, "photoCount": 2, "viewedCount": 0, "pickedCount": 1},
    ]

    # 恢复的会话成为 current session，GET 也返回 restored=true
    assert client.get("/api/sessions/current").json()["session"]["id"] == sid
    assert get_summary(client, sid)["restored"] is True

    # 恢复后的结果与保存的 JSON 一致（generatedAt 除外）
    restored_result = client.get(f"/api/sessions/{sid}/result").json()
    assert _result_without_generated_at(restored_result) == _result_without_generated_at(result)
    assert restored_result["lastRound"] == result["lastRound"]
    assert [photo["pickCount"] for photo in restored_result["photos"]] == [
        photo["pickCount"] for photo in result["photos"]
    ]
    assert [photo["pickedInRounds"] for photo in restored_result["photos"]] == [
        photo["pickedInRounds"] for photo in result["photos"]
    ]

    # 轮次照片顺序与计数
    round2 = list_round_photos(client, sid, 2)["photos"]
    assert [photo["relativePath"] for photo in round2] == ["a.jpg", "sub/d.jpg"]
    assert [photo["pickCount"] for photo in round2] == [2, 1]
    assert all(photo["viewed"] is False for photo in round2)


def test_restored_session_can_download_and_export(client, library, tmp_path):
    _original_sid, result = _make_two_round_result(client, library)
    summary = _restore(client, result).json()
    sid = summary["id"]

    download = client.get(f"/api/sessions/{sid}/result", params={"download": 1})
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.json() == client.get(f"/api/sessions/{sid}/result").json()

    destination = tmp_path / "restore-export"
    destination.mkdir()
    exported = client.post(
        f"/api/sessions/{sid}/export-photos",
        json={"destination": str(destination)},
    )
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload["copied"] == 1
    assert [item["relativePath"] for item in payload["files"]] == ["a.jpg"]
    assert (destination / "a.jpg").is_file()


def test_restore_ignores_new_files_but_keeps_lock_counts(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    make_jpeg(library / "sub" / "new-photo.jpg", color=(1, 2, 3))

    summary = _restore(client, result).json()
    sid = summary["id"]
    assert summary["libraryTotal"] == 7
    assert summary["rounds"][0]["photoCount"] == 7
    assert summary["rounds"][1]["photoCount"] == 2

    restored_result = client.get(f"/api/sessions/{sid}/result").json()
    assert restored_result["totalPhotos"] == 7
    by_relative = {photo["relativePath"]: photo for photo in restored_result["photos"]}
    assert by_relative["sub/new-photo.jpg"]["pickCount"] == 0
    assert by_relative["sub/new-photo.jpg"]["pickedInRounds"] == []
    assert by_relative["a.jpg"]["pickCount"] == 2
    assert restored_result["lastRound"]["pickedPhotoIds"] == result["lastRound"]["pickedPhotoIds"]


def test_restore_tolerates_deleted_unpicked_file(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    (library / "b.JPG").unlink()

    response = _restore(client, result)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["libraryTotal"] == 5
    assert summary["rounds"][0]["photoCount"] == 5
    assert summary["rounds"][1]["photoCount"] == 2
    result_after = client.get(f"/api/sessions/{summary['id']}/result").json()
    assert result_after["totalPhotos"] == 5
    assert "b.JPG" not in {photo["relativePath"] for photo in result_after["photos"]}


def test_restore_tolerates_deleted_picked_file(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    (library / "sub" / "d.jpg").unlink()  # 第 1 轮被 pick、第 2 轮保留的照片

    response = _restore(client, result)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["libraryTotal"] == 5
    assert summary["roundIndex"] == 2
    # 第 2 轮只剩仍未删除的 a.jpg
    assert summary["rounds"][1]["photoCount"] == 1
    result_after = client.get(f"/api/sessions/{summary['id']}/result").json()
    assert [photo["relativePath"] for photo in result_after["photos"] if photo["pickCount"] > 0] == ["a.jpg"]


def test_restore_last_round_fallback_without_last_round_field(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    payload = copy.deepcopy(result)
    payload.pop("lastRound", None)

    summary = _restore(client, payload).json()
    sid = summary["id"]
    restored_result = client.get(f"/api/sessions/{sid}/result").json()
    assert restored_result["lastRound"]["index"] == 2
    assert restored_result["lastRound"]["pickedCount"] == 1
    assert restored_result["lastRound"]["pickedPhotoIds"] == result["lastRound"]["pickedPhotoIds"]


def test_restore_single_round_result(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    pick(client, sid, photos["b.JPG"]["id"])
    result = finish(client, sid).json()["result"]

    summary = _restore(client, result).json()
    assert summary["roundIndex"] == 1
    assert summary["roundCount"] == 1
    assert summary["roundPhotoCount"] == 6
    assert summary["pickCount"] == 2
    assert summary["rounds"] == [{"index": 1, "photoCount": 6, "viewedCount": 0, "pickedCount": 2}]
    restored_result = client.get(f"/api/sessions/{summary['id']}/result").json()
    assert restored_result["lastRound"]["pickedCount"] == 2


# ---- 错误码 ----------------------------------------------------------------


def test_restore_unsupported_schema(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    for schema_version in (2, 0, None, "1"):
        payload = copy.deepcopy(result)
        payload["schemaVersion"] = schema_version
        response = _restore(client, payload)
        assert response.status_code == 400, (schema_version, response.text)
        assert error_code(response) == "UNSUPPORTED_SCHEMA"


def test_restore_browser_origin_rejected(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    payload = copy.deepcopy(result)
    payload["origin"] = "browser"
    response = _restore(client, payload)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_SOURCE"


def test_restore_invalid_path(client, library, tmp_path):
    _original_sid, result = _make_two_round_result(client, library)
    for source in (str(tmp_path / "no-such-dir"), "", None):
        payload = copy.deepcopy(result)
        payload["sourceDirectory"] = source
        response = _restore(client, payload)
        assert response.status_code == 400, (source, response.text)
        assert error_code(response) == "INVALID_PATH"


def test_restore_no_photos_in_directory(client, library, tmp_path):
    _original_sid, result = _make_two_round_result(client, library)
    empty = tmp_path / "empty"
    empty.mkdir()
    payload = copy.deepcopy(result)
    payload["sourceDirectory"] = str(empty)
    response = _restore(client, payload)
    assert response.status_code == 400
    assert error_code(response) == "NO_PHOTOS"


def test_restore_no_matching_photos(client, library):
    """目录里还有新照片，但 JSON 里的照片全部被删除 → NO_PHOTOS。"""
    _original_sid, result = _make_two_round_result(client, library)
    for photo in sorted(library.rglob("*")):
        if photo.is_file() and photo.suffix.lower() in {".jpg", ".jpeg"}:
            photo.unlink()
    make_jpeg(library / "brand-new.jpg")
    response = _restore(client, result)
    assert response.status_code == 400
    assert error_code(response) == "NO_PHOTOS"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda payload: payload.update(photos="nope"), id="photos-not-list"),
        pytest.param(lambda payload: payload["photos"].append("x"), id="photo-not-object"),
        pytest.param(lambda payload: payload["photos"][0].pop("relativePath"), id="missing-relativePath"),
        pytest.param(lambda payload: payload["photos"][0].update(relativePath=""), id="empty-relativePath"),
        pytest.param(
            lambda payload: payload["photos"][0].update(pickedInRounds="round-1"),
            id="pickedInRounds-not-list",
        ),
        pytest.param(
            lambda payload: payload["photos"][0].update(pickedInRounds=[0]),
            id="pickedInRounds-zero",
        ),
        pytest.param(
            lambda payload: payload["photos"][0].update(pickedInRounds=[99]),
            id="pickedInRounds-out-of-range",
        ),
        pytest.param(
            lambda payload: payload["photos"][0].update(pickedInRounds=[2, 1]),
            id="pickedInRounds-not-sorted",
        ),
        pytest.param(
            lambda payload: payload["photos"][0].update(pickedInRounds=[1, 1]),
            id="pickedInRounds-duplicate",
        ),
        pytest.param(lambda payload: payload.update(roundsPlayed=0), id="roundsPlayed-zero"),
        pytest.param(lambda payload: payload.pop("roundsPlayed"), id="missing-roundsPlayed"),
        pytest.param(
            lambda payload: payload["photos"][0].update(pickCount="many"),
            id="invalid-pickCount",
        ),
        pytest.param(lambda payload: payload.update(lastRound=[]), id="lastRound-not-object"),
        pytest.param(
            lambda payload: payload.update(lastRound={"pickedPhotoIds": [1, 2]}),
            id="lastRound-ids-not-str",
        ),
    ],
)
def test_restore_invalid_structure(client, library, mutate):
    _original_sid, result = _make_two_round_result(client, library)
    payload = copy.deepcopy(result)
    mutate(payload)
    response = _restore(client, payload)
    assert response.status_code == 400, response.text
    assert error_code(response) == "INVALID_RESTORE"


def test_restore_duplicate_relative_paths(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    payload = copy.deepcopy(result)
    payload["photos"].append(copy.deepcopy(payload["photos"][0]))
    response = _restore(client, payload)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_RESTORE"


def test_restore_rounds_played_over_limit(client, library):
    _original_sid, result = _make_two_round_result(client, library)
    payload = copy.deepcopy(result)
    payload["roundsPlayed"] = 5000
    response = _restore(client, payload)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_RESTORE"


def test_restore_requires_json_object(client):
    response = client.post(RESTORE, json=[1, 2, 3])
    # 顶层不是对象：FastAPI 请求体校验失败（422），不进入 restore 业务校验
    assert response.status_code == 422


def test_normal_session_restored_false(client, library):
    session = create_session(client, library)
    assert session["restored"] is False
    assert get_summary(client, session["id"])["restored"] is False
