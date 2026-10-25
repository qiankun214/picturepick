"""轮次状态机测试：rounds/next、pick 数量门槛、历史计数。"""

from __future__ import annotations

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


def _photos(client, session_id, index=1):
    return photos_by_name(list_round_photos(client, session_id, index))


def test_next_round_requires_at_least_two_picks(client, library):
    session = create_session(client, library)
    sid = session["id"]

    response = next_round(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "NOT_ENOUGH_PICKS"

    pick(client, sid, _photos(client, sid)["a.jpg"]["id"])
    response = next_round(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "NOT_ENOUGH_PICKS"
    # 失败不应改变轮次状态
    assert get_summary(client, sid)["roundIndex"] == 1


def test_next_round_resets_counts_and_keeps_history(client, library):
    session = create_session(client, library)
    sid = session["id"]
    round1 = _photos(client, sid)
    first = round1["a.jpg"]["id"]
    second = round1["b.JPG"]["id"]

    view(client, sid, first)
    pick(client, sid, first)
    pick(client, sid, second)

    response = next_round(client, sid)
    assert response.status_code == 200
    summary = response.json()
    assert summary["roundIndex"] == 2
    assert summary["roundCount"] == 2
    assert summary["roundPhotoCount"] == 2
    assert summary["libraryTotal"] == 6
    assert summary["viewedCount"] == 0
    assert summary["pickCount"] == 0
    assert summary["phase"] == "picking"
    assert summary["rounds"] == [
        {"index": 1, "photoCount": 6, "viewedCount": 1, "pickedCount": 2},
        {"index": 2, "photoCount": 2, "viewedCount": 0, "pickedCount": 0},
    ]

    round2 = list_round_photos(client, sid, 2)
    assert round2["total"] == 2
    assert round2["photos"][0]["id"] == first
    assert round2["photos"][1]["id"] == second
    for photo in round2["photos"]:
        assert photo["picked"] is False
        assert photo["viewed"] is False
        # 历史累计保留
        assert photo["pickCount"] == 1

    # 新一轮浏览/ pick 只影响本轮计数
    assert view(client, sid, first)["viewedCount"] == 1
    assert pick(client, sid, first)["pickCount"] == 1
    assert get_summary(client, sid)["rounds"][1]["viewedCount"] == 1
    listed = _photos(client, sid, 2)
    assert listed["a.jpg"]["pickCount"] == 2  # 第 1 轮 + 第 2 轮
    assert listed["a.jpg"]["picked"] is True
    assert listed["b.JPG"]["pickCount"] == 1
    assert listed["b.JPG"]["picked"] is False


def test_multiple_rounds_until_finish(client, library):
    session = create_session(client, library)
    sid = session["id"]
    round1 = _photos(client, sid)
    first = round1["a.jpg"]["id"]
    second = round1["b.JPG"]["id"]

    pick(client, sid, first)
    pick(client, sid, second)
    assert next_round(client, sid).json()["roundIndex"] == 2

    round2 = _photos(client, sid, 2)
    pick(client, sid, round2["a.jpg"]["id"])
    pick(client, sid, round2["b.JPG"]["id"])
    assert next_round(client, sid).json()["roundIndex"] == 3

    round3 = list_round_photos(client, sid, 3)
    assert round3["total"] == 2
    pick(client, sid, round3["photos"][0]["id"])

    response = finish(client, sid)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["roundsPlayed"] == 3
    by_id = {photo["id"]: photo for photo in result["photos"]}
    assert by_id[first]["pickCount"] == 3
    assert by_id[first]["pickedInRounds"] == [1, 2, 3]
    assert by_id[second]["pickCount"] == 2
    assert by_id[second]["pickedInRounds"] == [1, 2]
    assert get_summary(client, sid)["phase"] == "finished"

    response = next_round(client, sid)
    assert response.status_code == 409
    assert error_code(response) == "PHASE_FINISHED"
