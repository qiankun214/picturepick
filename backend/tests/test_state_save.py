"""选片页「保存 JSON」状态保存 / 直接导入重启测试（v5.3）。

* 选片页保存的 JSON 带 ``phase="picking"``，恢复后直接回到选片状态（可继续 pick）；
* 结束生成的 JSON ``phase="finished"``，恢复后进入结果页（旧行为/旧 JSON 默认值）。
"""

from __future__ import annotations

import json

from helpers import create_session, error_code, finish, list_round_photos, pick


def _pick_two(client, sid: str) -> list:
    photos = list_round_photos(client, sid)["photos"]
    pick(client, sid, photos[0]["id"])
    pick(client, sid, photos[1]["id"])
    return photos


def test_picking_result_has_phase_and_picks(client, library):
    sid = create_session(client, library)["id"]
    _pick_two(client, sid)
    result = client.get(f"/api/sessions/{sid}/result").json()
    assert result["phase"] == "picking"
    assert result["roundsPlayed"] == 1
    assert result["pickedPhotoCount"] == 2
    assert result["lastRound"]["pickedCount"] == 2
    picked = [p for p in result["photos"] if p["pickCount"] > 0]
    assert len(picked) == 2
    assert all(p["pickedInRounds"] == [1] for p in picked)


def test_download_state_json_contains_phase(client, library):
    sid = create_session(client, library)["id"]
    _pick_two(client, sid)
    downloaded = client.get(f"/api/sessions/{sid}/result", params={"download": 1})
    assert downloaded.status_code == 200
    payload = json.loads(downloaded.content.decode("utf-8"))
    assert payload["phase"] == "picking"
    assert payload["sourceDirectory"]
    assert len(payload["photos"]) == 6


def test_finish_result_phase_finished(client, library):
    sid = create_session(client, library)["id"]
    _pick_two(client, sid)
    response = finish(client, sid)
    assert response.status_code == 200
    assert response.json()["result"]["phase"] == "finished"


def test_restore_picking_state_returns_to_picking(client, library):
    sid = create_session(client, library)["id"]
    _pick_two(client, sid)
    state = client.get(f"/api/sessions/{sid}/result").json()

    restored = client.post("/api/sessions/restore", json=state)
    assert restored.status_code == 201, restored.text
    summary = restored.json()
    assert summary["phase"] == "picking"
    assert summary["restored"] is True
    assert summary["roundIndex"] == 1
    assert summary["pickCount"] == 2

    round_photos = list_round_photos(client, summary["id"])["photos"]
    picked_names = sorted(p["relativePath"] for p in round_photos if p["picked"])
    assert picked_names == ["a.jpg", "b.JPG"]

    # 恢复后可以继续 pick（不是只读的结果页）
    target = next(p for p in round_photos if not p["picked"])
    continued = pick(client, summary["id"], target["id"])
    assert continued["phase"] == "picking"
    assert continued["pickCount"] == 3


def test_restore_picking_state_with_no_picks(client, library):
    sid = create_session(client, library)["id"]
    state = client.get(f"/api/sessions/{sid}/result").json()
    assert state["phase"] == "picking"
    assert state["pickedPhotoCount"] == 0

    restored = client.post("/api/sessions/restore", json=state)
    assert restored.status_code == 201, restored.text
    summary = restored.json()
    assert summary["phase"] == "picking"
    assert summary["roundPhotoCount"] == 6
    assert summary["pickCount"] == 0


def test_restore_old_json_without_phase_defaults_finished(client, library):
    sid = create_session(client, library)["id"]
    _pick_two(client, sid)
    result = finish(client, sid).json()["result"]
    legacy = dict(result)
    legacy.pop("phase", None)

    restored = client.post("/api/sessions/restore", json=legacy)
    assert restored.status_code == 201, restored.text
    assert restored.json()["phase"] == "finished"


def test_restore_invalid_phase_is_rejected(client, library):
    sid = create_session(client, library)["id"]
    state = client.get(f"/api/sessions/{sid}/result").json()
    state["phase"] = "weird"
    response = client.post("/api/sessions/restore", json=state)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_RESTORE"
