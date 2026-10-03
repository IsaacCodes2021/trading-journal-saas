import asyncio

import pytest
from fastapi import Request, UploadFile
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.api.limits import BodyLimitMiddleware, BodyTooLarge

JSON_LIMIT, UPLOAD_LIMIT = 1000, 5000


@pytest.fixture
def ran(tmp_path):
    return []


@pytest.fixture
def client(tmp_path, ran):
    config = ApiConfig(web_dist=tmp_path / "none", max_json_bytes=JSON_LIMIT, max_upload_bytes=UPLOAD_LIMIT)
    app = create_app(config)

    # POST routes, because the SPA's catch-all is a GET and would answer a GET to these paths first.
    @app.post("/api/v1/body")
    async def body(request: Request) -> dict[str, int]:
        ran.append("body")
        return {"size": len(await request.body())}

    @app.post("/api/v1/upload")
    async def upload(file: UploadFile) -> dict[str, int]:
        ran.append("upload")
        return {"size": len(await file.read())}

    return TestClient(app)


def post_body(client, size, **kwargs):
    return client.post("/api/v1/body", content=b"x" * size, headers={"content-type": "application/json"}, **kwargs)


def test_a_body_at_the_limit_passes_and_one_byte_more_does_not(client, ran):
    assert post_body(client, JSON_LIMIT).json() == {"size": JSON_LIMIT}
    response = post_body(client, JSON_LIMIT + 1)
    assert response.status_code == 413
    assert ran == ["body"]  # the second request never reached the handler


def test_a_declared_length_over_the_limit_is_refused_as_problem_json_with_the_security_headers(client, ran):
    response = post_body(client, JSON_LIMIT * 50)
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 413
    assert "content-security-policy" in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert ran == []


def test_a_chunked_body_is_counted_as_it_is_read(client, ran):
    # No Content-Length at all, so only the running count can catch it.
    def chunks():
        for _ in range(JSON_LIMIT // 100 + 5):
            yield b"x" * 100

    response = client.post("/api/v1/body", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"


def test_uploads_get_the_larger_limit(client):
    ok = client.post("/api/v1/upload", files={"file": ("a.png", b"x" * (UPLOAD_LIMIT - 500), "image/png")})
    assert ok.status_code == 200
    assert ok.json() == {"size": UPLOAD_LIMIT - 500}
    too_big = client.post("/api/v1/upload", files={"file": ("a.png", b"x" * (UPLOAD_LIMIT + 1), "image/png")})
    assert too_big.status_code == 413


def test_uploads_do_not_make_json_bodies_bigger(client):
    assert post_body(client, JSON_LIMIT + 1).status_code == 413


def test_a_body_that_lies_about_its_length_is_caught_when_it_is_read():
    async def scenario():
        async def app(scope, receive, send):
            while (await receive()).get("more_body"):
                pass

        middleware = BodyLimitMiddleware(app, json_limit=100, upload_limit=100)
        chunks = [
            {"type": "http.request", "body": b"x" * 60, "more_body": True},
            {"type": "http.request", "body": b"x" * 60, "more_body": False},
        ]

        async def receive():
            return chunks.pop(0)

        scope = {"type": "http", "headers": [(b"content-length", b"10")]}  # claims 10 bytes, sends 120
        await middleware(scope, receive, lambda message: None)

    with pytest.raises(BodyTooLarge) as caught:
        asyncio.run(scenario())
    assert caught.value.status_code == 413


def test_a_content_length_that_is_not_a_number_is_a_400():
    sent = []

    async def scenario():
        async def app(scope, receive, send):
            raise AssertionError("must not run")

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        scope = {"type": "http", "headers": [(b"content-length", b"12abc")]}
        await BodyLimitMiddleware(app, json_limit=100, upload_limit=100)(scope, receive, send)

    asyncio.run(scenario())
    assert sent[0]["status"] == 400


def test_the_limits_must_be_positive():
    with pytest.raises(ConfigError, match="positive"):
        ApiConfig(max_json_bytes=0)
