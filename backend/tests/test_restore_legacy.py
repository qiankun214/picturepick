"""restore 旧版（v1）JSON 兼容测试（task-10 / docs/API.md 第 3 版 §4 兼容扩展）。

真实案例：旧版本保存的结果 JSON 没有 origin / lastRound 字段，photos[].absolutePath
可能指向已断开或改名的网络盘/移动盘。restore 需要：
* 缺失 origin 按 server、缺失 schemaVersion 按 1、缺失 roundsPlayed/lastRound 自动推导；
* 源目录不可扫描时回退用 absolutePath 中仍存在的照片重建。
"""

from __future__ import annotations

import copy
import os

from helpers import (
    create_session,
    error_code,
    expected_photo_id,
    finish,
    get_summary,
    list_round_photos,
    next_round,
    pick,
    photos_by_name,
)

RESTORE = "/api/sessions/restore"


def _photos(client, session_id, index=1):
    return photos_by_name(list_round_photos(client, session_id, index)["photos"])


def _restore(client, payload):
    return client.post(RESTORE, json=payload)


def _finished_two_round_result(client, library):
    """两轮流程：第 1 轮 pick a/b；第 2 轮再 pick a/b → finish。"""
    session = create_session(client, library)
    sid = session["id"]
    round1 = _photos(client, sid)
    pick(client, sid, round1["a.jpg"]["id"])
    pick(client, sid, round1["b.JPG"]["id"])
    assert next_round(client, sid).status_code == 200
    for photo in list_round_photos(client, sid, 2)["photos"]:
        pick(client, sid, photo["id"])
    return finish(client, sid).json()["result"]


def _as_v1(result, *, keep_schema=True, keep_rounds_played=True):
    """把新版结果 JSON 变成旧版（v1）形状：无 origin / lastRound（可选去掉其它字段）。"""
    payload = copy.deepcopy(result)
    payload.pop("origin", None)
    payload.pop("lastRound", None)
    if not keep_schema:
        payload.pop("schemaVersion", None)
    if not keep_rounds_played:
        payload.pop("roundsPlayed", None)
    return payload


def _break_absolute_paths(payload, keys):
    """把指定 relativePath 的 absolutePath 改成不存在的路径（模拟网络盘掉线）。"""
    broken = set(keys)
    for photo in payload["photos"]:
        if photo["relativePath"] in broken:
            photo["absolutePath"] = str("Z:\\no\\such\\dir") + "\\" + photo["name"]


# ---- v1 JSON（无 origin / lastRound） --------------------------------------


def test_v1_json_without_origin_and_last_round_restores(client, library):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)

    response = _restore(client, payload)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["restored"] is True
    assert summary["phase"] == "finished"
    assert summary["roundIndex"] == payload["roundsPlayed"] == 2
    assert summary["roundCount"] == 2
    assert summary["rounds"] == [
        {"index": 1, "photoCount": 6, "viewedCount": 0, "pickedCount": 2},
        {"index": 2, "photoCount": 2, "viewedCount": 0, "pickedCount": 2},
    ]

    restored_result = client.get(f"/api/sessions/{summary['id']}/result").json()
    # lastRound 由 pickedInRounds 含 roundsPlayed 推导，应与原结果一致
    assert restored_result["lastRound"] == original["lastRound"]
    assert restored_result["lastRound"]["index"] == 2
    assert restored_result["lastRound"]["pickedCount"] == 2
    by_relative = {photo["relativePath"]: photo for photo in restored_result["photos"]}
    assert by_relative["a.jpg"]["pickCount"] == 2
    assert by_relative["a.jpg"]["pickedInRounds"] == [1, 2]
    assert by_relative["b.JPG"]["pickCount"] == 2
    assert restored_result["roundsPlayed"] == 2
    assert restored_result["origin"] == "server"


def test_legacy_missing_schema_version_defaults_to_1(client, library):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original, keep_schema=False)
    assert "schemaVersion" not in payload

    response = _restore(client, payload)
    assert response.status_code == 201, response.text
    assert response.json()["restored"] is True


def test_legacy_missing_rounds_played_is_derived(client, library):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original, keep_rounds_played=False)
    assert "roundsPlayed" not in payload

    response = _restore(client, payload)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["roundIndex"] == 2  # max(pickedInRounds)
    assert summary["roundCount"] == 2
    restored_result = client.get(f"/api/sessions/{summary['id']}/result").json()
    assert restored_result["roundsPlayed"] == 2
    assert restored_result["lastRound"]["pickedCount"] == 2


def test_legacy_origin_missing_schema_v2_rejected(client, library):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    payload["schemaVersion"] = 2
    response = _restore(client, payload)
    assert response.status_code == 400
    assert error_code(response) == "UNSUPPORTED_SCHEMA"


