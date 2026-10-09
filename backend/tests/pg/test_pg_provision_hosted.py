"""Setting up a managed database for the first deploy (``tradejournal.pg.provision_hosted``).

A managed provider hands you an administrator who is NOT a superuser, so the tests run as one: a scratch role that may
create roles and databases and owns a scratch database, standing in for the host's default account. The six roles are
cluster-wide, so the tests reuse the passwords the test databases already use (read from ``.env.test``) and change no one's.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import quote, urlsplit

import psycopg
import pytest
from conftest import ENV_FILE
from psycopg import sql

from tradejournal.pg import migrate as pg_migrate
from tradejournal.pg import provision_hosted as hosted
from tradejournal.pg.provision import password_key, read_env_file


@pytest.fixture(scope="module")
def cluster(pg_urls):
    """(host, port, superuser URL), or a skip: the superuser's password comes from PGPASSWORD, as for provision.py."""
    parts = urlsplit(pg_urls[0])
    if not os.environ.get("PGPASSWORD"):
        if os.environ.get("TJ_REQUIRE_POSTGRES"):
            pytest.fail("TJ_REQUIRE_POSTGRES is set but PGPASSWORD (the superuser's password) is not")
        pytest.skip("PGPASSWORD is not set: the hosted setup needs a superuser to make its scratch administrator")
    return parts.hostname, parts.port, f"postgresql://postgres@{parts.hostname}:{parts.port}/postgres"


def make_scratch(super_url: str, *, can_create_roles: bool) -> tuple[str, str, str]:
    """A throwaway administrator and a database it owns. Returns (role, password, database)."""
    tag = secrets.token_hex(4)
    admin, database = f"hosted_admin_{tag}", f"hosted_db_{tag}"
    password = secrets.token_hex(12)
    rights = "CREATEROLE CREATEDB" if can_create_roles else "NOCREATEROLE NOCREATEDB"
    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL(f"CREATE ROLE {{}} LOGIN NOSUPERUSER {rights} PASSWORD {{}}").format(
                sql.Identifier(admin), sql.Literal(password)
            )
        )
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(admin)))
        # On a host the administrator creates these roles itself, and so holds the right to change them. Here they already
        # exist (the test databases use them), so give the stand-in that right, and nothing more.
        for role in hosted.ROLES:
            conn.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN OPTION, INHERIT FALSE, SET FALSE").format(
                    sql.Identifier(role), sql.Identifier(admin)
                )
            )
    return admin, password, database


def drop_scratch(super_url: str, admin: str, database: str) -> None:
    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(admin)))


def existing_password_file(path, cluster_info, database: str) -> None:
    """An output file holding the passwords the test roles already have, so the run changes none of them."""
    host, port, _ = cluster_info
    env = read_env_file(ENV_FILE)
    setup_url = f"postgresql://x@{host}:{port}/{database}"
    lines = [
        f"{key}={hosted.url_for(setup_url, role, env[password_key(role)])}" for role, key in hosted.URL_KEYS.items()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def scratch(cluster):
    _, _, super_url = cluster
    admin, password, database = make_scratch(super_url, can_create_roles=True)
    yield admin, password, database
    drop_scratch(super_url, admin, database)


def setup_url(cluster, admin: str, password: str, database: str) -> str:
    host, port, _ = cluster
    return f"postgresql://{admin}:{quote(password)}@{host}:{port}/{database}"


def test_a_database_with_a_limited_administrator_is_set_up_so_the_migrations_run(
    cluster, scratch, tmp_path, monkeypatch, capsys
):
    admin, password, database = scratch
    out = tmp_path / ".env.hosted"
    existing_password_file(out, cluster, database)
    monkeypatch.setenv("TJ_SETUP_URL", setup_url(cluster, admin, password, database))

    assert hosted.main(["--out", str(out)]) == 0

    captured = capsys.readouterr()
    shown = captured.out + captured.err
    env = read_env_file(ENV_FILE)
    assert not any(env[password_key(role)] in shown for role in hosted.ROLES)  # nothing secret is printed
    host, port, super_url = cluster
    with psycopg.connect(setup_url(cluster, admin, password, database), autocommit=True) as conn:
        assert hosted.verify(conn) == []
    # the secret the server signs tokens with is what it accepts: 64 hexadecimal characters
    assert len(bytes.fromhex(hosted.read_settings(out)["TJ_SESSION_SECRET"])) == 32
    # the real test of "owns what it needs to": the migrations run as tj_owner, from the file, with nothing else done
    owner_url = next(
        line.split("=", 1)[1] for line in out.read_text(encoding="utf-8").splitlines() if "TJ_OWNER_URL=" in line
    )
    pg_migrate.migrate(owner_url)
    # and the request role can reach the schema but not change it
    app_url = next(
        line.split("=", 1)[1]
        for line in out.read_text(encoding="utf-8").splitlines()
        if line.startswith("TJ_DATABASE_URL=")
    )
    with psycopg.connect(app_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE sneaky (id int)")


def test_running_it_again_changes_nothing(cluster, scratch, tmp_path, monkeypatch):
    admin, password, database = scratch
    out = tmp_path / ".env.hosted"
    existing_password_file(out, cluster, database)
    monkeypatch.setenv("TJ_SETUP_URL", setup_url(cluster, admin, password, database))
    assert hosted.main(["--out", str(out)]) == 0
    first = out.read_text(encoding="utf-8")
    assert hosted.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == first


def test_an_administrator_who_cannot_create_roles_is_refused_before_anything_is_changed(
    cluster, tmp_path, monkeypatch, capsys
):
    _, _, super_url = cluster
    admin, password, database = make_scratch(super_url, can_create_roles=False)
    try:
        monkeypatch.setenv("TJ_SETUP_URL", setup_url(cluster, admin, password, database))
        out = tmp_path / ".env.hosted"
        assert hosted.main(["--out", str(out)]) == 1
        assert "CREATEROLE" in capsys.readouterr().err
        assert not out.exists()
        with psycopg.connect(super_url, autocommit=True) as conn:
            owner = conn.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (database,)
            ).fetchone()[0]
        assert owner == admin  # the database was not handed over
    finally:
        drop_scratch(super_url, admin, database)


def test_verify_names_a_database_that_has_not_been_handed_over(cluster, scratch):
    admin, password, database = scratch
    with psycopg.connect(setup_url(cluster, admin, password, database), autocommit=True) as conn:
        problems = hosted.verify(conn)
    assert any("owned by" in problem and admin in problem for problem in problems)


def test_verify_names_a_role_with_the_wrong_attributes(cluster, scratch):
    admin, password, database = scratch
    _, _, super_url = cluster
    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute("ALTER ROLE app_rw CREATEDB")  # put back below, whatever happens
        try:
            with psycopg.connect(setup_url(cluster, admin, password, database), autocommit=True) as admin_conn:
                problems = hosted.verify(admin_conn)
        finally:
            conn.execute("ALTER ROLE app_rw NOCREATEDB")
    assert "app_rw has the wrong attributes" in problems


def give_the_schema_to_the_administrator(cluster, admin: str, database: str) -> None:
    """A database as some hosts make it: the public schema belongs to the administrator, not to the database's owner."""
    host, port, _ = cluster
    with psycopg.connect(f"postgresql://postgres@{host}:{port}/{database}", autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(admin)))


