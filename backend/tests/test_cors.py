"""CORS 测试：仅允许 localhost / 127.0.0.1。"""

from __future__ import annotations


def test_local_origins_are_allowed(client):
    for origin in (
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost",
        "https://127.0.0.1:1234",
    ):
        response = client.get("/api/health", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin


def test_external_origin_is_not_allowed(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_preflight(client):
    response = client.options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-methods" in response.headers
    assert response.headers.get("access-control-expose-headers", "").lower() in ("", "content-disposition")
