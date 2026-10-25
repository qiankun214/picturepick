"""viewed / pick / unpick 计数与幂等测试。"""

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
    unpick,
    view,
)


def _photos(client, session_id, index=1):
    return photos_by_name(list_round_photos(client, session_id, index))


def test_viewed_marks_current_round_and_is_idempotent(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    first = photos["a.jpg"]["id"]
    second = photos["b.JPG"]["id"]

    summary = view(client, sid, first)
    assert summary["viewedCount"] == 1
    assert summary["rounds"][0]["viewedCount"] == 1
    assert get_summary(client, sid)["viewedCount"] == 1

    # 幂等：重复上报不增加计数
    assert view(client, sid, first)["viewedCount"] == 1
    assert view(client, sid, second)["viewedCount"] == 2

    listed = _photos(client, sid)
    assert listed["a.jpg"]["viewed"] is True
    assert listed["b.JPG"]["viewed"] is True
    assert listed["c.jpeg"]["viewed"] is False


def test_viewed_unknown_photo(client, library):
    session = create_session(client, library)
    response = client.post(f"/api/sessions/{session['id']}/photos/nope/viewed")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"


def test_pick_increments_counts_and_is_idempotent(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    first = photos["a.jpg"]["id"]

    summary = pick(client, sid, first)
    assert summary["pickCount"] == 1
    assert summary["rounds"][0]["pickedCount"] == 1

    # 重复 pick 幂等，不重复计数
    assert pick(client, sid, first)["pickCount"] == 1

    listed = _photos(client, sid)
    assert listed["a.jpg"]["picked"] is True
    assert listed["a.jpg"]["pickCount"] == 1
    assert listed["b.JPG"]["picked"] is False

    # 再 pick 一张
    assert pick(client, sid, photos["b.JPG"]["id"])["pickCount"] == 2


def test_unpick_decrements_and_is_idempotent(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photo_id = _photos(client, sid)["a.jpg"]["id"]

    pick(client, sid, photo_id)
    summary = unpick(client, sid, photo_id)
    assert summary["pickCount"] == 0
    assert summary["rounds"][0]["pickedCount"] == 0
    listed = _photos(client, sid)
    assert listed["a.jpg"]["picked"] is False
    assert listed["a.jpg"]["pickCount"] == 0

    # 未 pick 时删除是幂等的
    assert unpick(client, sid, photo_id)["pickCount"] == 0


def test_pick_unknown_photo(client, library):
    session = create_session(client, library)
    response = client.put(f"/api/sessions/{session['id']}/photos/deadbeefdeadbeef/pick")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"

    response = client.delete(f"/api/sessions/{session['id']}/photos/deadbeefdeadbeef/pick")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"


def test_pick_photo_not_in_current_round(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)

    pick(client, sid, photos["a.jpg"]["id"])
    pick(client, sid, photos["b.JPG"]["id"])
    assert next_round(client, sid).status_code == 200

    # c.jpeg 不在第 2 轮中，pick 应返回 PHOTO_NOT_FOUND
    response = client.put(f"/api/sessions/{sid}/photos/{photos['c.jpeg']['id']}/pick")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"


def test_mutations_rejected_after_finish(client, library):
    session = create_session(client, library)
    sid = session["id"]
    photos = _photos(client, sid)
    first = photos["a.jpg"]["id"]
    pick(client, sid, first)
    pick(client, sid, photos["b.JPG"]["id"])
    assert finish(client, sid).status_code == 200

    for method, path in (
        ("put", f"/api/sessions/{sid}/photos/{first}/pick"),
        ("delete", f"/api/sessions/{sid}/photos/{first}/pick"),
        ("post", f"/api/sessions/{sid}/photos/{first}/viewed"),
        ("post", f"/api/sessions/{sid}/rounds/next"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 409, (method, path, response.text)
        assert error_code(response) == "PHASE_FINISHED"
