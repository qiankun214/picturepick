"""AI 识鸟：配置/状态/验证接口测试（docs/BIRDID-CONTRACT.md §2、§6.1）。

v5.1 起配置项是**完整 API 端点 URL**（/chat/completions 或 /responses），
后端不拼接路径；这里覆盖端点校验、协议识别与旧配置兼容。
"""

from __future__ import annotations

import json

from helpers import error_code
from picturepick.birdid.config import config_path, load_settings, mask_key

DEFAULT_MODEL = "doubao-seed-2-1-lite"
DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
RESPONSES_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/responses"


def test_status_defaults_unconfigured(client):
    payload = client.get("/api/bird/status").json()
    assert payload == {
        "configured": False,
        "model": DEFAULT_MODEL,
        "endpoint": DEFAULT_ENDPOINT,
        "endpointProtocol": "chat",
        "endpointWarning": None,
        "keyMasked": None,
        "keySource": None,
        "mock": False,
        "queue": {"queued": 0, "running": 0},
    }


def test_update_config_saves_key_and_endpoint(client):
    response = client.put(
        "/api/bird/config",
        json={
            "apiKey": "ark-1234567890abcd",
            "endpoint": RESPONSES_ENDPOINT,
            "model": "doubao-seed-2-1-lite",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["configured"] is True
    assert payload["keySource"] == "config"
    assert payload["keyMasked"] == "ark-…abcd"
    assert payload["endpoint"] == RESPONSES_ENDPOINT
    assert payload["endpointProtocol"] == "responses"
    assert payload["endpointWarning"] is None
    assert "ark-1234567890abcd" not in response.text

    stored = load_settings()
    assert stored["arkApiKey"] == "ark-1234567890abcd"
    assert stored["arkEndpoint"] == RESPONSES_ENDPOINT
    assert "arkBaseUrl" not in stored
    assert config_path().is_file()


def test_endpoint_trailing_slash_is_trimmed(client):
    response = client.put(
        "/api/bird/config",
        json={"endpoint": "https://example.com/api/v3/chat/completions/"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["endpoint"] == "https://example.com/api/v3/chat/completions"
    assert response.json()["endpointProtocol"] == "chat"


def test_endpoint_without_api_suffix_is_rejected(client):
    for bad in (
        "https://ark.cn-beijing.volces.com/api/v3",
        "https://ark.cn-beijing.volces.com/api/v3/responses/chat/completions",
        "ftp://example.com/api/v3/chat/completions",
        "https://",
    ):
        response = client.put("/api/bird/config", json={"endpoint": bad})
        assert response.status_code == 400, f"{bad} -> {response.text}"
        assert error_code(response) == "INVALID_CONFIG"


def test_clear_endpoint_restores_default(client):
    client.put("/api/bird/config", json={"endpoint": RESPONSES_ENDPOINT})
    payload = client.put("/api/bird/config", json={"endpoint": ""}).json()
    assert payload["endpoint"] == DEFAULT_ENDPOINT
    assert payload["endpointProtocol"] == "chat"


def test_env_key_overrides_config_file(client, monkeypatch):
    client.put("/api/bird/config", json={"apiKey": "config-key-0000"})
    monkeypatch.setenv("PICTUREPICK_ARK_API_KEY", "env-key-11112222")
    payload = client.get("/api/bird/status").json()
    assert payload["keySource"] == "env"
    assert payload["keyMasked"] == "env-…2222"


def test_env_endpoint_and_model_override(client, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_ARK_ENDPOINT", "https://ark.example.com/api/v3/responses")
    monkeypatch.setenv("PICTUREPICK_ARK_MODEL", "ep-20261025-abcdef")
    payload = client.get("/api/bird/status").json()
    assert payload["endpoint"] == "https://ark.example.com/api/v3/responses"
    assert payload["endpointProtocol"] == "responses"
    assert payload["model"] == "ep-20261025-abcdef"


def test_legacy_base_url_env_is_used_as_endpoint(client, monkeypatch):
    monkeypatch.setenv(
        "PICTUREPICK_ARK_BASE_URL", "https://ark.example.com/api/v3/responses"
    )
    payload = client.get("/api/bird/status").json()
    assert payload["endpoint"] == "https://ark.example.com/api/v3/responses"
    assert payload["endpointProtocol"] == "responses"


def test_legacy_config_field_is_read_as_endpoint(client):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "arkApiKey": "ark-legacy-1234",
                "arkBaseUrl": "https://ark.cn-beijing.volces.com/api/v3/responses",
                "arkModel": "doubao-seed-2-1-lite",
            }
        ),
        encoding="utf-8",
    )
    payload = client.get("/api/bird/status").json()
    assert payload["configured"] is True
    assert payload["endpoint"] == "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert payload["endpointProtocol"] == "responses"


def test_legacy_base_only_config_reports_warning(client):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "arkApiKey": "ark-legacy-1234",
                "arkBaseUrl": "https://ark.cn-beijing.volces.com/api/v3",
            }
        ),
        encoding="utf-8",
    )
    payload = client.get("/api/bird/status").json()
    assert payload["configured"] is True
    assert payload["endpoint"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert payload["endpointProtocol"] is None
    assert payload["endpointWarning"]
    assert "/chat/completions" in payload["endpointWarning"]


def test_corrupt_config_file_is_ignored(client):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    payload = client.get("/api/bird/status").json()
    assert payload["configured"] is False
    assert payload["keySource"] is None
    assert payload["endpoint"] == DEFAULT_ENDPOINT


def test_verify_reports_missing_configuration(client):
    payload = client.post("/api/bird/verify").json()
    assert payload["ok"] is False
    assert "尚未配置" in payload["message"]


def test_verify_mock_mode_succeeds(client, mock_bird):
    payload = client.post("/api/bird/verify").json()
    assert payload["ok"] is True
    assert payload["model"] == DEFAULT_MODEL
    assert payload["latencyMs"] == 0


def test_verify_mock_failure(client, mock_bird, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK_FAIL", "1")
    payload = client.post("/api/bird/verify").json()
    assert payload["ok"] is False
    assert "模拟" in payload["message"]


def test_verify_unsupported_endpoint(client, monkeypatch):
    monkeypatch.setenv("PICTUREPICK_ARK_API_KEY", "ark-test-1234")
    monkeypatch.setenv("PICTUREPICK_ARK_ENDPOINT", "https://ark.example.com/api/v3")
    payload = client.post("/api/bird/verify").json()
    assert payload["ok"] is False
    assert "/chat/completions" in payload["message"]


def test_mask_key_variants():
    assert mask_key("") is None
    assert mask_key("short") == "…rt"
    assert mask_key("ark-1234567890abcd") == "ark-…abcd"


def test_status_reports_mock_mode(client, mock_bird):
    payload = client.get("/api/bird/status").json()
    assert payload["configured"] is True
    assert payload["mock"] is True
    assert payload["keyMasked"] is None
    assert payload["keySource"] == "mock"


def test_env_mock_zero_overrides_config(client, monkeypatch):
    client.put("/api/bird/config", json={"mock": True})
    assert client.get("/api/bird/status").json()["mock"] is True
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK", "0")
    payload = client.get("/api/bird/status").json()
    assert payload["mock"] is False
    assert payload["configured"] is False


def test_env_mock_one_overrides_config(client, monkeypatch):
    client.put("/api/bird/config", json={"mock": False})
    monkeypatch.setenv("PICTUREPICK_BIRD_MOCK", "1")
    payload = client.get("/api/bird/status").json()
    assert payload["mock"] is True
    assert payload["configured"] is True


def test_saved_config_is_utf8_json(client):
    client.put(
        "/api/bird/config",
        json={"apiKey": "ark-中文-0000", "model": "m1", "endpoint": RESPONSES_ENDPOINT},
    )
    raw = config_path().read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["arkApiKey"] == "ark-中文-0000"
    assert payload["arkModel"] == "m1"
    assert payload["arkEndpoint"] == RESPONSES_ENDPOINT
