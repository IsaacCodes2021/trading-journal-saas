import pytest
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.schemas import ApiModel, Money

HTML = {"accept": "text/html"}
OUTSIDE_TEXT = "outside the web root"


class Echo(ApiModel):
    amount: Money
    note: str = ""


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(OUTSIDE_TEXT, encoding="utf-8")
    return root


def make_client(dist, **config) -> TestClient:
    app = create_app(ApiConfig(web_dist=dist, app_version="build-42", **config))

    # POST routes, because the SPA's catch-all is a GET and would answer a GET to these paths first.
    @app.post("/api/v1/echo")
    def echo(body: Echo) -> Echo:
        return body

    @app.post("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("database password is hunter2")

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(dist):
    return make_client(dist)


# ---- security headers --------------------------------------------------------------------------------------------


def test_every_response_carries_the_security_headers(client):
    for response in (client.get("/healthz"), client.get("/", headers=HTML), client.get("/nope")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-app-version"] == "build-42"
        assert response.headers["cross-origin-opener-policy"] == "same-origin"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_the_csp_never_allows_inline_or_eval_code(client):
    csp = client.get("/healthz").headers["content-security-policy"]
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "script-src 'self';" in csp


def test_hsts_only_in_production(dist):
    assert "strict-transport-security" not in make_client(dist).get("/healthz").headers
    production = make_client(dist, production=True, app_origin="https://app.example.com", session_secret=b"s" * 32)
    assert production.get("/healthz").headers["strict-transport-security"].startswith("max-age=")


def test_private_paths_are_never_cacheable(client):
    for path in ("/api/v1/anything", "/auth/login", "/files/1"):
        assert client.get(path).headers["cache-control"] == "no-store"


def test_a_server_error_still_has_the_security_headers_and_leaks_nothing(client):
    response = client.post("/api/v1/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert "hunter2" not in response.text
    assert "content-security-policy" in response.headers
    assert response.headers["cache-control"] == "no-store"


# ---- errors ------------------------------------------------------------------------------------------------------


def test_errors_are_problem_json(client):
    response = client.get("/api/v1/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["type"], body["errors"]) == (404, "about:blank", None)
    assert body["title"] == "Not Found"
    assert body["detail"] is None  # the default 404 text only repeats the title


def test_validation_errors_name_the_field_and_do_not_echo_the_input(client):
    response = client.post("/api/v1/echo", json={"amount": 12.5, "note": 7})
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == 422
    assert {error["field"] for error in body["errors"]} == {"amount", "note"}
    assert "12.5" not in response.text.replace("never a JSON number", "")  # the number itself is not echoed back


def test_money_round_trips_as_an_exact_string(client):
    response = client.post("/api/v1/echo", json={"amount": "-475.00"})
    assert response.status_code == 200
    assert response.json() == {"amount": "-475.00", "note": ""}


# ---- the single-page app -----------------------------------------------------------------------------------------


def test_healthz_is_not_shadowed_by_the_spa(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_navigations_get_index_html_and_must_revalidate(client):
    for path in ("/", "/trades", "/trades/42/notes"):
        response = client.get(path, headers=HTML)
        assert response.status_code == 200
        assert "<title>spa</title>" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_head_requests_work_for_pages_and_assets(client):
    # Uptime monitors and `curl -I` use HEAD; same status and headers as GET, no body.
    page = client.head("/trades", headers=HTML)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-cache"
    assert "content-security-policy" in page.headers
    assert page.content == b""
    assert client.head("/assets/app-abc123.js").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.head("/api/v1/nope", headers=HTML).status_code == 404


def test_a_missing_script_or_image_is_a_404_not_index_html(client):
    assert client.get("/missing.js", headers={"accept": "*/*"}).status_code == 404
    assert client.get("/assets/missing.js").status_code == 404


def test_server_paths_are_never_answered_with_the_spa(client):
    for path in ("/api/v1/nope", "/auth/nope", "/files/nope", "/webhooks/nope", "/readyz/nope", "/healthz/nope"):
        response = client.get(path, headers=HTML)
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"


def test_hashed_assets_are_cached_for_a_year(client):
    response = client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_top_level_files_are_served_and_revalidated(client):
    response = client.get("/favicon.svg")
    assert (response.status_code, response.text) == (200, "<svg/>")
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt", "/assets/../../secret.txt"]
)
def test_paths_cannot_escape_the_web_root(client, path):
    response = client.get(path, headers=HTML)
    assert OUTSIDE_TEXT not in response.text


def test_an_absolute_path_cannot_escape_the_web_root(client, dist):
    # On Windows, `root / "C:/..."` replaces the root; this is the case the resolve-then-check guard exists for.
    secret = (dist.parent / "secret.txt").as_posix().lstrip("/")
    assert OUTSIDE_TEXT not in client.get("/" + secret, headers=HTML).text


def test_the_app_starts_without_a_built_front_end(tmp_path):
    client = make_client(tmp_path / "does-not-exist")
    assert client.get("/healthz").status_code == 200
    assert client.get("/", headers=HTML).status_code == 404
