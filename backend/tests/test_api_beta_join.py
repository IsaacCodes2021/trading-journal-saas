"""The public form that joins the beta (api/routers/beta.py), with no database: the rules about who may post, what is said
back, and what is sent. The database's own rules are in tests/pg/test_pg_beta.py."""

from __future__ import annotations

from dataclasses import replace

from api_fakes import FakeAccounts, FakeAudit, FakeSessions
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.mailer import Email, MailError
from tradejournal.api.ports import BetaCode, Services
from tradejournal.api.ratelimit import RateLimits

ORIGIN = "https://app.example.com"
CONFIG = ApiConfig(app_origin=ORIGIN, production=True, session_secret=b"s" * 32)
GOOD = {"first_name": "Alex", "email": "alex@example.test", "prop_firm": "Topstep"}
HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}


class FakeBeta:
    def __init__(self) -> None:
        self.joins: list[dict] = []
        self.results: list[tuple[int, bool]] = []
        self.make = True  # False: the database refuses to make a code (an existing account, a code a moment ago, ...)

    def join(self, *, first_name: str, email: str, prop_firm: str) -> BetaCode | None:
        self.joins.append({"first_name": first_name, "email": email, "prop_firm": prop_firm})
        return BetaCode(7, "TJ-AAAA-BBBB") if self.make else None

    def email_result(self, signup_id: int, *, ok: bool) -> None:
        self.results.append((signup_id, ok))


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[Email] = []
        self.fail_with: str | None = None

    def send(self, email: Email) -> None:
        if self.fail_with:
            raise MailError(self.fail_with)
        self.sent.append(email)


def build(*, beta=True, mailer=True, config=CONFIG):
    fake_beta, fake_mailer = FakeBeta(), FakeMailer()
    services = Services(
        sessions=FakeSessions(), accounts=FakeAccounts(), audit=FakeAudit(), beta=fake_beta if beta else None
    )
    app = create_app(config, services, mailer=fake_mailer if mailer else None)
    return TestClient(app, base_url=ORIGIN, follow_redirects=False), fake_beta, fake_mailer


def post(client, body=None, **headers):
    return client.post("/api/v1/beta/join", json=GOOD if body is None else body, headers={**HEADERS, **headers})


# ---- what it does ---------------------------------------------------------------------------------------------------


def test_a_request_makes_a_code_and_emails_it_to_the_address_with_a_link_to_our_page():
    client, beta, mailer = build()
    response = post(client, {**GOOD, "email": "  Alex@Example.TEST "})
    assert response.status_code == 202 and response.json() == {"status": "ok"}
    assert beta.joins == [{"first_name": "Alex", "email": "Alex@Example.TEST", "prop_firm": "Topstep"}]
    [email] = mailer.sent
    assert email.to == "alex@example.test" and email.subject == "Your Roundturn beta code"
    assert "TJ-AAAA-BBBB" in email.text and f"{ORIGIN}/invite?code=TJ-AAAA-BBBB" in email.text
    assert beta.results == [(7, True)]


def test_the_answer_is_the_same_whether_or_not_a_code_was_made():
    client, beta, mailer = build()
    made = post(client)
    beta.make = False
    refused = post(client)
    assert (made.status_code, made.json()) == (refused.status_code, refused.json())
    assert len(mailer.sent) == 1  # nothing is sent for the one that made no code


def test_a_failed_email_is_recorded_and_the_person_is_told_nothing_different():
    client, beta, mailer = build()
    mailer.fail_with = "mail_unavailable_503"
    response = post(client)
    assert response.status_code == 202 and response.json() == {"status": "ok"}
    assert beta.results == [(7, False)] and mailer.sent == []


def test_a_failed_email_never_puts_a_reason_in_the_answer_or_the_log(caplog):
    client, _beta, mailer = build()
    mailer.fail_with = "mail_refused_422"
    with caplog.at_level("WARNING"):
        response = post(client)
    assert "mail_refused" not in response.text
    assert "TJ-AAAA-BBBB" not in caplog.text and "alex@example.test" not in caplog.text


def test_a_filled_hidden_field_gets_the_same_answer_and_nothing_is_done():
    client, beta, mailer = build()
    response = post(client, {**GOOD, "website": "http://spam.example"})
    assert response.status_code == 202 and response.json() == {"status": "ok"}
    assert beta.joins == [] and mailer.sent == []