def test_legacy_explicit_browser_origin_still_rejected(client, library):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    payload["origin"] = "browser"
    response = _restore(client, payload)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_SOURCE"


# ---- 源目录不可用 → absolutePath 回退 -------------------------------------


def test_source_directory_missing_falls_back_to_absolute_paths(client, library, tmp_path):
    """模拟「目录改名/网络盘换盘符，但 absolutePath 照片仍存在」。"""
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    renamed_source = str(tmp_path / "old-renamed-source")
    payload["sourceDirectory"] = renamed_source

    response = _restore(client, payload)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["restored"] is True
    assert summary["origin"] == "server"
    assert summary["sourceLabel"] == renamed_source  # 展示保留 JSON 原文
    # 回退重建的照片数 = JSON 中 absolutePath 仍存在的照片数
    assert summary["libraryTotal"] == len(payload["photos"])
    assert summary["rounds"][0]["photoCount"] == len(payload["photos"])
    # root 指向可用照片所在目录，保证 /image 与 export-photos 的路径安全校验可用
    assert os.path.normcase(summary["root"]) == os.path.normcase(os.path.abspath(str(library)))

    restored_result = client.get(f"/api/sessions/{summary['id']}/result").json()
    assert restored_result["sourceDirectory"] == renamed_source
    assert restored_result["lastRound"]["pickedCount"] == 2
    by_relative = {photo["relativePath"]: photo for photo in restored_result["photos"]}
    # relativePath 用 JSON 值；absolutePath 为真实绝对路径；ID = sha1(absolutePath)[:16]
    assert by_relative["a.jpg"]["absolutePath"] == os.path.abspath(str(library / "a.jpg"))
    assert by_relative["a.jpg"]["id"] == expected_photo_id(library / "a.jpg")

    # 回退恢复后仍可看图片 / 导出照片
    listed = _photos(client, summary["id"])
    image = client.get(
        f"/api/sessions/{summary['id']}/photos/{listed['a.jpg']['id']}/image"
    )
    assert image.status_code == 200
    destination = tmp_path / "export-fallback"
    destination.mkdir()
    exported = client.post(
        f"/api/sessions/{summary['id']}/export-photos",
        json={"destination": str(destination)},
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["copied"] == 2


def test_fallback_partial_absolute_paths_ignored(client, library, tmp_path):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    payload["sourceDirectory"] = str(tmp_path / "gone")
    _break_absolute_paths(payload, {"a.jpg"})  # 只丢失一张（被 pick 的）照片

    response = _restore(client, payload)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["libraryTotal"] == len(payload["photos"]) - 1
    restored_result = client.get(f"/api/sessions/{summary['id']}/result").json()
    assert "a.jpg" not in {photo["relativePath"] for photo in restored_result["photos"]}
    # 第 2 轮只剩 b.JPG
    assert restored_result["lastRound"]["pickedCount"] == 1
    assert restored_result["lastRound"]["index"] == 2


def test_fallback_session_can_resume(client, library, tmp_path):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    payload["sourceDirectory"] = str(tmp_path / "gone")

    summary = _restore(client, payload).json()
    response = client.post(f"/api/sessions/{summary['id']}/resume")
    assert response.status_code == 200, response.text
    resumed = response.json()
    assert resumed["phase"] == "picking"
    assert resumed["roundIndex"] == 3
    assert resumed["roundPhotoCount"] == 2
    assert resumed["restored"] is True


# ---- 所有路径都不可用 ------------------------------------------------------


def test_all_paths_unavailable_returns_invalid_path_with_source(client, library, tmp_path):
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    missing_source = str(tmp_path / "network-drive-offline" / "[20261024]相册" / "JPG")
    payload["sourceDirectory"] = missing_source
    for photo in payload["photos"]:
        photo["absolutePath"] = str("Z:\\no\\such\\dir") + "\\" + photo["name"]

    response = _restore(client, payload)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_PATH"
    assert missing_source in detail["message"]
    assert "请确认" in detail["message"]


def test_scan_without_matches_and_no_absolute_path_returns_invalid_path(client, library):
    """JSON 照片全部没有 absolutePath（如 browser 风格），源目录匹配不到 → INVALID_PATH。"""
    original = _finished_two_round_result(client, library)
    payload = _as_v1(original)
    for photo in payload["photos"]:
        photo["absolutePath"] = ""
    for photo in payload["photos"]:
        photo["relativePath"] = "unmatched/" + photo["name"]

    response = _restore(client, payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_PATH"
    assert payload["sourceDirectory"] in detail["message"]
