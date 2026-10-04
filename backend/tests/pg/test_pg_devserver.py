"""The local development launcher: it finds the user to be, and never starts without what it needs."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tradejournal.api.devserver import DevServerError, build_config, main, pick_user


def test_with_no_choice_it_is_the_only_user(login_url, alice):
    assert pick_user(login_url, None) == alice


def test_an_empty_or_shared_database_needs_a_choice(login_url, owner, alice, bob):
    with pytest.raises(DevServerError, match="2 users"):
        pick_user(login_url, None)
    assert pick_user(login_url, str(bob)) == bob  # naming one resolves it
    owner.execute("TRUNCATE users CASCADE")
    with pytest.raises(DevServerError, match="0 users"):
        pick_user(login_url, None)


def test_a_named_user_must_exist_and_be_an_id(login_url, alice):
    with pytest.raises(DevServerError, match="no such user"):
        pick_user(login_url, str(uuid4()))
    with pytest.raises(DevServerError, match="UUID"):
        pick_user(login_url, "alice")


def test_the_config_comes_from_the_env_file_and_is_for_development(tmp_path, login_url, alice, app_url):
    env_file = tmp_path / "env"
    env_file.write_text(f"TJ_DEV_APP_URL={app_url}\nTJ_DEV_LOGIN_URL={login_url}\n", encoding="utf-8")
    config = build_config(env_file, origin="http://localhost:5173", user_id=None)
    assert config.dev_user_id == alice and config.production is False
    assert config.app_origin == "http://localhost:5173"
    assert app_url not in repr(config) and login_url not in repr(config)  # the URLs hold passwords


def test_it_refuses_to_start_without_the_database_urls(tmp_path, capsys):
    env_file = tmp_path / "env"
    env_file.write_text("TJ_DEV_APP_URL=postgresql://x@127.0.0.1/db\n", encoding="utf-8")
    with pytest.raises(DevServerError, match="provisioning"):
        build_config(env_file, origin="http://localhost:5173", user_id=None)
    assert main(["--env-file", str(env_file)]) == 2
    assert "cannot start" in capsys.readouterr().err


def test_a_database_that_is_behind_stops_the_launch_unless_migrating_was_asked_for(app_url, monkeypatch):
    from tradejournal.api import devserver

    monkeypatch.setattr(devserver, "latest_version", lambda: "9999_future")
    env = {"TJ_DEV_APP_URL": app_url}
    with pytest.raises(DevServerError, match="needs 9999_future.*--migrate"):
        devserver.check_schema(env, migrate_now=False)
    with pytest.raises(DevServerError, match="TJ_DEV_OWNER_URL"):
        devserver.check_schema(env, migrate_now=True)  # asked to migrate, but no owner credentials to do it with
    seen = []
    monkeypatch.setattr(devserver, "migrate", lambda url: seen.append(url) or ["9999_future"])
    message = devserver.check_schema({**env, "TJ_DEV_OWNER_URL": "owner-url"}, migrate_now=True)
    assert message == "applied migrations: 9999_future" and seen == ["owner-url"]


def test_a_current_database_launches_quietly(app_url):
    from tradejournal.api import devserver

    assert devserver.check_schema({"TJ_DEV_APP_URL": app_url}, migrate_now=False) is None


# ---- trying the real sign-in on this machine -------------------------------------------------------------------------

HIDDEN_TEXT = "kinde-client-secret-value-123"


def env_files(tmp_path, app_url, login_url, owner_url=None, kinde=True):
    env = tmp_path / "env"
    lines = [f"TJ_DEV_APP_URL={app_url}", f"TJ_DEV_LOGIN_URL={login_url}"]
    if owner_url:
        lines.append(f"TJ_DEV_OWNER_URL={owner_url}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    kinde_file = tmp_path / "env.kinde"
    if kinde:
        kinde_file.write_text(
            f"KINDE_DOMAIN=https://example.kinde.com\nKINDE_CLIENT_ID=client-id-123\nKINDE_CLIENT_SECRET={HIDDEN_TEXT}\n"
            "KINDE_M2M_CLIENT_ID=m2m\nKINDE_M2M_CLIENT_SECRET=m2m-secret\n",
            encoding="utf-8",
        )
    return env, kinde_file


def test_real_sign_in_needs_no_development_user_and_takes_its_settings_from_the_kinde_file(
    tmp_path, owner, app_url, login_url
):
    env, kinde = env_files(tmp_path, app_url, login_url)
    config = build_config(env, origin="http://localhost:5173", user_id=None, real_sign_in=True, kinde_env_file=kinde)
    assert config.dev_user_id is None and config.production is False  # the database has no user at all: not needed
    assert (config.kinde_domain, config.kinde_client_id) == ("https://example.kinde.com", "client-id-123")
    assert config.kinde_client_secret == HIDDEN_TEXT and config.signup_mode == "invite"
    assert HIDDEN_TEXT not in repr(config) and "m2m" not in repr(config)  # the secret never shows; M2M is not used


def test_the_app_built_from_it_has_the_real_flow_and_the_development_one_does_not(tmp_path, alice, app_url, login_url):
    from tradejournal.api.app import create_app

    env, kinde = env_files(tmp_path, app_url, login_url)
    real = build_config(env, origin="http://localhost:5173", user_id=None, real_sign_in=True, kinde_env_file=kinde)
    assert create_app(real).state.auth is not None
    fixed = build_config(env, origin="http://localhost:5173", user_id=None)
    assert create_app(fixed).state.auth is None


def test_a_missing_or_incomplete_kinde_file_says_what_is_missing_and_never_a_value(tmp_path, app_url, login_url):
    env, kinde = env_files(tmp_path, app_url, login_url, kinde=False)
    with pytest.raises(
        DevServerError, match="env.kinde does not exist.*KINDE_DOMAIN, KINDE_CLIENT_ID, KINDE_CLIENT_SECRET"
    ):
        build_config(env, origin="http://localhost:5173", user_id=None, real_sign_in=True, kinde_env_file=kinde)
    kinde.write_text("KINDE_DOMAIN=https://example.kinde.com\nKINDE_CLIENT_ID=client-id-123\n", encoding="utf-8")
    with pytest.raises(DevServerError, match="no value for KINDE_CLIENT_SECRET") as problem:
        build_config(env, origin="http://localhost:5173", user_id=None, real_sign_in=True, kinde_env_file=kinde)
    assert "client-id-123" not in str(problem.value) and "example.kinde.com" not in str(problem.value)


def test_a_kinde_domain_that_is_not_https_is_refused_by_the_same_rule_as_in_production(tmp_path, app_url, login_url):
    env, kinde = env_files(tmp_path, app_url, login_url)
    kinde.write_text(kinde.read_text().replace("https://", "http://"), encoding="utf-8")
    with pytest.raises(DevServerError, match="TJ_KINDE_DOMAIN must look like"):
        build_config(env, origin="http://localhost:5173", user_id=None, real_sign_in=True, kinde_env_file=kinde)


def test_a_development_user_and_real_sign_in_do_not_mix(tmp_path, alice, app_url, login_url):
    env, kinde = env_files(tmp_path, app_url, login_url)
    with pytest.raises(DevServerError, match="--user-id is for the development sign-in"):
        build_config(env, origin="http://localhost:5173", user_id=str(alice), real_sign_in=True, kinde_env_file=kinde)


def test_the_launcher_reports_these_problems_as_a_plain_message_and_a_failure_status(
    tmp_path, capsys, app_url, login_url
):
    env, kinde = env_files(tmp_path, app_url, login_url, kinde=False)
    assert main(["--env-file", str(env), "--real-sign-in", "--kinde-env-file", str(kinde)]) == 2  # never your own file
    assert "cannot start" in capsys.readouterr().err


def test_make_invite_prints_a_single_use_code_for_a_week_that_the_real_flow_accepts(
    tmp_path, capsys, system, owner, owner_url, app_url, login_url
):
    from datetime import UTC, datetime, timedelta

    from tradejournal.pg import invites

    env, _ = env_files(tmp_path, app_url, login_url, owner_url)
    assert main(["--env-file", str(env), "--make-invite"]) == 0
    code = capsys.readouterr().out.strip()
    assert invites.normalize(code) == code and code.startswith("TJ-")
    row = owner.execute("SELECT max_uses, expires_at, label, email FROM invites").fetchone()
    assert (row["max_uses"], row["label"], row["email"]) == (1, "local trial", None)
    assert timedelta(days=6, hours=23) < row["expires_at"] - datetime.now(UTC) <= timedelta(days=7)
    with system.system_session("login") as conn:
        assert invites.check(conn, code.lower(), "anyone@example.test") is not None


def test_make_invite_can_be_for_one_email_address_only(tmp_path, capsys, system, owner_url, owner, app_url, login_url):
    from tradejournal.pg import invites

    env, _ = env_files(tmp_path, app_url, login_url, owner_url)
    assert main(["--env-file", str(env), "--make-invite", "--invite-email", "me@example.test"]) == 0
    code = capsys.readouterr().out.strip()
    with system.system_session("login") as conn:
        assert invites.check(conn, code, "me@example.test") is not None
        assert invites.check(conn, code, "someone.else@example.test") is None


def test_make_invite_without_the_owner_connection_says_so(tmp_path, capsys, app_url, login_url):
    env, _ = env_files(tmp_path, app_url, login_url)
    assert main(["--env-file", str(env), "--make-invite"]) == 2
    err = capsys.readouterr().err
    assert "cannot make an invite" in err and "TJ_DEV_OWNER_URL" in err and app_url not in err