def test_verify_names_a_public_schema_that_belongs_to_someone_else(cluster, scratch):
    admin, password, database = scratch
    give_the_schema_to_the_administrator(cluster, admin, database)
    with psycopg.connect(setup_url(cluster, admin, password, database), autocommit=True) as conn:
        problems = hosted.verify(conn)
    assert f"the public schema is owned by {admin}" in problems


def test_a_public_schema_the_administrator_owns_is_handed_over_too(cluster, scratch, tmp_path, monkeypatch):
    admin, password, database = scratch
    give_the_schema_to_the_administrator(cluster, admin, database)
    out = tmp_path / ".env.hosted"
    existing_password_file(out, cluster, database)
    monkeypatch.setenv("TJ_SETUP_URL", setup_url(cluster, admin, password, database))
    assert hosted.main(["--out", str(out)]) == 0
    with psycopg.connect(setup_url(cluster, admin, password, database), autocommit=True) as conn:
        assert hosted.verify(conn) == []
        owner = conn.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'").fetchone()[
            0
        ]
    assert owner == "tj_owner"


def test_with_migrate_the_schema_is_built_in_the_same_run(cluster, scratch, tmp_path, monkeypatch, capsys):
    admin, password, database = scratch
    out = tmp_path / ".env.hosted"
    existing_password_file(out, cluster, database)
    monkeypatch.setenv("TJ_SETUP_URL", setup_url(cluster, admin, password, database))
    assert hosted.main(["--out", str(out), "--migrate"]) == 0
    assert "migrations applied: 0001" in capsys.readouterr().out
    host, port, _ = cluster
    with psycopg.connect(f"postgresql://postgres@{host}:{port}/{database}", autocommit=True) as conn:
        done = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert done == len(list(pg_migrate.MIGRATIONS_DIR.glob("*.sql")))
    assert hosted.main(["--out", str(out), "--migrate"]) == 0  # and a second run has nothing left to do
    assert "nothing to apply" in capsys.readouterr().out


def test_the_settings_file_is_what_the_server_needs_to_start_and_report_ready(cluster, scratch, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tradejournal.api.app import create_app
    from tradejournal.api.config import ApiConfig

    # production mode installs the JSON log on the root logger for good; leave the other tests' logging alone
    monkeypatch.setattr("tradejournal.api.app.configure_logging", lambda *args, **kwargs: None)

    admin, password, database = scratch
    out = tmp_path / ".env.hosted"
    existing_password_file(out, cluster, database)
    monkeypatch.setenv("TJ_SETUP_URL", setup_url(cluster, admin, password, database))
    assert hosted.main(["--out", str(out), "--migrate"]) == 0
    # what a person pastes into the host: the lines that are not commented out, and nothing else
    live = {
        key: value
        for line in out.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, _, value in [line.partition("=")]
    }
    assert set(live) == {"TJ_DATABASE_URL", "TJ_LOGIN_DATABASE_URL", "TJ_SESSION_SECRET"}
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    env = {**live, "TJ_ENV": "production", "TJ_APP_ORIGIN": "https://roundturn.example", "TJ_WEB_DIST": str(tmp_path)}
    with TestClient(create_app(ApiConfig.from_env(env))) as client:
        assert client.get("/healthz").status_code == 200
        ready = client.get("/readyz")
    assert ready.status_code == 200, ready.text
