"""The request log and the structured, scrubbed JSON logs (api/logs.py, api/requestlog.py).

The point of all of it is what must NOT appear: the tests send secrets in every place a request can carry one and read
the whole output for them.
"""

import asyncio
import contextlib
import io
import json
import logging
from types import SimpleNamespace

import psycopg.errors
import pytest
from fastapi import Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.logs import (
    QUIET_LIBRARIES,
    REDACTED,
    JsonFormatter,
    configure_logging,
    error_summary,
    request_id_var,
    scrub,
)
from tradejournal.api.requestlog import MAX_LOGGED_PATH, REQUEST_ID_HEADER

PRODUCTION = {"production": True, "app_origin": "https://app.example.com", "session_secret": b"s" * 32}


@contextlib.contextmanager
def preserved_logging():
    """What `configure_logging` changes, put back afterwards, so no test leaks its logging into the next."""
    root = logging.getLogger()
    saved_root = root.handlers[:], root.level
    names = ("uvicorn", "uvicorn.error", "uvicorn.access", *QUIET_LIBRARIES)
    saved = [
        (n, logging.getLogger(n).handlers[:], logging.getLogger(n).propagate, logging.getLogger(n).level) for n in names
    ]
    try:
        yield
    finally:
        root.handlers[:] = saved_root[0]
        root.setLevel(saved_root[1])
        for name, handlers, propagate, level in saved:
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.setLevel(level)


@pytest.fixture
def out():
    """Logging as production sets it up, into a string, and put back afterwards."""
    with preserved_logging():
        stream = io.StringIO()
        configure_logging(stream)
        yield stream


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def make_client(**config) -> TestClient:
    app = create_app(ApiConfig(app_version="build-42", **config))

    # POST routes, because the SPA's catch-all is a GET and would answer a GET to these paths first.
    @app.post("/api/v1/things/{thing_id}")
    def thing(thing_id: int, request: Request) -> dict:
        logging.getLogger("tj.test").warning("inside %s", thing_id)
        return {"id": thing_id}

    @app.post("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("database password is hunter2 and the note says dana@example.com")

    @app.post("/api/v1/status/{code}")
    def status(code: int) -> Response:
        return Response(status_code=code)

    @app.post("/api/v1/halfway")
    def halfway() -> StreamingResponse:
        def chunks():
            yield b"first"
            raise RuntimeError("note text hunter2, after the answer began")

        return StreamingResponse(chunks())

    return TestClient(app, raise_server_exceptions=False)


# ---- scrubbing ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("GET /auth/callback?code=abc123def&state=xyz789uvw HTTP/1.1", "abc123def"),
        ("GET /auth/callback?code=abc123def&state=xyz789uvw HTTP/1.1", "xyz789uvw"),
        ("https://x.example/cb?id_token=tok-1234&access_token=tok-5678", "tok-1234"),
        ("https://x.example/cb?id_token=tok-1234&access_token=tok-5678", "tok-5678"),
        ("client_secret=s3cr3t-value", "s3cr3t-value"),
        ("password=hunter2", "hunter2"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ("cookie __Host-tj_session=Zm9vYmFyYmF6; theme=dark", "Zm9vYmFyYmF6"),
        ("could not connect to postgresql://app_rw:hunter2@db.internal:5432/tj", "hunter2"),
        ("bad token eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl", "eyJhbGciOiJSUzI1NiJ9"),
        ("the secret is " + "a1b2c3d4" * 8, "a1b2c3d4a1b2c3d4"),
        ("Key (email)=(dana@example.com) already exists.", "dana@example.com"),
        ("sent to Dana.K+beta@Example.CO.uk today", "Dana.K+beta"),
    ],
)
def test_scrub_removes_what_looks_like_a_secret(text, secret):
    assert secret not in scrub(text)


def test_scrub_keeps_the_rest_of_the_line_readable():
    assert scrub("GET /auth/callback?code=abc123def&state=xyz789uvw HTTP/1.1") == (
        f"GET /auth/callback?code={REDACTED}&state={REDACTED} HTTP/1.1"
    )
    assert scrub("Authorization: Bearer abcdefghijklmnop") == f"Authorization: Bearer {REDACTED}"
    assert (
        scrub("postgresql://app_rw:hunter2@db.internal:5432/tj")
        == f"postgresql://app_rw:{REDACTED}@db.internal:5432/tj"
    )
    assert scrub("Key (email)=(dana@example.com) already exists.") == "Key (email)=([email]) already exists."


