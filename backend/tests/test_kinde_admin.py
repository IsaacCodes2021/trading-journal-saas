"""Removing an identity at Kinde: idempotent, retry-safe, and never a secret in an error (no network: a mock transport)."""

from __future__ import annotations

import httpx
import pytest

from tradejournal.auth.idp import IdentityError
from tradejournal.auth.kinde_admin import KindeManagement

DOMAIN = "https://example.kinde.com"
CLIENT_ID = "m2m-client-id-123"
CLIENT_VALUE = "m2m-hidden-value-456"
ACCESS_CREDENTIAL = "management-access-token-789"


class Kinde:
    """A pretend Management API that records what it was asked and answers as scripted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.token_requests = 0
        self.token_status = 200
        self.lifetime = 86_399
        self.lookup = 200
        self.delete = 200
        self.network_down = False
        self.bearer_seen: set[str | None] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.network_down:
            raise httpx.ConnectError("down", request=request)
        path = request.url.path
        self.calls.append((request.method, str(request.url)))
        if path == "/oauth2/token":
            self.token_requests += 1
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "client_credentials" and form["audience"] == f"{DOMAIN}/api"
            assert form["client_id"] == CLIENT_ID and form["client_secret"] == CLIENT_VALUE
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": ACCESS_CREDENTIAL, "expires_in": self.lifetime})
        self.bearer_seen.add(request.headers.get("authorization"))
        if request.method == "GET":
            return httpx.Response(self.lookup, json={"id": "x"})
        return httpx.Response(self.delete, json={})


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def kinde():
    return Kinde()


def provider(kinde: Kinde, clock: Clock | None = None) -> KindeManagement:
    return KindeManagement(
        domain=DOMAIN + "/",
        client_id=CLIENT_ID,
        client_secret=CLIENT_VALUE,
        http=httpx.Client(transport=httpx.MockTransport(kinde.handle)),
        monotonic=clock or Clock(),
    )


def test_it_looks_the_identity_up_then_removes_it_with_the_management_token(kinde):
    assert provider(kinde).delete_user("kp_abc123") is True
    assert [call[0] for call in kinde.calls] == ["POST", "GET", "DELETE"]
    assert kinde.calls[1][1] == f"{DOMAIN}/api/v1/user?id=kp_abc123"
    assert kinde.bearer_seen == {f"Bearer {ACCESS_CREDENTIAL}"}


def test_an_identity_that_is_already_gone_counts_as_removed_and_nothing_more_is_sent(kinde):
    for status in (400, 404):  # Kinde answers 400 for a user that was deleted (spike S1b); 404 is allowed too
        kinde.calls.clear()
        kinde.lookup = status
        assert provider(kinde).delete_user("kp_abc123") is True
        assert [call[0] for call in kinde.calls] == ["POST", "GET"]  # no DELETE


def test_a_removal_that_races_a_second_run_is_still_a_success(kinde):
    kinde.delete = 404  # someone else removed it between the lookup and the delete
    assert provider(kinde).delete_user("kp_abc123") is True
    for status in (200, 204):
        kinde.delete = status
        assert provider(kinde).delete_user("kp_abc123") is True


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_any_other_answer_to_the_lookup_is_an_error_to_retry_later(kinde, status):
    kinde.lookup = status
    with pytest.raises(IdentityError) as problem:
        provider(kinde).delete_user("kp_abc123")
    assert problem.value.reason == f"management_lookup_{status}"
    assert [call[0] for call in kinde.calls] == ["POST", "GET"]  # it did not go on to delete anything


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_a_refused_removal_is_an_error_to_retry_later(kinde, status):
    kinde.delete = status
    with pytest.raises(IdentityError) as problem:
        provider(kinde).delete_user("kp_abc123")
    assert problem.value.reason == f"management_delete_{status}"


def test_a_network_failure_and_a_refused_token_are_errors_with_short_reasons(kinde):
    kinde.network_down = True
    with pytest.raises(IdentityError) as problem:
        provider(kinde).delete_user("kp_abc123")
    assert problem.value.reason == "management_network"
    kinde.network_down = False
    kinde.token_status = 401
    with pytest.raises(IdentityError) as problem:
        provider(kinde).delete_user("kp_abc123")
    assert problem.value.reason == "management_token"


def test_a_garbled_token_answer_is_a_token_error_not_a_crash(kinde):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>not json</html>"))
    management = KindeManagement(
        domain=DOMAIN, client_id=CLIENT_ID, client_secret=CLIENT_VALUE, http=httpx.Client(transport=transport)
    )
    with pytest.raises(IdentityError) as problem:
        management.delete_user("kp_abc123")
    assert problem.value.reason == "management_token"


def test_the_token_is_reused_until_a_minute_before_it_expires(kinde):
    clock = Clock()
    management = provider(kinde, clock)
    kinde.lifetime = 3600
    for _ in range(3):
        management.delete_user("kp_a")
    assert kinde.token_requests == 1
    clock.now += 3600 - 61
    management.delete_user("kp_a")
    assert kinde.token_requests == 1  # still more than a minute left
    clock.now += 2
    management.delete_user("kp_a")
    assert kinde.token_requests == 2  # inside the last minute: a fresh one


def test_a_subject_cannot_change_the_address_it_is_sent_to(kinde):
    provider(kinde).delete_user("kp_a/../b?id=other&x=#frag")
    sent = kinde.calls[1][1]
    assert sent.startswith(f"{DOMAIN}/api/v1/user?id=kp_a%2F..%2Fb%3Fid%3Dother%26x%3D%23frag")
    assert sent.count("?") == 1 and "#" not in sent


@pytest.mark.parametrize("subject", ["", "x" * 201])
def test_an_empty_or_enormous_subject_is_refused_before_anything_is_sent(kinde, subject):
    with pytest.raises(IdentityError) as problem:
        provider(kinde).delete_user(subject)
    assert problem.value.reason == "management_subject" and kinde.calls == []


def test_no_secret_or_token_ever_appears_in_an_error(kinde):
    texts = []
    for setup in ({"network_down": True}, {"token_status": 500}, {"lookup": 500}, {"delete": 500}):
        fresh = Kinde()
        for name, value in setup.items():
            setattr(fresh, name, value)
        with pytest.raises(IdentityError) as problem:
            provider(fresh).delete_user("kp_abc123")
        texts.append(f"{problem.value!r} {problem.value}")
    assert not any(
        secret in text for text in texts for secret in (CLIENT_VALUE, CLIENT_ID, ACCESS_CREDENTIAL, "Bearer")
    )
