"""resume 继续筛选测试（docs/API.md 第 3 版 §4 POST /api/sessions/{sid}/resume）。"""

from __future__ import annotations

import pytest

from helpers import (
    create_session,
    error_code,
    finish,
    get_summary,
    list_round_photos,
    next_round,
    pick,
    photos_by_name,
    view,
)


def _resume(client, session_id):
    return client.post(f"/api/sessions/{session_id}/resume")


def _photos(client, session_id, index=1):
    return photos_by_name(list_round_photos(client, session_id, index)["photos"])


def _finished_two_round_session(client, library, picks_in_round2=2):
    """两轮后 finished：第 1 轮 pick a/b，第 2 轮 pick 前 N 张。"""

    session = create_session(client, library)
    sid = session["id"]
    round1 = _photos(client, sid)
    pick(client, sid, round1["a.jpg"]["id"])
    pick(client, sid, round1["b.JPG"]["id"])
    assert next_round(client, sid).status_code == 200
    round2 = list_round_photos(client, sid, 2)["photos"]
    assert [photo["name"] for photo in round2] == ["a.jpg", "b.JPG"]
    for photo in round2[:picks_in_round2]:
        pick(client, sid, photo["id"])
    assert finish(client, sid).status_code == 200
    return sid


# ---- 正常 resume -----------------------------------------------------------


def test_resume_creates_new_round_and_returns_to_picking(client, library):
    sid = _finished_two_round_session(client, library)
    before = get_summary(client, sid)
    assert before["phase"] == "finished"
    assert before["roundIndex"] == 2
    assert before["restored"] is False

    response = _resume(client, sid)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["phase"] == "picking"
    assert summary["roundIndex"] == 3
    assert summary["roundCount"] == 3
    assert summary["roundPhotoCount"] == 2
    assert summary["viewedCount"] == 0
    assert summary["pickCount"] == 0
    assert summary["rounds"] == [
        {"index": 1, "photoCount": 6, "viewedCount": 0, "pickedCount": 2},
        {"index": 2, "photoCount": 2, "viewedCount": 0, "pickedCount": 2},
        {"index": 3, "photoCount": 2, "viewedCount": 0, "pickedCount": 0},
    ]

    round3 = list_round_photos(client, sid, 3)["photos"]
    assert [photo["name"] for photo in round3] == ["a.jpg", "b.JPG"]
    assert [photo["pickCount"] for photo in round3] == [2, 2]
    assert all(photo["picked"] is False and photo["viewed"] is False for photo in round3)


def test_resume_then_pick_and_finish_again(client, library):
    sid = _finished_two_round_session(client, library)
    assert _resume(client, sid).status_code == 200

    round3 = photos_by_name(list_round_photos(client, sid, 3)["photos"])
    first = round3["a.jpg"]["id"]
    assert view(client, sid, first)["viewedCount"] == 1
    assert pick(client, sid, first)["pickCount"] == 1
    photos = photos_by_name(list_round_photos(client, sid, 3)["photos"])
    assert photos["a.jpg"]["pickCount"] == 3  # 第 1/2/3 轮累计

    response = finish(client, sid)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["roundsPlayed"] == 3
    by_id = {photo["id"]: photo for photo in result["photos"]}
    assert by_id[first]["pickCount"] == 3
    assert by_id[first]["pickedInRounds"] == [1, 2, 3]
    second = round3["b.JPG"]["id"]
    assert by_id[second]["pickCount"] == 2
    assert by_id[second]["pickedInRounds"] == [1, 2]
    assert result["lastRound"] == {
        "index": 3,
        "pickedCount": 1,
        "pickedPhotoIds": [first],
    }
    # finish 幂等（使用 resume 后重新生成的结果，而不是旧结果）
    assert finish(client, sid).json()["result"] == result


def test_resume_second_time_rejected(client, library):
    sid = _finished_two_round_session(client, library)
    assert _resume(client, sid).status_code == 200

    response = _resume(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "PHASE_PICKING"


def test_resume_on_restored_session(client, library):
    sid = _finished_two_round_session(client, library)
    before = get_summary(client, sid)
    assert before["phase"] == "finished"
    result_json = client.get(f"/api/sessions/{sid}/result").json()
    restored = client.post("/api/sessions/restore", json=result_json).json()
    assert restored["restored"] is True

    response = _resume(client, restored["id"])
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["phase"] == "picking"
    assert summary["roundIndex"] == 3
    assert summary["roundPhotoCount"] == 2
    assert summary["restored"] is True

    round3 = list_round_photos(client, restored["id"], 3)["photos"]
    assert [photo["relativePath"] for photo in round3] == ["a.jpg", "b.JPG"]
    assert [photo["pickCount"] for photo in round3] == [2, 2]


# ---- resume 错误分支 -------------------------------------------------------


def test_resume_picking_session_rejected(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    pick(client, sid, photos["b.JPG"]["id"])

    response = _resume(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "PHASE_PICKING"


def test_resume_finished_with_one_pick_rejected(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    finish(client, sid)

    response = _resume(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "NOT_ENOUGH_PICKS"
    # 失败不应改变会话状态
    summary = get_summary(client, sid)
    assert summary["phase"] == "finished"
    assert summary["roundIndex"] == 1


def test_resume_finished_without_picks_rejected(client, library):
    session = create_session(client, library)
    sid = session["id"]
    finish(client, sid)

    response = _resume(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "NOT_ENOUGH_PICKS"


def test_resume_browser_session_rejected(client):
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
    sid = created.json()["id"]
    photos = _photos(client, sid)
    pick(client, sid, photos["a.jpg"]["id"])
    pick(client, sid, photos["b.jpg"]["id"])
    finish(client, sid)

    response = _resume(client, sid)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_SOURCE"


def test_resume_unknown_session(client):
    response = _resume(client, "no-such-session")
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"
