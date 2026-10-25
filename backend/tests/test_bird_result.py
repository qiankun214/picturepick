"""AI 识鸟：结果 JSON / 恢复现场 / 结束态采纳测试（docs/BIRDID-CONTRACT.md §6.3）。"""

from __future__ import annotations

import json

from helpers import (
    create_session,
    error_code,
    finish,
    get_summary,
    list_round_photos,
    submit_and_wait,
    wait_bird_idle,
)


def session_photos(client, directory):
    session = create_session(client, directory)
    photos = list_round_photos(client, session["id"])["photos"]
    return session["id"], photos


def adopt_first(client, sid: str, photo_id: str) -> dict:
    task = submit_and_wait(client, sid, photo_id)
    response = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert response.status_code == 200, response.text
    return response.json()


def test_picking_result_contains_bird(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    observation = adopt_first(client, sid, photos[0]["id"])["observation"]
    result = client.get(f"/api/sessions/{sid}/result").json()
    assert result["birdIdentifiedCount"] == 1
    target = next(photo for photo in result["photos"] if photo["id"] == photos[0]["id"])
    assert target["bird"]["species"] == observation["species"]
    assert target["bird"]["adoptedAt"]
    assert wait_bird_idle(client)


def test_finish_result_and_download_include_bird(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    observation = adopt_first(client, sid, photos[0]["id"])["observation"]
    response = finish(client, sid)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["schemaVersion"] == 1
    assert result["birdIdentifiedCount"] == 1
    target = next(photo for photo in result["photos"] if photo["id"] == photos[0]["id"])
    assert target["bird"]["species"] == observation["species"]
    assert target["bird"]["exifWritten"] is True

    download = client.get(f"/api/sessions/{sid}/result", params={"download": 1})
    assert download.status_code == 200
    payload = json.loads(download.content.decode("utf-8"))
    assert payload["birdIdentifiedCount"] == 1


def test_restore_keeps_bird_identification(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    observation = adopt_first(client, sid, photos[0]["id"])["observation"]
    result = finish(client, sid).json()["result"]

    restored = client.post("/api/sessions/restore", json=result)
    assert restored.status_code == 201, restored.text
    restored_id = restored.json()["id"]
    assert restored.json()["birdIdentifiedCount"] == 1

    restored_result = client.get(f"/api/sessions/{restored_id}/result").json()
    target = next(photo for photo in restored_result["photos"] if photo["id"] == photos[0]["id"])
    assert target["bird"]["species"] == observation["species"]
    assert target["bird"]["family"] == observation["family"]

    active_photos = client.get(f"/api/sessions/{restored_id}/rounds/1/photos").json()["photos"]
    adopted = next(photo for photo in active_photos if photo["id"] == photos[0]["id"])
    assert adopted["bird"]["species"] == observation["species"]
    assert wait_bird_idle(client)


def test_restore_legacy_json_without_bird(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    adopt_first(client, sid, photos[0]["id"])
    result = finish(client, sid).json()["result"]

    # 模拟旧版本导出的 JSON：没有 bird / birdIdentifiedCount 字段
    legacy = json.loads(json.dumps(result))
    legacy.pop("birdIdentifiedCount", None)
    for photo in legacy["photos"]:
        photo.pop("bird", None)

    restored = client.post("/api/sessions/restore", json=legacy)
    assert restored.status_code == 201, restored.text
    restored_id = restored.json()["id"]
    assert restored.json()["birdIdentifiedCount"] == 0
    restored_result = client.get(f"/api/sessions/{restored_id}/result").json()
    assert restored_result["birdIdentifiedCount"] == 0
    assert all(photo["bird"] is None for photo in restored_result["photos"])
    assert wait_bird_idle(client)


def test_adopt_after_finish_updates_frozen_result(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    finish(client, sid)

    before = client.get(f"/api/sessions/{sid}/result").json()
    assert before["birdIdentifiedCount"] == 0

    adopted = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert adopted.status_code == 200, adopted.text

    after = client.get(f"/api/sessions/{sid}/result").json()
    assert after["birdIdentifiedCount"] == 1
    target = next(photo for photo in after["photos"] if photo["id"] == photos[0]["id"])
    assert target["bird"] is not None
    assert wait_bird_idle(client)


def test_restore_rejects_unknown_bird_shape_gracefully(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    adopt_first(client, sid, photos[0]["id"])
    result = finish(client, sid).json()["result"]

    broken = json.loads(json.dumps(result))
    for photo in broken["photos"]:
        if photo.get("bird"):
            photo["bird"] = {"species": 123, "confidence": "high", "weird": [1, 2]}
            break

    restored = client.post("/api/sessions/restore", json=broken)
    assert restored.status_code == 201, restored.text
    assert wait_bird_idle(client)
