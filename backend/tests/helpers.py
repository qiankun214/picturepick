"""pytest 共享工具：构造真实 JPEG、调用 API 的小封装。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, Iterable, List, Union

from PIL import Image


def make_jpeg(
    path: Union[str, Path],
    size: tuple = (64, 48),
    color: tuple = (120, 90, 200),
    quality: int = 90,
) -> Path:
    """生成一张真实 JPEG 文件（父目录自动创建）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(target, format="JPEG", quality=quality)
    return target


def expected_photo_id(path: Union[str, Path]) -> str:
    """按契约计算照片 ID：sha1(绝对路径字符串)[:16]。"""
    absolute = os.path.abspath(str(path))
    return hashlib.sha1(absolute.encode("utf-8")).hexdigest()[:16]


def create_session(client, directory: Union[str, Path], recursive: bool = True) -> dict:
    response = client.post(
        "/api/sessions",
        json={"directory": str(directory), "recursive": recursive},
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_summary(client, session_id: str) -> dict:
    response = client.get(f"/api/sessions/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()


def list_round_photos(client, session_id: str, index: int = 1, **params) -> dict:
    response = client.get(
        f"/api/sessions/{session_id}/rounds/{index}/photos",
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def photos_by_name(photos_or_payload) -> Dict[str, dict]:
    photos = photos_or_payload["photos"] if isinstance(photos_or_payload, dict) else photos_or_payload
    return {photo["name"]: photo for photo in photos}


def view(client, session_id: str, photo_id: str) -> dict:
    response = client.post(f"/api/sessions/{session_id}/photos/{photo_id}/viewed")
    assert response.status_code == 200, response.text
    return response.json()


def pick(client, session_id: str, photo_id: str) -> dict:
    response = client.put(f"/api/sessions/{session_id}/photos/{photo_id}/pick")
    assert response.status_code == 200, response.text
    return response.json()


def unpick(client, session_id: str, photo_id: str) -> dict:
    response = client.delete(f"/api/sessions/{session_id}/photos/{photo_id}/pick")
    assert response.status_code == 200, response.text
    return response.json()


def next_round(client, session_id: str):
    return client.post(f"/api/sessions/{session_id}/rounds/next")


def finish(client, session_id: str):
    return client.post(f"/api/sessions/{session_id}/finish")


def error_code(response) -> str:
    try:
        return response.json()["detail"]["code"]
    except (KeyError, TypeError, ValueError):
        return ""


def ids_of(photos: Iterable[dict]) -> List[str]:
    return [photo["id"] for photo in photos]


# ---- AI 识鸟（第 5 版） ----------------------------------------------------


def create_bird_task(client, session_id: str, photo_id: str):
    return client.post(
        f"/api/sessions/{session_id}/bird-tasks", json={"photoId": photo_id}
    )


def get_bird_tasks(client, session_id: str) -> dict:
    response = client.get(f"/api/sessions/{session_id}/bird-tasks")
    assert response.status_code == 200, response.text
    return response.json()


def wait_bird_task(client, session_id: str, task_id: str, timeout: float = 10.0) -> dict:
    """轮询单个任务直到进入终态（completed / failed / adopted / dismissed）。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{session_id}/bird-tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in ("completed", "failed", "adopted", "dismissed"):
            return task
        time.sleep(0.01)
    raise AssertionError(f"任务在 {timeout}s 内未完成: {task}")


def submit_and_wait(client, session_id: str, photo_id: str, timeout: float = 10.0) -> dict:
    response = create_bird_task(client, session_id, photo_id)
    assert response.status_code == 201, response.text
    task = response.json()
    return wait_bird_task(client, session_id, task["id"], timeout=timeout)


def wait_bird_idle(client, timeout: float = 10.0) -> bool:
    """等待识鸟任务 worker 清空队列（避免测试结束时后台仍访问网络）。"""
    service = getattr(client.app.state, "bird", None)
    if service is None:
        return True
    return service.wait_idle(timeout=timeout)