@pytest.mark.parametrize(
    "text",
    [
        "POST /api/v1/trades 200 12.5ms",
        "trade 1204 opened at 09:30:01 on 2026-10-04",
        "5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11",
        "a" * 31,
        "",
    ],
)
def test_scrub_leaves_ordinary_text_alone(text):
    assert scrub(text) == text


def test_scrub_hides_a_hexadecimal_run_from_thirty_two_characters_and_not_before():
    assert scrub("x " + "a" * 32 + " y") == f"x {REDACTED} y"
    assert scrub("x " + "a" * 31 + " y") == "x " + "a" * 31 + " y"


def test_scrub_can_be_applied_twice():
    once = scrub("code=abc123 and dana@example.com and " + "f" * 40)
    assert scrub(once) == once


# ---- the error summary -------------------------------------------------------------------------------------------


def _fail_inside() -> None:
    raise ValueError("the note says hunter2")


def _caught() -> ValueError:
    try:
        _fail_inside()
    except ValueError as error:
        return error
    raise AssertionError("unreachable")


def test_an_error_is_its_type_and_where_it_happened_and_never_its_message():
    summary = error_summary(_caught())
    assert summary["type"] == "ValueError"
    assert any(frame.endswith(" _fail_inside") or " _fail_inside" in frame for frame in summary["frames"])
    assert all(":" in frame for frame in summary["frames"])
    assert "hunter2" not in json.dumps(summary)


def test_a_database_error_gives_its_code_and_names_but_not_the_value_that_broke_it():
    error = psycopg.errors.UniqueViolation(
        'duplicate key value violates "users_email_key". Key (email)=(dana@example.com)'
    )
    summary = error_summary(error)
    assert summary["type"] == "psycopg.errors.UniqueViolation"
    assert summary["sqlstate"] == "23505"
    assert "dana@example.com" not in json.dumps(summary)


def test_a_frame_is_the_end_of_its_file_name_the_line_and_the_function():
    [*_, last] = error_summary(_caught())["frames"]
    where, function = last.rsplit(" ", 1)
    path, line = where.rsplit(":", 1)
    assert function == "_fail_inside"
    assert line.isdigit()
    assert path.endswith("tests/test_api_logging.py")
    assert path.count("/") <= 2  # at most the last three parts of the path
    assert "\\" not in path and ":" not in path


def test_a_database_error_names_its_table_and_constraint_when_it_has_them():
    class Violation(Exception):
        sqlstate = "23505"
        diag = SimpleNamespace(
            table_name="users", constraint_name="users_email_key", message_detail="Key (email)=(x@y.zz)"
        )

    summary = error_summary(Violation("duplicate"))
    assert (summary["sqlstate"], summary["table_name"], summary["constraint_name"]) == (
        "23505",
        "users",
        "users_email_key",
    )
    assert "x@y.zz" not in json.dumps(summary)


def test_only_the_first_three_causes_are_named():
    error: BaseException | None = None
    for kind in (KeyError, IndexError, TypeError, OSError, ValueError):
        try:
            raise kind("x") from error
        except BaseException as raised:  # noqa: BLE001
            error = raised
    assert error is not None
    assert error_summary(error)["caused_by"] == ["OSError", "TypeError", "IndexError"]


def test_an_error_says_what_it_was_raised_from_and_keeps_only_the_last_frames():
    def deep(n: int) -> None:
        if n == 0:
            try:
                raise KeyError("secret-key-name")
            except KeyError as inner:
                raise RuntimeError("outer") from inner
        deep(n - 1)

    with pytest.raises(RuntimeError) as caught:
        deep(30)
    summary = error_summary(caught.value)
    assert summary["caused_by"] == ["KeyError"]
    assert len(summary["frames"]) == 12
    assert "secret-key-name" not in json.dumps(summary)


# ---- the JSON line -----------------------------------------------------------------------------------------------


def _record(message="hello", level=logging.INFO, fields=None, exc_info=None, args=()):
    record = logging.LogRecord("tj.test", level, __file__, 1, message, args, exc_info)
    if fields is not None:
        record.fields = fields
    return record


def test_a_record_is_one_line_of_json_with_when_level_logger_and_message():
    line = JsonFormatter().format(_record("two\nlines", level=logging.WARNING))
    assert "\n" not in line
    data = json.loads(line)
    assert data["level"] == "warning"
    assert data["logger"] == "tj.test"
    assert data["msg"] == "two\nlines"
    assert data["ts"].endswith("+00:00") and data["ts"][10] == "T"


