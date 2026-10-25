"""AI 识鸟：任务队列 / 进度 / 采纳 / 忽略 接口测试（docs/BIRDID-CONTRACT.md §5、§6.2）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from helpers import (
    create_bird_task,
    create_session,
    error_code,
    finish,
    get_bird_tasks,
    get_summary,
    list_round_photos,
    make_jpeg,
    submit_and_wait,
    wait_bird_idle,
    wait_bird_task,
)

DEFAULT_MODEL = "doubao-seed-2-1-lite"


def session_photos(client, directory: str) -> tuple[str, list]:
    session = create_session(client, directory)
    photos = list_round_photos(client, session["id"])["photos"]
    return session["id"], photos


def task_by_id(tasks: list, task_id: str) -> dict:
    for task in tasks:
        if task["id"] == task_id:
            return task
    raise AssertionError(f"任务列表中缺少 {task_id}")


def test_create_task_returns_queued_metadata(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    response = create_bird_task(client, sid, photos[0]["id"])
    assert response.status_code == 201, response.text
    task = response.json()

    assert task["sessionId"] == sid
    assert task["photoId"] == photos[0]["id"]
    assert task["photoName"] == "a.jpg"
    assert task["status"] == "queued"
    assert task["stage"] == "queued"
    assert task["progress"] == 0
    assert task["queuePosition"] == 1
    assert task["model"] == DEFAULT_MODEL
    assert task["result"] is None
    assert task["error"] is None
    assert task["adopted"] is False
    assert task["referenceAvailable"] is False
    assert task["referenceUrl"] is None
    assert task["thumbnailUrl"].endswith(f"/photos/{photos[0]['id']}/image?variant=thumb")
    assert task["createdAt"]
    assert task["startedAt"] is None
    assert wait_bird_idle(client)


def test_duplicate_active_task_is_reused(client, library, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_DELAY", "0.3")
    sid, photos = session_photos(client, library)
    first = create_bird_task(client, sid, photos[0]["id"]).json()
    second = create_bird_task(client, sid, photos[0]["id"])
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]
    assert len(get_bird_tasks(client, sid)["tasks"]) == 1
    assert wait_bird_idle(client)


def test_queue_order_and_counters(client, library, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_DELAY", "0.35")
    sid, photos = session_photos(client, library)
    create_bird_task(client, sid, photos[0]["id"])
    # 等第一张进入 running
    deadline = time.monotonic() + 5
    running = None
    while time.monotonic() < deadline:
        tasks = get_bird_tasks(client, sid)["tasks"]
        if tasks and tasks[0]["status"] == "running":
            running = tasks[0]
            break
        time.sleep(0.01)
    assert running is not None

    create_bird_task(client, sid, photos[1]["id"])
    payload = get_bird_tasks(client, sid)
    statuses = [task["status"] for task in payload["tasks"]]
    assert statuses[0] == "running"
    assert statuses[1] == "queued"
    assert payload["tasks"][1]["queuePosition"] == 1
    assert payload["queue"] == {"queued": 1, "running": 1}

    status = client.get("/api/bird/status").json()
    assert status["queue"] == {"queued": 1, "running": 1}
    assert wait_bird_idle(client)
    assert client.get("/api/bird/status").json()["queue"] == {"queued": 0, "running": 0}


def test_completed_task_has_result_and_reference(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert task["status"] == "completed"
    assert task["stage"] == "done"
    assert task["progress"] == 100
    assert task["result"]["identified"] is True
    assert task["result"]["species"]
    assert task["result"]["family"]
    assert task["result"]["genus"]
    assert task["referenceAvailable"] is True
    assert task["referenceUrl"].endswith(f"/bird-tasks/{task['id']}/reference")
    assert task["finishedAt"] is not None
    assert task["elapsedMs"] >= 0
    assert task["error"] is None

    reference = client.get(task["referenceUrl"])
    assert reference.status_code == 200
    assert reference.headers["content-type"].startswith("image/jpeg")
    assert len(reference.content) > 1000
    assert reference.content[:2] == b"\xff\xd8"

    assert wait_bird_idle(client)


def test_dismiss_completed_task_is_idempotent(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    first = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/dismiss")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "dismissed"
    second = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/dismiss")
    assert second.status_code == 200
    assert second.json()["status"] == "dismissed"
    assert get_bird_tasks(client, sid)["tasks"][0]["status"] == "dismissed"
    assert wait_bird_idle(client)


def test_dismiss_queued_task(client, library, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_DELAY", "0.4")
    sid, photos = session_photos(client, library)
    create_bird_task(client, sid, photos[0]["id"])
    time.sleep(0.05)
    second = create_bird_task(client, sid, photos[1]["id"]).json()
    response = client.post(f"/api/sessions/{sid}/bird-tasks/{second['id']}/dismiss")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dismissed"
    assert response.json()["queuePosition"] is None
    assert wait_bird_idle(client)
    statuses = {task["id"]: task["status"] for task in get_bird_tasks(client, sid)["tasks"]}
    assert statuses[second["id"]] == "dismissed"


def test_adopt_writes_exif_and_result_json(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    response = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert response.status_code == 200, response.text
    payload = response.json()

    observation = payload["observation"]
    assert observation["species"] == task["result"]["species"]
    assert observation["family"] == task["result"]["family"]
    assert observation["genus"] == task["result"]["genus"]
    assert observation["model"] == DEFAULT_MODEL
    assert observation["adoptedAt"]
    assert payload["exif"]["written"] is True
    assert payload["exif"]["error"] is None
    assert payload["task"]["status"] == "adopted"
    assert payload["task"]["adopted"] is True

    adopted_photo = client.get(f"/api/sessions/{sid}/rounds/1/photos").json()["photos"][0]
    assert adopted_photo["bird"]["species"] == observation["species"]
    assert adopted_photo["bird"]["exifWritten"] is True
    assert get_summary(client, sid)["birdIdentifiedCount"] == 1

    result = client.get(f"/api/sessions/{sid}/result").json()
    assert result["birdIdentifiedCount"] == 1
    json_photo = next(photo for photo in result["photos"] if photo["id"] == photos[0]["id"])
    assert json_photo["bird"]["species"] == observation["species"]

    absolute = json_photo["absolutePath"]
    with Image.open(absolute) as image:
        tags = dict(image.getexif())
    assert observation["species"] in tags[0x9C9B].decode("utf-16-le")
    assert observation["family"] in tags[0x9C9E].decode("utf-16-le")

    # 再次采纳：用当前任务结果覆盖（幂等更新）
    again = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert again.status_code == 200
    assert again.json()["task"]["status"] == "adopted"
    assert wait_bird_idle(client)


def test_adopt_unfinished_task_conflicts(client, library, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_DELAY", "0.4")
    sid, photos = session_photos(client, library)
    task = create_bird_task(client, sid, photos[0]["id"]).json()
    response = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert response.status_code == 409
    assert error_code(response) == "BIRD_TASK_NOT_COMPLETED"
    assert wait_bird_idle(client)


def test_adopt_unknown_task_returns_404(client, library):
    sid, _photos = session_photos(client, library)
    response = client.post(f"/api/sessions/{sid}/bird-tasks/not-a-task/adopt")
    assert response.status_code == 404
    assert error_code(response) == "BIRD_TASK_NOT_FOUND"


def test_task_session_mismatch_returns_404(client, library, other_library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    other_sid, _other_photos = session_photos(client, other_library)
    response = client.post(f"/api/sessions/{other_sid}/bird-tasks/{task['id']}/adopt")
    assert response.status_code == 404
    assert error_code(response) == "BIRD_TASK_NOT_FOUND"
    assert wait_bird_idle(client)


def test_dismiss_adopted_task_conflicts(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt").status_code == 200
    response = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/dismiss")
    assert response.status_code == 409
    assert error_code(response) == "BIRD_TASK_ADOPTED"


def test_submit_without_configuration_returns_409(client, library):
    sid, photos = session_photos(client, library)
    response = create_bird_task(client, sid, photos[0]["id"])
    assert response.status_code == 409
    assert error_code(response) == "BIRD_NOT_CONFIGURED"


def test_finished_session_rejects_new_task(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    finish(client, sid)
    response = create_bird_task(client, sid, photos[0]["id"])
    assert response.status_code == 409
    assert error_code(response) == "PHASE_FINISHED"


def test_browser_session_rejects_bird_task(client, mock_bird):
    created = client.post(
        "/api/sessions",
        json={
            "source": "browser",
            "label": "旧会话",
            "photos": [{"relativePath": "a.jpg", "sizeBytes": 100}],
        },
    )
    assert created.status_code == 201
    sid = created.json()["id"]
    photos = client.get(f"/api/sessions/{sid}/rounds/1/photos").json()["photos"]
    response = create_bird_task(client, sid, photos[0]["id"])
    assert response.status_code == 400
    assert error_code(response) == "INVALID_SOURCE"


def test_mock_failure_task(client, library, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_FAIL", "1")
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert task["status"] == "failed"
    assert task["error"]["code"] == "BIRD_API_ERROR"
    assert "模拟失败" in task["error"]["message"]
    assert task["referenceAvailable"] is False

    reference = client.get(f"/api/sessions/{sid}/bird-tasks/{task['id']}/reference")
    assert reference.status_code == 404
    assert error_code(reference) == "BIRD_REFERENCE_UNAVAILABLE"

    dismiss = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/dismiss")
    assert dismiss.status_code == 200
    assert dismiss.json()["status"] == "dismissed"
    assert wait_bird_idle(client)


def test_non_bird_result_cannot_be_adopted(client, tmp_path, mock_bird):
    root = tmp_path / "birds"
    make_jpeg(root / "notbird-1.jpg", color=(20, 40, 60))
    sid, photos = session_photos(client, root)
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert task["status"] == "completed"
    assert task["result"]["identified"] is False
    assert task["result"]["reason"]
    assert task["referenceAvailable"] is False

    response = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert response.status_code == 409
    assert error_code(response) == "BIRD_RESULT_UNAVAILABLE"
    assert wait_bird_idle(client)


def test_reference_unavailable_while_queued(client, library, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_DELAY", "0.4")
    sid, photos = session_photos(client, library)
    task = create_bird_task(client, sid, photos[0]["id"]).json()
    response = client.get(f"/api/sessions/{sid}/bird-tasks/{task['id']}/reference")
    assert response.status_code == 404
    assert error_code(response) == "BIRD_REFERENCE_UNAVAILABLE"
    assert wait_bird_idle(client)


def test_task_not_found(client, library):
    sid, _photos = session_photos(client, library)
    response = client.get(f"/api/sessions/{sid}/bird-tasks/nope")
    assert response.status_code == 404
    assert error_code(response) == "BIRD_TASK_NOT_FOUND"


def test_adopt_does_not_require_picked_photo(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    task = submit_and_wait(client, sid, photos[2]["id"])
    response = client.post(f"/api/sessions/{sid}/bird-tasks/{task['id']}/adopt")
    assert response.status_code == 200
    result = client.get(f"/api/sessions/{sid}/result").json()
    target = next(photo for photo in result["photos"] if photo["id"] == photos[2]["id"])
    assert target["pickCount"] == 0
    assert target["bird"] is not None
    assert wait_bird_idle(client)


class _NoWebSearchClient:
    """模拟 Responses API：模型可用但联网搜索插件未开通（参考图缺失）。"""

    web_search_error = "ToolNotOpen: Your account has not activated web search."

    def __init__(self, config) -> None:
        self.config = config

    def identify(self, image_bytes, seed="", photo_name=""):
        from picturepick.birdid.parser import ParsedIdentification
        from picturepick.models import BirdResult

        return ParsedIdentification(
            result=BirdResult(
                identified=True,
                family="鹭科",
                familyLatin="Ardeidae",
                genus="白鹭属",
                genusLatin="Egretta",
                species="白鹭",
                speciesLatin="Egretta garzetta",
                confidence=0.9,
                referenceSource="",
            )
        )

    def verify(self):
        return True, "ok", 0


def test_web_search_unavailable_sets_reference_hint(client, library, mock_bird):
    sid, photos = session_photos(client, library)
    service = client.app.state.bird
    service._client_factory = lambda config: _NoWebSearchClient(config)
    service._reference_resolver = lambda parsed: None
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert task["status"] == "completed"
    assert task["result"]["identified"] is True
    assert task["referenceAvailable"] is False
    assert "联网搜索" in task["result"]["referenceNote"]
    assert wait_bird_idle(client)


class _PageThenImageClient:
    """模型首轮/第一次重试都给网页，第二次重试才给图片文件直链。"""

    web_search_error = None

    def __init__(self, config) -> None:
        self.config = config
        self.requests = []

    def identify(self, image_bytes, seed="", photo_name=""):
        from picturepick.birdid.parser import ParsedIdentification
        from picturepick.models import BirdResult

        return ParsedIdentification(
            result=BirdResult(
                identified=True,
                family="鹭科",
                species="白鹭",
                speciesLatin="Egretta garzetta",
                confidence=0.9,
                referenceSource="上海自然博物馆",
            ),
            reference_image_url="https://www.snhm.org.cn/cpjc/info.htm?eid=983",
        )

    def request_reference_url(self, result, rejected=None):
        self.requests.append(list(rejected or []))
        if len(self.requests) == 1:
            return {
                "referenceImageUrl": "https://www.snhm.org.cn/cpjc/info.htm?eid=983",
                "referenceSource": "上海自然博物馆",
                "referenceNote": "",
            }
        return {
            "referenceImageUrl": "https://img.example.com/egret.jpg",
            "referenceSource": "中国鸟类图库",
            "referenceNote": "",
        }

    def verify(self):
        return True, "ok", 0


def test_page_url_is_rejected_and_model_asked_again(client, library, mock_bird):
    from picturepick.birdid.reference import ReferenceImage

    sid, photos = session_photos(client, library)
    service = client.app.state.bird
    fake_client = _PageThenImageClient(None)
    service._client_factory = lambda config: fake_client

    def resolver(parsed, timeout=None):
        url = parsed.reference_image_url or ""
        if not url.endswith(".jpg"):
            return None
        return ReferenceImage(
            data=b"\xff\xd8\xff\xe0fake",
            content_type="image/jpeg",
            source_url=url,
            source_label=parsed.result.referenceSource or "AI 提供的图片",
        )

    service._reference_resolver = resolver
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert task["status"] == "completed"
    assert task["referenceAvailable"] is True
    assert task["result"]["referenceSource"] == "中国鸟类图库"
    # 第一次重试带上首轮的网页 URL 作为「不要重复」提示
    assert any("snhm.org.cn" in u for u in fake_client.requests[0])
    # 第二次重试带上第一次重试返回的网页 URL
    assert len(fake_client.requests) >= 2
    assert any("snhm.org.cn" in u for u in fake_client.requests[1])
    assert wait_bird_idle(client)


class _WikiFirstClient:
    """首轮模型给维基链接（不可访问），再让模型要一次境内可访问直链。"""

    web_search_error = None

    def __init__(self, config) -> None:
        self.config = config

    def identify(self, image_bytes, seed="", photo_name=""):
        from picturepick.birdid.parser import ParsedIdentification
        from picturepick.models import BirdResult

        return ParsedIdentification(
            result=BirdResult(
                identified=True,
                family="鹭科",
                familyLatin="Ardeidae",
                genus="白鹭属",
                genusLatin="Egretta",
                species="白鹭",
                speciesLatin="Egretta garzetta",
                confidence=0.9,
                referenceSource="Wikimedia Commons",
            ),
            reference_image_url="https://upload.wikimedia.org/wikipedia/commons/egret.jpg",
        )

    def request_reference_url(self, result, rejected=None, **kwargs):
        return {
            "referenceImageUrl": "https://img.example.com/egret.jpg",
            "referenceSource": "中国鸟类图库",
            "referenceNote": "",
        }

    def verify(self):
        return True, "ok", 0


def test_blocked_reference_triggers_second_model_request(client, library, mock_bird):
    from picturepick.birdid.reference import ReferenceImage

    sid, photos = session_photos(client, library)
    service = client.app.state.bird
    service._client_factory = lambda config: _WikiFirstClient(config)

    calls = []

    def resolver(parsed, timeout=None):
        calls.append(parsed.reference_image_url)
        url = parsed.reference_image_url or ""
        if "wikipedia" in url or "wikimedia" in url:
            return None
        return ReferenceImage(
            data=b"\xff\xd8\xff\xe0fake",
            content_type="image/jpeg",
            source_url=url,
            source_label=parsed.result.referenceSource or "AI 提供的图片",
        )

    service._reference_resolver = resolver
    task = submit_and_wait(client, sid, photos[0]["id"])
    assert task["status"] == "completed"
    assert task["referenceAvailable"] is True
    assert task["result"]["referenceSource"] == "中国鸟类图库"
    # 第一次用模型给的维基链接，第二次用重新请求到的境内链接
    assert any("wikimedia" in call for call in calls)
    assert any("img.example.com" in call for call in calls)
    assert wait_bird_idle(client)
