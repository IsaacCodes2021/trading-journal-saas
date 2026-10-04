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
