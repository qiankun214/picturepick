"""AI 识鸟：参考照片获取测试（由大模型 API 提供真实图片直链，docs/BIRDID-CONTRACT.md §4.4）。"""

from __future__ import annotations

import io
import json

from PIL import Image

from picturepick.birdid.parser import ParsedIdentification, parse_model_output
from picturepick.birdid.reference import ReferenceImage, resolve_reference, shrink_image

IDENTIFIED = {
    "identified": True,
    "family": "鹭科",
    "familyLatin": "Ardeidae",
    "genus": "白鹭属",
    "genusLatin": "Egretta",
    "species": "白鹭",
    "speciesLatin": "Egretta garzetta",
    "confidence": 0.9,
    "referenceImageUrl": "https://static.example.com/birds/egret.jpg",
    "referenceSource": "中国鸟类图库",
}


def jpeg_bytes(size=(800, 600), color=(20, 80, 120)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def parsed(**overrides) -> ParsedIdentification:
    payload = dict(IDENTIFIED)
    payload.update(overrides)
    return parse_model_output(json.dumps(payload, ensure_ascii=False))


class FakeGetter:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"未预置的 URL: {url}")


def test_model_provided_reference_url_is_downloaded():
    getter = FakeGetter({"static.example.com": ("image/jpeg", jpeg_bytes())})
    reference = resolve_reference(parsed(), http_get=getter)
    assert isinstance(reference, ReferenceImage)
    assert reference.data[:2] == b"\xff\xd8"
    assert reference.source_label == "中国鸟类图库"
    assert reference.source_url.endswith("egret.jpg")
    assert getter.calls == ["https://static.example.com/birds/egret.jpg"]


def test_missing_reference_url_returns_none_without_network():
    getter = FakeGetter({})
    assert resolve_reference(parsed(referenceImageUrl=""), http_get=getter) is None
    assert getter.calls == []


def test_not_identified_returns_none():
    getter = FakeGetter({})
    assert resolve_reference(parsed(identified=False), http_get=getter) is None
    assert getter.calls == []


def test_private_ip_reference_url_is_rejected():
    from picturepick.birdid.reference import _is_safe_public_url

    assert _is_safe_public_url("https://static.example.com/x.jpg") is True
    assert _is_safe_public_url("http://127.0.0.1/x.jpg") is False
    assert _is_safe_public_url("http://localhost/x.jpg") is False
    assert _is_safe_public_url("http://192.168.1.10/x.jpg") is False
    assert _is_safe_public_url("file:///C:/x.jpg") is False
    getter = FakeGetter({})
    assert resolve_reference(
        parsed(referenceImageUrl="http://169.254.169.254/latest/meta-data"), http_get=getter
    ) is None
    assert getter.calls == []


def test_reference_bad_content_type_is_rejected():
    getter = FakeGetter({"static.example.com": ("text/html", b"<html>no</html>")})
    assert resolve_reference(parsed(), http_get=getter) is None


def test_reference_download_failure_returns_none():
    getter = FakeGetter({"static.example.com": OSError("network down")})
    assert resolve_reference(parsed(), http_get=getter) is None


def test_shrink_image_downscales_and_reencodes():
    data = jpeg_bytes(size=(2400, 1200))
    shrunk = shrink_image(data)
    assert shrunk is not None
    payload, content_type = shrunk
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(payload)) as image:
        assert max(image.size) <= 1000


def test_shrink_image_rejects_garbage():
    assert shrink_image(b"not an image") is None


def test_wikimedia_url_is_skipped():
    getter = FakeGetter({"upload.wikimedia.org": ("image/jpeg", jpeg_bytes())})
    reference = resolve_reference(
        parsed(referenceImageUrl="https://upload.wikimedia.org/wikipedia/commons/egret.jpg"),
        http_get=getter,
    )
    assert reference is None
    assert getter.calls == []


def test_source_label_falls_back_when_model_omits_it():
    getter = FakeGetter({"static.example.com": ("image/jpeg", jpeg_bytes())})
    reference = resolve_reference(parsed(referenceSource=""), http_get=getter)
    assert reference is not None
    assert reference.source_label == "大模型提供的参考图"
