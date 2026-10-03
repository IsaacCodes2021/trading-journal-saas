"""/readyz: can this process serve? Public, so it answers with states and never with error text. No database needed here;
tests/pg/test_pg_readiness.py runs the real check against Postgres."""

import pytest
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.api.openapi import render

PRODUCTION = {"production": True, "app_origin": "https://app.example.com", "session_secret": b"s" * 32}


def client(readiness=None, **config) -> TestClient:
    return TestClient(create_app(ApiConfig(**config), readiness=readiness), raise_server_exceptions=False)


def test_ready_when_every_check_passes():
    response = client(lambda: (True, {"database": "ok"})).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_not_ready_is_a_503_with_the_state_of_each_check():
    response = client(lambda: (False, {"database": "schema_behind"})).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "schema_behind"}}


def test_a_check_that_blows_up_is_not_ready_and_leaks_nothing():
    def explode():
        raise RuntimeError("could not connect to server at 10.0.3.7 as app_rw with password hunter2")

    response = client(explode).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"readiness": "error"}}
    assert "hunter2" not in response.text and "10.0.3.7" not in response.text


def test_the_answer_is_never_cached_and_carries_the_security_headers():
    response = client(lambda: (True, {"database": "ok"})).get("/readyz")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_it_needs_no_credentials_and_is_not_part_of_the_api_contract():
    web = client(lambda: (True, {"database": "ok"}))
    assert web.get("/readyz").status_code == 200  # no session, no CSRF token
    assert web.get("/readyz", headers={"accept": "text/html"}).status_code == 200  # the SPA fallback does not shadow it
    assert "/readyz" not in render()


def test_without_a_database_a_development_process_is_ready_and_says_so():
    response = client().get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "not configured"}}


def test_without_a_database_a_production_process_is_not_ready():
    response = client(**PRODUCTION).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "not configured"}


def test_the_database_url_is_validated_and_never_printed():
    config = ApiConfig(database_url="postgresql://app_rw:hunter2@db.internal:5432/tj")
    assert "hunter2" not in repr(config) and "db.internal" not in repr(config)
    assert ApiConfig(database_url="postgres://u:p@h/db").database_url
    for bad in ("mysql://u:p@h/db", "http://h", "h:5432/db"):
        with pytest.raises(ConfigError, match="TJ_DATABASE_URL"):
            ApiConfig(database_url=bad)


def test_the_database_url_comes_from_the_environment_and_empty_means_unset():
    assert ApiConfig.from_env({"TJ_DATABASE_URL": "postgresql://u:p@h/db"}).database_url == "postgresql://u:p@h/db"
    assert ApiConfig.from_env({"TJ_DATABASE_URL": ""}).database_url is None
    assert ApiConfig.from_env({}).database_url is None


def test_a_process_with_a_database_url_builds_its_own_check_and_survives_an_unreachable_database():
    # Nothing listens on this port. The app must still boot (the pool keeps trying in the background) and say not ready.
    config = ApiConfig(database_url="postgresql://app_rw:x@127.0.0.1:9/tj?connect_timeout=1")
    with TestClient(create_app(config)) as web:
        response = web.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "unreachable"}}
