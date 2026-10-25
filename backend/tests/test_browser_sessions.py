"""browser 来源会话 + PickResult.lastRound 测试（docs/API.md 第 2 版）。

浏览器原生目录选择只上传照片元数据：后端不读文件系统，照片 ID =
sha1(relativePath)[:16]，absolutePath 为空串，图片接口统一 404。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse

import pytest

from helpers import (
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
from picturepick import scanner


DEFAULT_LABEL = "假期照片"
BROWSER_PHOTOS = [
    {"relativePath": "2024/b.jpg", "sizeBytes": 120, "lastModified": 1719999999000},
    {"relativePath": "a.jpg", "sizeBytes": 100},
    {"relativePath": "z/c.jpeg", "sizeBytes": 140, "lastModified": 1719999998000},
]


def browser_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]


def browser_payload(photos, label=DEFAULT_LABEL, recursive=True, **extra):
    payload = {
        "source": "browser",
        "label": label,
        "recursive": recursive,
        "photos": photos,
    }
    payload.update(extra)
    return payload


def create_browser(client, photos=None, label=DEFAULT_LABEL, **extra):
    response = client.post(
        "/api/sessions",
        json=browser_payload(BROWSER_PHOTOS if photos is None else photos, label, **extra),
    )
    assert response.status_code == 201, response.text
    return response.json()


def listing(client, session_id, index=1, **params):
    return list_round_photos(client, session_id, index, **params)


# ---- 创建与 SessionSummary --------------------------------------------------


def test_create_browser_session_summary(client):
    session = create_browser(client)

    assert re.fullmatch(r"[0-9a-f]{32}", session["id"])
    assert session["origin"] == "browser"
    assert session["sourceLabel"] == DEFAULT_LABEL
    assert session["root"] == DEFAULT_LABEL
    assert session["recursive"] is True
    assert session["phase"] == "picking"
    assert session["roundIndex"] == 1
    assert session["roundCount"] == 1
    assert session["libraryTotal"] == 3
    assert session["roundPhotoCount"] == 3
    assert session["viewedCount"] == 0
    assert session["pickCount"] == 0
    assert session["rounds"] == [
        {"index": 1, "photoCount": 3, "viewedCount": 0, "pickedCount": 0}
    ]

    # GET /sessions/{sid} 与 current 都带同样的新字段
    assert get_summary(client, session["id"]) == session
    current = client.get("/api/sessions/current").json()["session"]
    assert current["id"] == session["id"]
    assert current["origin"] == "browser"


def test_browser_session_default_label_and_recursive_flag(client):
    created = create_browser(client, label="   ")
    assert created["sourceLabel"] == "照片文件夹"
    assert created["root"] == "照片文件夹"

    created = create_browser(client, label=" 我的照片 ", recursive=False)
    assert created["sourceLabel"] == "我的照片"
    assert created["recursive"] is False


def test_browser_photo_items_do_not_include_images(client):
    session = create_browser(client)
    payload = listing(client, session["id"])

    assert payload["total"] == 3
    photos = payload["photos"]
    # 按 relativePath 不区分大小写排序
    assert [photo["relativePath"] for photo in photos] == ["2024/b.jpg", "a.jpg", "z/c.jpeg"]
    assert [photo["name"] for photo in photos] == ["b.jpg", "a.jpg", "c.jpeg"]
    assert [photo["sizeBytes"] for photo in photos] == [120, 100, 140]
    for photo in photos:
        assert set(photo) == {
            "id",
            "name",
            "relativePath",
            "sizeBytes",
            "pickCount",
            "picked",
            "viewed",
        }
        assert photo["id"] == browser_id(photo["relativePath"])
        assert photo["pickCount"] == 0
        assert photo["picked"] is False
        assert photo["viewed"] is False


def test_browser_photo_id_stable_across_sessions(client):
    first = create_browser(client, label="A")
    second = create_browser(client, label="B")
    ids_first = {photo["relativePath"]: photo["id"] for photo in listing(client, first["id"])["photos"]}
    ids_second = {photo["relativePath"]: photo["id"] for photo in listing(client, second["id"])["photos"]}
    assert ids_first == ids_second


def test_browser_id_and_absolute_path_via_scanner():
    photos = scanner.build_browser_photos(
        [
            {"relativePath": "sub/nested/A.JPG", "sizeBytes": 7},
            {"relativePath": "b.jpeg"},
        ]
    )
    assert [photo.relative_path for photo in photos] == ["b.jpeg", "sub/nested/A.JPG"]
    for photo in photos:
        assert photo.id == browser_id(photo.relative_path)
        assert photo.absolute_path == ""
    assert scanner.MAX_BROWSER_PHOTOS == 50000


# ---- 校验 ------------------------------------------------------------------


def test_browser_duplicates_are_deduped(client):
    created = create_browser(
        client,
        [
            {"relativePath": "a.jpg", "sizeBytes": 10},
            {"relativePath": "a.jpg", "sizeBytes": 99},
            {"relativePath": "sub\\b.JPG", "sizeBytes": 20},
            {"relativePath": "sub/b.JPG", "sizeBytes": 21},
            {"relativePath": "c.jpeg", "sizeBytes": 30},
        ],
    )
    assert created["libraryTotal"] == 3
    photos = photos_by_name(listing(client, created["id"])["photos"])
    assert set(photos) == {"a.jpg", "b.JPG", "c.jpeg"}
    assert photos["a.jpg"]["sizeBytes"] == 10  # 保留首次出现
    # 反斜杠路径被规范化为正斜杠
    assert photos["b.JPG"]["relativePath"] == "sub/b.JPG"
    assert photos["b.JPG"]["id"] == browser_id("sub/b.JPG")


@pytest.mark.parametrize(
    "photos",
    [
        pytest.param([{"relativePath": "/abs/a.jpg"}], id="absolute"),
        pytest.param([{"relativePath": "C:\\photos\\a.jpg"}], id="drive-letter"),
        pytest.param([{"relativePath": "../a.jpg"}], id="parent-prefix"),
        pytest.param([{"relativePath": "a/../b.jpg"}], id="parent-segment"),
        pytest.param([{"relativePath": "a//b.jpg"}], id="empty-segment"),
        pytest.param([{"relativePath": "./a.jpg"}], id="dot-prefix"),
        pytest.param([{"relativePath": "a/./b.jpg"}], id="dot-segment"),
        pytest.param([{"relativePath": "a/b.png"}], id="wrong-extension"),
        pytest.param([{"relativePath": "photo"}], id="no-extension"),
        pytest.param([{"relativePath": ""}], id="empty-path"),
        pytest.param([{"relativePath": "   "}], id="blank-path"),
        pytest.param([{"relativePath": 42}], id="non-string-path"),
        pytest.param([{"sizeBytes": 1}], id="missing-path"),
        pytest.param([{"relativePath": "a.jpg", "sizeBytes": -1}], id="negative-size"),
        pytest.param([{"relativePath": "a.jpg", "sizeBytes": "12"}], id="string-size"),
        pytest.param([{"relativePath": "a.jpg", "lastModified": -5}], id="negative-mtime"),
        pytest.param(["a.jpg"], id="non-object-entry"),
    ],
)
def test_browser_invalid_photos(client, photos):
    response = client.post("/api/sessions", json=browser_payload(photos))
    assert response.status_code == 400, response.text
    assert error_code(response) == "INVALID_PHOTOS"
    assert response.json()["detail"]["message"]
    # 校验失败不应创建会话
    assert client.get("/api/sessions/current").json() == {"session": None}


def test_browser_photos_not_a_list(client):
    response = client.post("/api/sessions", json=browser_payload({"a.jpg": 1}))
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PHOTOS"


def test_browser_missing_photos_key(client):
    payload = {"source": "browser", "label": DEFAULT_LABEL}
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PHOTOS"


def test_browser_empty_photos(client):
    response = client.post("/api/sessions", json=browser_payload([]))
    assert response.status_code == 400
    assert error_code(response) == "NO_PHOTOS"


def test_browser_too_many_photos(client, monkeypatch):
    monkeypatch.setattr(scanner, "MAX_BROWSER_PHOTOS", 3)
    response = client.post(
        "/api/sessions",
        json=browser_payload(
            [{"relativePath": f"{index}.jpg"} for index in range(4)]
        ),
    )
    assert response.status_code == 400
    assert error_code(response) == "TOO_MANY_PHOTOS"


def test_unknown_source_is_rejected(client):
    response = client.post(
        "/api/sessions",
        json={"source": "ftp", "label": DEFAULT_LABEL, "photos": [{"relativePath": "a.jpg"}]},
    )
    assert response.status_code == 422
    assert error_code(response) == "INVALID_SOURCE"


# ---- 分页 / viewed / pick / 多轮 -------------------------------------------


def test_browser_pagination(client):
    photos = [{"relativePath": f"{name}.jpg", "sizeBytes": index} for index, name in enumerate("abcdef")]
    created = create_browser(client, photos)

    all_photos = listing(client, created["id"], limit=100)["photos"]
    assert [photo["relativePath"] for photo in all_photos] == [f"{name}.jpg" for name in "abcdef"]

    page = listing(client, created["id"], offset=1, limit=2)
    assert page["offset"] == 1
    assert page["limit"] == 2
    assert [photo["id"] for photo in page["photos"]] == [
        browser_id("b.jpg"),
        browser_id("c.jpg"),
    ]
    empty = listing(client, created["id"], offset=100, limit=5)
    assert empty["photos"] == []


def test_browser_viewed_and_pick_counts(client):
    session = create_browser(client)
    sid = session["id"]
    photos = photos_by_name(listing(client, sid)["photos"])
    first = photos["a.jpg"]["id"]
    second = photos["b.jpg"]["id"]

    assert view(client, sid, first)["viewedCount"] == 1
    assert view(client, sid, first)["viewedCount"] == 1  # 幂等
    assert view(client, sid, second)["viewedCount"] == 2

    assert pick(client, sid, first)["pickCount"] == 1
    assert pick(client, sid, first)["pickCount"] == 1  # 幂等
    assert pick(client, sid, second)["pickCount"] == 2
    assert unpick(client, sid, first)["pickCount"] == 1

    listed = photos_by_name(listing(client, sid)["photos"])
    assert listed["a.jpg"]["viewed"] is True
    assert listed["a.jpg"]["picked"] is False
    assert listed["b.jpg"]["picked"] is True
    assert listed["c.jpeg"]["viewed"] is False


def test_browser_multi_round_and_finish_last_round(client):
    photos = [{"relativePath": name} for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg")]
    session = create_browser(client, photos)
    sid = session["id"]
    round1 = photos_by_name(listing(client, sid)["photos"])

    # 第 1 轮按 c → a → b 的顺序 pick（与放映顺序不同）
    pick(client, sid, round1["c.jpg"]["id"])
    pick(client, sid, round1["a.jpg"]["id"])
    pick(client, sid, round1["b.jpg"]["id"])
    assert next_round(client, sid).status_code == 200

    round2_payload = listing(client, sid, 2)
    round2 = photos_by_name(round2_payload["photos"])
    assert round2_payload["total"] == 3
    # 新一轮顺序 = 上一轮照片顺序（a, b, c），而不是 pick 顺序
    assert [photo["relativePath"] for photo in round2_payload["photos"]] == ["a.jpg", "b.jpg", "c.jpg"]
    assert [photo["pickCount"] for photo in round2_payload["photos"]] == [1, 1, 1]

    # 第 2 轮按 c → a 的顺序 pick；finish 不需要进入第 3 轮
    pick(client, sid, round2["c.jpg"]["id"])
    pick(client, sid, round2["a.jpg"]["id"])

    response = finish(client, sid)
    assert response.status_code == 200
    payload = response.json()
    assert payload["savedPath"] is None
    result = payload["result"]

    assert result["origin"] == "browser"
    assert result["sourceDirectory"] == DEFAULT_LABEL
    assert result["schemaVersion"] == 1
    assert result["totalPhotos"] == 4
    assert result["roundsPlayed"] == 2
    assert result["pickedPhotoCount"] == 3

    # lastRound = 最后一轮（第 2 轮）仍保留的照片，顺序与该轮照片顺序一致
    assert result["lastRound"] == {
        "index": 2,
        "pickedCount": 2,
        "pickedPhotoIds": [round2["a.jpg"]["id"], round2["c.jpg"]["id"]],
    }

    # browser 来源没有绝对路径
    assert all(photo["absolutePath"] == "" for photo in result["photos"])
    by_relative = {photo["relativePath"]: photo for photo in result["photos"]}
    assert by_relative["a.jpg"]["pickCount"] == 2
    assert by_relative["a.jpg"]["pickedInRounds"] == [1, 2]
    assert by_relative["c.jpg"]["pickCount"] == 2
    assert by_relative["c.jpg"]["pickedInRounds"] == [1, 2]
    assert by_relative["b.jpg"]["pickCount"] == 1
    assert by_relative["b.jpg"]["pickedInRounds"] == [1]
    assert by_relative["d.jpg"]["pickCount"] == 0

    # 排序：pickCount desc, relativePath asc
    assert [photo["relativePath"] for photo in result["photos"]] == [
        "a.jpg",
        "c.jpg",
        "b.jpg",
        "d.jpg",
    ]
    assert get_summary(client, sid)["phase"] == "finished"
    # finish 幂等
    assert finish(client, sid).json()["result"] == result


def test_browser_last_round_before_finish_and_when_empty(client):
    photos = [{"relativePath": name} for name in ("a.jpg", "b.jpg", "c.jpg")]
    session = create_browser(client, photos)
    sid = session["id"]

    # 未 pick 时 finish：lastRound 为空数组
    result = finish(client, sid).json()["result"]
    assert result["lastRound"] == {"index": 1, "pickedCount": 0, "pickedPhotoIds": []}

    # 新会话：未 finish 调用 /result 也带 lastRound（当前轮）
    session = create_browser(client, photos)
    sid = session["id"]
    listed = photos_by_name(listing(client, sid)["photos"])
    pick(client, sid, listed["b.jpg"]["id"])
    result = client.get(f"/api/sessions/{sid}/result").json()
    assert result["lastRound"] == {
        "index": 1,
        "pickedCount": 1,
        "pickedPhotoIds": [listed["b.jpg"]["id"]],
    }
    assert get_summary(client, sid)["phase"] == "picking"


# ---- 图片接口 --------------------------------------------------------------


def test_browser_image_returns_404(client):
    session = create_browser(client)
    sid = session["id"]
    photo_id = listing(client, sid)["photos"][0]["id"]

    for variant in ("original", "thumb"):
        response = client.get(
            f"/api/sessions/{sid}/photos/{photo_id}/image",
            params={"variant": variant},
        )
        assert response.status_code == 404
        assert error_code(response) == "PHOTO_NOT_FOUND"
        assert "浏览器" in response.json()["detail"]["message"]

    # 未知照片 / 未知会话
    response = client.get(f"/api/sessions/{sid}/photos/deadbeefdeadbeef/image")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"

    response = client.get("/api/sessions/nope/photos/deadbeefdeadbeef/image")
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


# ---- 下载 / 导出 -----------------------------------------------------------


def test_browser_result_download_and_export(client, tmp_path):
    session = create_browser(client)
    sid = session["id"]
    listed = photos_by_name(listing(client, sid)["photos"])
    pick(client, sid, listed["a.jpg"]["id"])
    finish(client, sid)

    download = client.get(f"/api/sessions/{sid}/result", params={"download": 1})
    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    encoded_label = urllib.parse.quote(DEFAULT_LABEL)
    assert encoded_label in disposition
    downloaded = download.json()
    assert downloaded["origin"] == "browser"
    assert downloaded["lastRound"]["pickedCount"] == 1

    target = tmp_path / "browser-result.json"
    response = client.post(f"/api/sessions/{sid}/export", json={"path": str(target)})
    assert response.status_code == 200, response.text
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert exported == downloaded
    assert all(photo["absolutePath"] == "" for photo in exported["photos"])


# ---- server 来源不回归 ------------------------------------------------------


def test_server_origin_fields_and_last_round_not_regressed(client, library):
    response = client.post("/api/sessions", json={"directory": str(library), "recursive": True})
    assert response.status_code == 201
    session = response.json()
    assert session["origin"] == "server"
    assert os.path.normcase(session["sourceLabel"]) == os.path.normcase(os.path.abspath(str(library)))
    assert os.path.normcase(session["root"]) == os.path.normcase(session["sourceLabel"])

    sid = session["id"]
    photos = photos_by_name(listing(client, sid)["photos"])
    first = photos["a.jpg"]
    pick(client, sid, first["id"])
    pick(client, sid, photos["b.JPG"]["id"])

    result = finish(client, sid).json()["result"]
    assert result["origin"] == "server"
    assert os.path.normcase(result["sourceDirectory"]) == os.path.normcase(os.path.abspath(str(library)))
    assert result["lastRound"] == {
        "index": 1,
        "pickedCount": 2,
        "pickedPhotoIds": [first["id"], photos["b.JPG"]["id"]],
    }
    absolute = {photo["relativePath"]: photo["absolutePath"] for photo in result["photos"]}
    assert os.path.isabs(absolute["a.jpg"])
    assert os.path.normcase(absolute["a.jpg"]) == os.path.normcase(str(library / "a.jpg"))

    # server 来源图片接口保持可用
    image = client.get(f"/api/sessions/{sid}/photos/{first['id']}/image")
    assert image.status_code == 200