def test_the_message_is_scrubbed_after_its_arguments_are_filled_in():
    data = json.loads(JsonFormatter().format(_record("token was %s", args=("code=abc123def",))))
    assert data["msg"] == f"token was code={REDACTED}"


def test_named_facts_are_added_and_text_ones_are_scrubbed():
    data = json.loads(
        JsonFormatter().format(
            _record(fields={"count": 3, "ok": True, "ratio": 0.5, "none": None, "who": "dana@example.com"})
        )
    )
    assert (data["count"], data["ok"], data["ratio"], data["none"]) == (3, True, 0.5, None)
    assert data["who"] == "[email]"


def test_a_named_fact_cannot_replace_the_time_level_logger_or_message():
    data = json.loads(
        JsonFormatter().format(_record("real", fields={"level": "fake", "msg": "fake", "logger": "fake", "ts": "fake"}))
    )
    assert (data["level"], data["msg"], data["logger"]) == ("info", "real", "tj.test")
    assert data["ts"] != "fake"


def test_the_request_id_is_added_while_one_is_set_and_not_after():
    token = request_id_var.set("req-12345678")
    try:
        assert json.loads(JsonFormatter().format(_record()))["request_id"] == "req-12345678"
    finally:
        request_id_var.reset(token)
    assert "request_id" not in json.loads(JsonFormatter().format(_record()))


def test_an_exception_on_a_record_becomes_its_summary_and_never_its_text():
    error = _caught()
    line = JsonFormatter().format(
        _record("failed", level=logging.ERROR, exc_info=(type(error), error, error.__traceback__))
    )
    data = json.loads(line)
    assert data["error"]["type"] == "ValueError"
    assert "hunter2" not in line
    assert "Traceback" not in line


def test_a_fact_that_is_not_json_is_written_as_text():
    data = json.loads(JsonFormatter().format(_record(fields={"thing": {1, 2}})))
    assert data["thing"] == "{1, 2}"


# ---- configuring -------------------------------------------------------------------------------------------------


def test_configuring_twice_leaves_one_handler(out):
    configure_logging(io.StringIO())
    configure_logging(out)
    assert len([h for h in logging.getLogger().handlers if h.get_name() == "tj-json"]) == 1
    logging.getLogger("tj.test").warning("once")
    assert len(lines(out)) == 1


def test_the_servers_own_error_log_comes_out_as_scrubbed_json_without_the_message(out):
    error = _caught()
    logging.getLogger("uvicorn.error").error(
        "Exception in ASGI application", exc_info=(type(error), error, error.__traceback__)
    )
    [data] = lines(out)
    assert data["logger"] == "uvicorn.error"
    assert data["error"]["type"] == "ValueError"
    assert "hunter2" not in out.getvalue()


def test_a_handler_the_server_put_on_its_own_error_log_is_replaced_so_nothing_is_written_twice(out):
    plain = io.StringIO()
    logging.getLogger("uvicorn.error").addHandler(logging.StreamHandler(plain))
    configure_logging(out)
    logging.getLogger("uvicorn.error").error("started code=SECRETCODE")
    assert plain.getvalue() == ""
    assert len(lines(out)) == 1


def test_the_servers_access_log_is_silenced_because_it_would_carry_the_query_string(out):
    logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET /auth/callback?code=SECRETCODE HTTP/1.1" 302')
    assert out.getvalue() == ""
    assert logging.getLogger("uvicorn.access").propagate is False


def test_the_http_client_libraries_do_not_write_every_outgoing_address_at_info(out):
    for name in QUIET_LIBRARIES:
        assert logging.getLogger(name).level == logging.WARNING
        logging.getLogger(name).info("HTTP Request: GET https://idp.example/users?email=x")
    assert out.getvalue() == ""


def test_production_starts_the_json_log_when_the_app_starts_and_development_does_not():
    root = logging.getLogger()
    with preserved_logging():
        with TestClient(create_app(ApiConfig())):
            assert not any(h.get_name() == "tj-json" for h in root.handlers)
        with TestClient(create_app(ApiConfig(**PRODUCTION))):
            assert any(h.get_name() == "tj-json" for h in root.handlers)


def test_the_container_does_not_start_the_servers_own_access_log():
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "--no-access-log" in dockerfile


# ---- the request line --------------------------------------------------------------------------------------------