def test_it_says_so_when_nobody_could_receive_the_code():
    for kwargs in ({"mailer": False}, {"beta": False}):
        client, beta, mailer = build(**kwargs)
        response = post(client)
        assert response.status_code == 503
        assert beta.joins == [] and mailer.sent == []


# ---- who may post ---------------------------------------------------------------------------------------------------


def test_a_foreign_origin_is_refused_and_nothing_is_made():
    client, beta, mailer = build()
    assert post(client, Origin="https://evil.example").status_code == 403
    assert post(client, Origin="null").status_code == 403
    assert beta.joins == [] and mailer.sent == []


def test_a_cross_site_request_is_refused():
    client, beta, _ = build()
    assert post(client, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert post(client, **{"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert beta.joins == []


def test_a_request_that_says_where_it_came_from_in_neither_way_is_refused():
    client, beta, _ = build()
    response = client.post("/api/v1/beta/join", json=GOOD, headers={"Content-Type": "application/json"})
    assert response.status_code == 403 and beta.joins == []


def test_a_plain_html_form_post_is_refused_even_from_our_own_origin():
    client, beta, _ = build()
    response = client.post(
        "/api/v1/beta/join",
        data=GOOD,
        headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 415 and beta.joins == []


def test_it_is_a_post_only_route_and_a_get_finds_nothing():
    client, _, _ = build()
    assert client.get("/api/v1/beta/join", headers={"Accept": "application/json"}).status_code in (404, 405)


def test_it_is_rate_limited_by_address_and_says_when_to_come_back():
    client, beta, _ = build()
    codes = [post(client).status_code for _ in range(6)]
    assert codes == [202] * 5 + [429]
    limited = post(client)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1


# ---- what it accepts ------------------------------------------------------------------------------------------------


def test_input_that_makes_no_sense_is_refused_before_anything_is_made():
    client, beta, mailer = build(config=replace(CONFIG, rate_limits=RateLimits(enabled=False)))
    bad = [
        {},
        {**GOOD, "first_name": "   "},
        {**GOOD, "first_name": "n" * 81},
        {**GOOD, "prop_firm": ""},
        {**GOOD, "prop_firm": "f" * 81},
        {**GOOD, "email": "ab"},
        {**GOOD, "email": "e" * 255},
        {**GOOD, "extra": "field"},
        {**GOOD, "website": "w" * 201},
    ]
    for body in bad:
        assert post(client, body).status_code == 422, body
    assert beta.joins == [] and mailer.sent == []


def test_a_huge_body_is_refused_by_the_global_limit_before_it_is_read():
    client, beta, _ = build()
    response = post(client, {**GOOD, "website": "x" * (2 * 1024 * 1024)})
    assert response.status_code == 413 and beta.joins == []


# ---- where it is not ------------------------------------------------------------------------------------------------


def test_it_is_not_in_the_api_description_and_sets_no_cookie():
    client, _, _ = build()
    # the operator's list is described (under /api/admin); the public join route is not
    assert all(not path.startswith("/api/v1/beta") for path in client.app.openapi()["paths"])
    assert "set-cookie" not in post(client).headers


def test_the_routes_that_answer_without_a_session_are_exactly_the_known_ones():
    # A route left out of the API description is not covered by the generated "everything needs a session" test, so every
    # such route is listed by hand: a new one has to be added here on purpose.
    client, _, _ = build()
    described = {(method.upper(), path) for path, item in client.app.openapi()["paths"].items() for method in item}
    answering = set()
    for route in client.app.routes:
        # FastAPI keeps an included router as one object holding its own routes (an internal, so this fails loudly if it moves)
        inner = getattr(getattr(route, "original_router", None), "routes", None) or [route]
        for single in inner:
            for method in getattr(single, "methods", None) or ():
                if method not in ("HEAD", "OPTIONS"):
                    answering.add((method, single.path))
    hidden = {item for item in answering - described if "{" not in item[1]}  # not the catch-all that serves the page
    assert hidden == {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/auth/login"),
        ("GET", "/auth/callback"),
        ("POST", "/api/v1/beta/join"),
        ("POST", "/webhooks/billing"),  # the payment provider's: its signature is the lock (test_pg_billing_app.py)
    }
