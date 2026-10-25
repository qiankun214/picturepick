"""本轮照片分页与图片接口测试。"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from helpers import (
    create_session,
    error_code,
    expected_photo_id,
    list_round_photos,
    make_jpeg,
    photos_by_name,
)
from picturepick.imaging import CACHE_MAX_ENTRIES, ThumbnailCache, cache


def test_round_photos_pagination_and_fields(client, library):
    session = create_session(client, library)
    payload = list_round_photos(client, session["id"], limit=1000)

    assert payload["roundIndex"] == 1
    assert payload["total"] == 6
    assert payload["offset"] == 0
    assert payload["limit"] == 1000
    photos = payload["photos"]
    assert len(photos) == 6
    assert [photo["relativePath"] for photo in photos] == [
        "a.jpg",
        "b.JPG",
        "c.jpeg",
        "sub/d.jpg",
        "sub/e.JPEG",
        "sub/nested/f.jpg",
    ]
    assert len({photo["id"] for photo in photos}) == 6
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
        assert photo["name"].lower().endswith((".jpg", ".jpeg"))
        assert photo["sizeBytes"] > 0
        assert photo["pickCount"] == 0
        assert photo["picked"] is False
        assert photo["viewed"] is False
        assert photo["id"] == expected_photo_id(library / photo["relativePath"])


def test_round_photos_offset_and_limit(client, library):
    session = create_session(client, library)
    all_photos = list_round_photos(client, session["id"])["photos"]

    page = list_round_photos(client, session["id"], offset=1, limit=2)
    assert page["offset"] == 1
    assert page["limit"] == 2
    assert len(page["photos"]) == 2
    assert page["photos"][0]["id"] == all_photos[1]["id"]
    assert page["photos"][1]["id"] == all_photos[2]["id"]

    tail = list_round_photos(client, session["id"], offset=5, limit=10)
    assert len(tail["photos"]) == 1
    assert tail["photos"][0]["id"] == all_photos[5]["id"]

    empty = list_round_photos(client, session["id"], offset=100, limit=10)
    assert empty["photos"] == []
    assert empty["total"] == 6

    default_page = list_round_photos(client, session["id"])
    assert default_page["limit"] == 500
    assert default_page["offset"] == 0


def test_round_not_found(client, library):
    session = create_session(client, library)
    for index in (0, 2, 99):
        response = client.get(f"/api/sessions/{session['id']}/rounds/{index}/photos")
        assert response.status_code == 404, index
        assert error_code(response) == "ROUND_NOT_FOUND"


def test_image_original_returns_file_bytes(client, library):
    session = create_session(client, library)
    photos = photos_by_name(list_round_photos(client, session["id"]))
    target = library / "a.jpg"

    response = client.get(f"/api/sessions/{session['id']}/photos/{photos['a.jpg']['id']}/image")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content == target.read_bytes()
    assert response.content[:2] == b"\xff\xd8"


def test_image_throws_thumbnail(client, tmp_path):
    root = tmp_path / "big"
    make_jpeg(root / "big.jpg", size=(1200, 800), color=(90, 140, 60))
    session = create_session(client, root)
    photo = list_round_photos(client, session["id"])["photos"][0]

    original = client.get(f"/api/sessions/{session['id']}/photos/{photo['id']}/image")
    thumbnail = client.get(
        f"/api/sessions/{session['id']}/photos/{photo['id']}/image",
        params={"variant": "thumb"},
    )
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/jpeg")
    image = Image.open(io.BytesIO(thumbnail.content))
    assert max(image.size) <= 512
    assert image.size[0] / image.size[1] == pytest.approx(1200 / 800, rel=0.02)
    assert len(thumbnail.content) < len(original.content)

    # 第二次命中缓存，内容一致
    again = client.get(
        f"/api/sessions/{session['id']}/photos/{photo['id']}/image",
        params={"variant": "thumb"},
    )
    assert again.content == thumbnail.content


def test_image_unknown_photo_and_session(client, library):
    session = create_session(client, library)
    response = client.get(f"/api/sessions/{session['id']}/photos/deadbeefdeadbeef/image")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"

    response = client.get("/api/sessions/nope/photos/deadbeefdeadbeef/image")
    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_image_photo_from_other_session(client, library, other_library):
    session_a = create_session(client, library)
    session_b = create_session(client, other_library)
    photo_a = list_round_photos(client, session_a["id"])["photos"][0]

    response = client.get(f"/api/sessions/{session_b['id']}/photos/{photo_a['id']}/image")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"


def test_image_missing_file_on_disk(client, library):
    session = create_session(client, library)
    photo = photos_by_name(list_round_photos(client, session["id"]))["a.jpg"]
    (library / "a.jpg").unlink()

    response = client.get(f"/api/sessions/{session['id']}/photos/{photo['id']}/image")
    assert response.status_code == 404
    assert error_code(response) == "PHOTO_NOT_FOUND"


def test_image_invalid_variant(client, library):
    session = create_session(client, library)
    photo = list_round_photos(client, session["id"])["photos"][0]
    response = client.get(
        f"/api/sessions/{session['id']}/photos/{photo['id']}/image",
        params={"variant": "huge"},
    )
    assert response.status_code == 422


def test_thumbnail_cache_lru():
    lru = ThumbnailCache(max_entries=2)
    key_a = ("a", 1, 1, 512)
    key_b = ("b", 1, 1, 512)
    key_c = ("c", 1, 1, 512)

    lru.put(key_a, b"a")
    lru.put(key_b, b"b")
    assert lru.get(key_a) == b"a"  # 访问 a，使其变为最近使用
    assert len(lru) == 2

    lru.put(key_c, b"c")  # 淘汰最久未用的 b
    assert lru.get(key_b) is None
    assert lru.get(key_a) == b"a"
    assert lru.get(key_c) == b"c"
    assert CACHE_MAX_ENTRIES >= 2

    cache().clear()
    assert len(cache()) == 0