def test_each_request_is_one_line_with_the_method_the_route_the_status_and_the_time(out):
    response = make_client().post("/api/v1/things/42")
    request_line = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert len(request_line) == 1
    [data] = request_line
    assert (data["msg"], data["method"], data["status"]) == ("request", "POST", 200)
    assert data["path"] == "/api/v1/things/{thing_id}"  # the template, not the id
    assert data["level"] == "info"
    assert isinstance(data["duration_ms"], float) and data["duration_ms"] >= 0
    assert data["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_nothing_the_request_carries_reaches_the_log_not_even_when_it_looks_harmless(out):
    make_client().post(
        "/api/v1/things/7?code=QUERYSECRET&state=STATESECRET&note=NOTEINQUERY",
        headers={
            "Cookie": "__Host-tj_session=COOKIESECRET",
            "Authorization": "Bearer AUTHSECRET12345",
            "User-Agent": "AGENTSECRET",
            "X-Csrf-Token": "CSRFSECRET",
        },
        json={"note": "BODYSECRET", "email": "body@example.com"},
    )
    written = out.getvalue()
    for secret in (
        "QUERYSECRET",
        "STATESECRET",
        "NOTEINQUERY",
        "COOKIESECRET",
        "AUTHSECRET",
        "AGENTSECRET",
        "CSRFSECRET",
        "BODYSECRET",
        "body@example.com",
        "testclient",  # the client's address
    ):
        assert secret not in written, secret


def test_the_sign_in_callback_with_its_code_is_logged_without_the_code(out):
    make_client().get("/auth/callback?code=THEAUTHCODE123&state=THESTATE456")
    assert "THEAUTHCODE123" not in out.getvalue()
    assert "THESTATE456" not in out.getvalue()
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert data["path"] == "/auth/callback"


def test_a_request_id_is_made_when_there_is_none_and_it_is_returned(out):
    response = make_client().post("/api/v1/things/1")
    assert len(response.headers[REQUEST_ID_HEADER]) == 36  # a random UUID


def test_two_requests_have_different_ids(out):
    client = make_client()
    ids = {client.post("/api/v1/things/1").headers[REQUEST_ID_HEADER] for _ in range(3)}
    assert len(ids) == 3


def test_a_plain_request_id_from_the_caller_is_kept(out):
    response = make_client().post("/api/v1/things/1", headers={REQUEST_ID_HEADER: "edge-7f3a9c2b.1"})
    assert response.headers[REQUEST_ID_HEADER] == "edge-7f3a9c2b.1"
    assert {d["request_id"] for d in lines(out) if d["logger"] == "tj.request"} == {"edge-7f3a9c2b.1"}


@pytest.mark.parametrize(
    "unsafe",
    [
        "short",
        "seven77",
        "has a space in it",
        "abcdefgh ijk",
        "a" * 65,
        "semi;colon;1234567",
        "<script>alert(1)</script>",
        "line\tbreak12345",
    ],
)
def test_an_id_from_the_caller_that_is_not_a_short_plain_token_is_replaced(out, unsafe):
    response = make_client().post("/api/v1/things/1", headers={REQUEST_ID_HEADER: unsafe})
    assert response.headers[REQUEST_ID_HEADER] != unsafe
    assert len(response.headers[REQUEST_ID_HEADER]) == 36


def test_the_longest_plain_id_and_the_shortest_are_kept(out):
    client = make_client()
    for good in ("a" * 8, "b" * 64):
        assert client.post("/api/v1/things/1", headers={REQUEST_ID_HEADER: good}).headers[REQUEST_ID_HEADER] == good


def test_other_log_lines_written_during_the_request_carry_its_id(out):
    response = make_client().post("/api/v1/things/9")
    [inside] = [d for d in lines(out) if d["logger"] == "tj.test"]
    assert inside["msg"] == "inside 9"
    assert inside["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_an_answer_that_is_a_refusal_is_a_warning_with_the_status(out):
    response = make_client().post("/api/v1/things/not-a-number")
    assert response.status_code == 422
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert (data["level"], data["status"]) == ("warning", 422)


@pytest.mark.parametrize(
    ("status", "level"),
    [
        (200, "info"),
        (204, "info"),
        (302, "info"),
        (399, "info"),
        (400, "warning"),
        (404, "warning"),
        (499, "warning"),
        (500, "error"),
        (503, "error"),
    ],
)
def test_the_level_follows_the_status(out, status, level):
    make_client().post(f"/api/v1/status/{status}")
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert (data["status"], data["level"]) == (status, level)


def test_a_refusal_by_a_layer_inside_the_app_is_logged_with_an_id_too(out):
    client = make_client(max_json_bytes=10)
    response = client.post("/api/v1/things/1", content=b"x" * 11, headers={"content-type": "application/json"})
    assert response.status_code == 413
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert (data["status"], data["level"]) == (413, "warning")
    assert data["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_a_failure_after_the_answer_began_is_still_an_error_line_without_the_text(out):
    with contextlib.suppress(Exception):
        make_client().post("/api/v1/halfway")
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert data["level"] == "error"
    assert data["status"] == 200  # what the caller was already told
    assert data["error"]["type"] == "RuntimeError"
    assert "hunter2" not in out.getvalue()


def test_the_request_id_is_put_back_when_the_request_ends():
    from tradejournal.api.requestlog import RequestLogMiddleware

    async def inner(scope, receive, send):
        assert request_id_var.get() is not None
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run() -> str | None:
        scope = {"type": "http", "method": "GET", "path": "/x", "headers": []}
        sent = []

        async def send(message):
            sent.append(message)

        await RequestLogMiddleware(inner)(scope, None, send)
        return request_id_var.get()

    assert asyncio.run(run()) is None


def test_a_path_nothing_matches_is_logged_as_it_came_but_cut_and_scrubbed(out):
    long_path = "/api/v1/nothing-here/" + "x" * 500 + "/dana@example.com"
    make_client().post(long_path)
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert data["level"] == "warning"
    assert len(data["path"]) <= MAX_LOGGED_PATH
    assert data["path"].startswith("/api/v1/nothing-here/xxxx")
    assert "dana@" not in out.getvalue()


def test_an_address_in_a_path_nothing_matches_is_hidden(out):
    make_client().post("/api/v1/nothing-here/dana@example.com/more")
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert data["path"] == "/api/v1/nothing-here/[email]/more"


def test_an_address_that_straddles_the_cut_is_hidden_whole_not_left_in_half(out):
    prefix = "/api/v1/" + "x" * (MAX_LOGGED_PATH - 12)
    make_client().post(prefix + "dana.kowalski@example.com")
    assert "dana" not in out.getvalue()
    assert "kowalski" not in out.getvalue()


def test_a_path_that_is_cut_is_cut_at_the_limit_and_not_before(out):
    client = make_client()
    client.post("/api/v1/zz" + "y" * (MAX_LOGGED_PATH - len("/api/v1/zz")))
    client.post("/api/v1/zz" + "y" * (MAX_LOGGED_PATH - len("/api/v1/zz") + 50))
    paths = [d["path"] for d in lines(out) if d["logger"] == "tj.request"]
    assert [len(p) for p in paths] == [MAX_LOGGED_PATH, MAX_LOGGED_PATH]


def test_the_health_checks_are_not_logged_while_they_pass(out):
    client = make_client()
    assert client.get("/healthz").status_code == 200
    assert out.getvalue() == ""


def test_a_failing_ready_check_is_logged_as_an_error(out):
    response = make_client(**PRODUCTION).get("/readyz")
    assert response.status_code == 503
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert (data["level"], data["path"], data["status"]) == ("error", "/readyz", 503)


def test_a_failure_is_an_error_line_with_its_type_and_place_and_never_its_message(out):
    response = make_client().post("/api/v1/boom")
    assert response.status_code == 500
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert (data["level"], data["status"], data["path"]) == ("error", 500, "/api/v1/boom")
    assert data["error"]["type"] == "RuntimeError"
    assert any(" boom" in frame for frame in data["error"]["frames"])
    assert "hunter2" not in out.getvalue()
    assert "dana@" not in out.getvalue()


def test_a_failure_returns_the_request_id_so_a_person_can_quote_it(out):
    response = make_client().post("/api/v1/boom")
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert response.headers[REQUEST_ID_HEADER] == data["request_id"]
    assert "hunter2" not in response.text


def test_the_security_headers_are_still_on_a_failure(out):
    response = make_client().post("/api/v1/boom")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_our_loggers_have_a_handler_of_their_own_so_nothing_is_printed_bare_before_the_json_log_is_configured():
    # Without one, Python writes a warning to standard error as the bare message ("request"), which says nothing.
    assert any(isinstance(h, logging.NullHandler) for h in logging.getLogger("tj").handlers)


def test_a_page_of_the_app_is_logged_by_its_path_without_its_query_string_and_assets_are_not_logged(out, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    client = make_client(web_dist=tmp_path)
    assert client.get("/trades/123?filter=SECRETFILTER", headers={"accept": "text/html"}).status_code == 200
    assert client.get("/assets/app-abc123.js").status_code == 200
    [data] = [d for d in lines(out) if d["logger"] == "tj.request"]
    assert (data["method"], data["path"], data["status"]) == ("GET", "/trades/123", 200)
    assert "SECRETFILTER" not in out.getvalue()
