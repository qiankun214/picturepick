"""AI 识鸟：方舟客户端与模拟客户端测试（docs/BIRDID-CONTRACT.md §4、§9）。"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from picturepick.birdid.client import (
    BirdClientError,
    MockBirdClient,
    ArkBirdClient,
    build_client,
    mock_reference_image,
    prepare_image_bytes,
)
from picturepick.birdid.config import BirdConfig
from picturepick.birdid.parser import parse_model_output
from picturepick.models import BirdResult


def make_config(**overrides) -> BirdConfig:
    values = dict(
        api_key="ark-test-key",
        endpoint="https://ark.example.com/api/v3/chat/completions",
        model="doubao-seed-2-1-lite",
        mock=False,
        source="env",
        mock_delay=0.0,
        mock_fail=False,
    )
    values.update(overrides)
    return BirdConfig(**values)


def completion(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]},
        ensure_ascii=False,
    ).encode("utf-8")


def responses_completion(content: str, *, with_reasoning: bool = True) -> bytes:
    output = []
    if with_reasoning:
        output.append({"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]})
    output.append(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
        }
    )
    return json.dumps({"object": "response", "output": output}, ensure_ascii=False).encode("utf-8")


def jpeg_bytes(size=(32, 24)) -> bytes:
    image = Image.new("RGB", size, (10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


VALID_CONTENT = json.dumps(
    {
        "identified": True,
        "family": "鹭科",
        "familyLatin": "Ardeidae",
        "genus": "白鹭属",
        "genusLatin": "Egretta",
        "species": "白鹭",
        "speciesLatin": "Egretta garzetta",
        "commonNameEn": "Little Egret",
        "confidence": 0.9,
        "note": "白色羽毛",
        "alternatives": [],
        "referenceImageUrl": "https://upload.wikimedia.org/x.jpg",
        "referenceSource": "Wikimedia Commons",
        "referenceNote": "参考图",
    },
    ensure_ascii=False,
)


class FakePost:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": json.loads(body.decode("utf-8")),
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("没有更多预置响应")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_client(responses, **overrides):
    fake = FakePost(responses)
    sleeps = []
    config = make_config(**overrides)
    client = ArkBirdClient(config, http_post=fake, sleep=sleeps.append)
    return client, fake, sleeps


def test_identify_sends_multimodal_request_and_parses():
    client, fake, _sleeps = make_client([(200, completion(VALID_CONTENT))])
    parsed = client.identify(jpeg_bytes(), seed="photo-1")
    assert parsed.result.species == "白鹭"
    assert parsed.reference_image_url.startswith("https://upload.wikimedia.org/")

    call = fake.calls[0]
    assert call["url"] == "https://ark.example.com/api/v3/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer ark-test-key"
    assert call["payload"]["model"] == "doubao-seed-2-1-lite"
    assert call["payload"]["thinking"] == {"type": "disabled"}
    user_content = call["payload"]["messages"][1]["content"]
    image_part = user_content[0]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert user_content[1]["type"] == "text"


def test_responses_endpoint_uses_responses_payload_and_parses_output():
    client, fake, _sleeps = make_client(
        [(200, responses_completion(VALID_CONTENT))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    parsed = client.identify(jpeg_bytes())
    assert parsed.result.species == "白鹭"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://ark.example.com/api/v3/responses"
    payload = call["payload"]
    assert payload["instructions"]
    assert "messages" not in payload
    assert payload["max_output_tokens"] == 1200
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["tools"] == [{"type": "web_search"}]
    content = payload["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/jpeg;base64,")
    assert content[1]["type"] == "input_text"
    assert content[1]["text"]


def test_responses_verify_uses_input_and_max_output_tokens():
    client, fake, _sleeps = make_client(
        [(200, responses_completion("ok", with_reasoning=False))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    ok, message, latency = client.verify()
    assert ok is True
    assert "Responses API" in message
    assert isinstance(latency, int)
    payload = fake.calls[0]["payload"]
    assert isinstance(payload["input"], str)
    assert payload["max_output_tokens"] == 16
    assert "messages" not in payload


def test_responses_empty_output_reports_api_error():
    client, _fake, _sleeps = make_client(
        [(200, json.dumps({"output": []}).encode())],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_API_ERROR"
    assert "output" in info.value.message


def test_responses_web_search_not_activated_retries_without_tools():
    tool_not_open = (
        404,
        json.dumps(
            {
                "error": {
                    "code": "ToolNotOpen",
                    "message": "Your account has not activated web search.",
                }
            }
        ).encode("utf-8"),
    )
    client, fake, sleeps = make_client(
        [(tool_not_open), (200, responses_completion(VALID_CONTENT))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    parsed = client.identify(jpeg_bytes())
    assert parsed.result.species == "白鹭"
    assert len(fake.calls) == 2
    assert fake.calls[0]["payload"]["tools"] == [{"type": "web_search"}]
    assert "tools" not in fake.calls[1]["payload"]
    assert client.web_search_error and "web search" in client.web_search_error.lower()
    assert sleeps == []


def test_responses_web_search_works_keeps_tools():
    client, fake, _sleeps = make_client(
        [(200, responses_completion(VALID_CONTENT))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    parsed = client.identify(jpeg_bytes())
    assert parsed.result.species == "白鹭"
    assert fake.calls[0]["payload"]["tools"] == [{"type": "web_search"}]
    assert client.web_search_error is None


def test_request_reference_url_uses_web_search_and_parses():
    result = BirdResult(
        identified=True, species="白鹭", speciesLatin="Egretta garzetta", family="鹭科"
    )
    content = json.dumps(
        {
            "referenceImageUrl": "https://img.example.com/egret.jpg",
            "referenceSource": "中国鸟类图库",
            "referenceNote": "https://example.com/page",
        },
        ensure_ascii=False,
    )
    client, fake, _sleeps = make_client(
        [(200, responses_completion(content))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    found = client.request_reference_url(result)
    assert found is not None
    assert found["referenceImageUrl"] == "https://img.example.com/egret.jpg"
    assert found["referenceSource"] == "中国鸟类图库"
    payload = fake.calls[0]["payload"]
    assert payload["tools"] == [{"type": "web_search"}]
    assert "白鹭" in payload["input"]


def test_request_reference_url_regex_fallback():
    result = BirdResult(identified=True, species="白鹭")
    content = "参考图片：https://img.example.com/egret.jpg 请查看。"
    client, _fake, _sleeps = make_client(
        [(200, responses_completion(content))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    found = client.request_reference_url(result)
    assert found is not None
    assert found["referenceImageUrl"] == "https://img.example.com/egret.jpg"


def test_request_reference_url_not_identified_returns_none():
    client, fake, _sleeps = make_client([], endpoint="https://ark.example.com/api/v3/responses")
    assert client.request_reference_url(BirdResult(identified=False)) is None
    assert fake.calls == []


def test_request_reference_url_toolnotopen_falls_back_without_tools():
    result = BirdResult(identified=True, species="白鹭")
    tool_not_open = (
        404,
        json.dumps(
            {"error": {"code": "ToolNotOpen", "message": "Your account has not activated web search."}}
        ).encode(),
    )
    content = json.dumps({"referenceImageUrl": "https://img.example.com/egret.jpg"}).encode()
    client, fake, _sleeps = make_client(
        [tool_not_open, (200, responses_completion(content.decode()))],
        endpoint="https://ark.example.com/api/v3/responses",
    )
    found = client.request_reference_url(result)
    assert found is not None
    assert len(fake.calls) == 2
    assert fake.calls[0]["payload"]["tools"] == [{"type": "web_search"}]
    assert "tools" not in fake.calls[1]["payload"]
    assert client.web_search_error


def test_unsupported_endpoint_is_rejected_before_request():
    client, fake, _sleeps = make_client([], endpoint="https://ark.example.com/api/v3")
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_API_ERROR"
    assert "/chat/completions" in info.value.message
    assert fake.calls == []

    ok, message, latency = client.verify()
    assert ok is False
    assert "/chat/completions" in message
    assert latency is None


def test_thinking_rejected_retries_without_thinking():
    rejected = (
        400,
        json.dumps({"error": {"message": "unknown field thinking"}}).encode("utf-8"),
    )
    client, fake, sleeps = make_client([rejected, (200, completion(VALID_CONTENT))])
    parsed = client.identify(jpeg_bytes())
    assert parsed.result.species == "白鹭"
    assert len(fake.calls) == 2
    assert "thinking" in fake.calls[0]["payload"]
    assert "thinking" not in fake.calls[1]["payload"]
    assert sleeps == []


def test_server_error_retries_once():
    client, fake, sleeps = make_client(
        [(500, b'{"error":{"message":"internal"}}'), (200, completion(VALID_CONTENT))]
    )
    parsed = client.identify(jpeg_bytes())
    assert parsed.result.species == "白鹭"
    assert len(fake.calls) == 2
    assert len(sleeps) == 1


def test_unauthorized_does_not_retry_and_hints_key():
    client, fake, sleeps = make_client(
        [(401, b'{"error":{"message":"invalid api key"}}')]
    )
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_API_ERROR"
    assert "401" in info.value.message
    assert "API Key" in info.value.message
    assert len(fake.calls) == 1
    assert sleeps == []


def test_404_reports_request_url_and_base_url_hint():
    client, fake, _sleeps = make_client(
        [
            (
                404,
                b'{"error":{"message":"The specified action is invalid: '
                b'/api/v3/responses/chat/completions"}}',
            )
        ]
    )
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_API_ERROR"
    assert "请求地址" in info.value.message
    assert "/chat/completions" in info.value.message
    assert "/api/v3" in info.value.message
    assert len(fake.calls) == 1


def test_timeout_retries_then_reports_timeout():
    client, fake, sleeps = make_client(
        [
            BirdClientError("BIRD_API_TIMEOUT", "timeout"),
            BirdClientError("BIRD_API_TIMEOUT", "timeout"),
        ]
    )
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_API_TIMEOUT"
    assert len(fake.calls) == 2
    assert len(sleeps) == 1


def test_invalid_model_output_reports_parse_error():
    client, _fake, _sleeps = make_client([(200, completion("我不知道这是什么鸟"))])
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_PARSE_ERROR"


def test_missing_choices_reports_api_error():
    client, _fake, _sleeps = make_client([(200, b'{"id":"x"}')])
    with pytest.raises(BirdClientError) as info:
        client.identify(jpeg_bytes())
    assert info.value.code == "BIRD_API_ERROR"
    assert "choices" in info.value.message


def test_verify_success_and_failure():
    client, _fake, _sleeps = make_client([(200, completion("ok"))])
    ok, message, latency = client.verify()
    assert ok is True
    assert "doubao-seed-2-1-lite" in message
    assert isinstance(latency, int)

    client, _fake, _sleeps = make_client([(403, b'{"error":{"message":"forbidden"}}')])
    ok, message, latency = client.verify()
    assert ok is False
    assert "403" in message
    assert latency is None


def test_prepare_image_resizes_long_edge():
    image = Image.new("RGB", (3000, 1000), (10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    prepared = prepare_image_bytes(buffer.getvalue())
    with Image.open(io.BytesIO(prepared)) as result:
        assert max(result.size) <= 1568
        assert result.format == "JPEG"


def test_build_client_selects_mock_or_ark():
    assert isinstance(build_client(make_config(mock=True)), MockBirdClient)
    assert isinstance(build_client(make_config(mock=False)), ArkBirdClient)


def test_mock_client_is_deterministic():
    config = make_config(mock=True, mock_delay=0)
    client = MockBirdClient(config, sleep=lambda _seconds: None)
    first = client.identify(b"x", seed="photo-a")
    again = client.identify(b"x", seed="photo-a")
    assert first.result.species == again.result.species
    assert first.result.identified is True
    assert first.result.confidence is not None
    assert "模拟" in first.result.note
    assert first.result.family and first.result.genus and first.result.species


def test_mock_client_notbird_and_failure():
    config = make_config(mock=True, mock_delay=0)
    client = MockBirdClient(config, sleep=lambda _seconds: None)
    parsed = client.identify(b"x", seed="notbird-001.jpg")
    assert parsed.result.identified is False
    assert parsed.result.reason

    failing = MockBirdClient(make_config(mock=True, mock_fail=True), sleep=lambda _seconds: None)
    with pytest.raises(BirdClientError) as info:
        failing.identify(b"x", seed="a")
    assert info.value.code == "BIRD_API_ERROR"
    assert "模拟失败" in info.value.message


def test_mock_reference_image_is_jpeg():
    parsed = parse_model_output(VALID_CONTENT)
    reference = mock_reference_image(parsed)
    assert reference.data[:2] == b"\xff\xd8"
    with Image.open(io.BytesIO(reference.data)) as image:
        assert image.format == "JPEG"
    assert reference.source_label == "Mock Reference"


# ---- 真实 urllib 链路（本地 HTTP 服务器，不访问外网） ------------------------


def _run_local_server(status: int = 200):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    requests: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server 接口
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            requests.append(
                {"path": self.path, "headers": dict(self.headers), "payload": json.loads(body)}
            )
            if status >= 400:
                payload = json.dumps({"error": {"message": "boom"}}).encode()
            else:
                payload = completion(VALID_CONTENT)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):  # pragma: no cover - 静默
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, requests


def test_real_http_post_path_sends_request():
    server, requests = _run_local_server(200)
    try:
        port = server.server_address[1]
        client = ArkBirdClient(
            make_config(endpoint=f"http://127.0.0.1:{port}/v1/chat/completions")
        )
        parsed = client.identify(jpeg_bytes())
        assert parsed.result.species == "白鹭"
        assert len(requests) == 1
        request = requests[0]
        assert request["path"] == "/v1/chat/completions"
        assert request["headers"]["Authorization"] == "Bearer ark-test-key"
        assert request["payload"]["model"] == "doubao-seed-2-1-lite"
    finally:
        server.shutdown()
        server.server_close()


def test_real_http_post_error_status_is_reported():
    server, requests = _run_local_server(401)
    try:
        port = server.server_address[1]
        client = ArkBirdClient(
            make_config(endpoint=f"http://127.0.0.1:{port}/v1/chat/completions")
        )
        with pytest.raises(BirdClientError) as info:
            client.identify(jpeg_bytes())
        assert info.value.code == "BIRD_API_ERROR"
        assert "401" in info.value.message
    finally:
        server.shutdown()
        server.server_close()

