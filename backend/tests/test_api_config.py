from pathlib import Path

import pytest

from tradejournal.api.config import ApiConfig, ConfigError

SECRET = "ab" * 32  # 32 bytes in hex
PRODUCTION = {"TJ_ENV": "production", "TJ_APP_ORIGIN": "https://app.example.com", "TJ_SESSION_SECRET": SECRET}


def test_development_needs_no_configuration():
    config = ApiConfig.from_env({})
    assert config.production is False
    assert config.app_origin == "http://localhost:8000"
    assert len(config.session_secret) >= 32


def test_development_secrets_differ_per_process_start():
    assert ApiConfig.from_env({}).session_secret != ApiConfig.from_env({}).session_secret


def test_production_reads_everything_from_the_environment():
    env = {**PRODUCTION, "TJ_WEB_DIST": "/srv/web", "TJ_APP_VERSION": "abc1234"}
    config = ApiConfig.from_env(env)
    assert (config.production, config.app_origin, config.app_version) == (True, "https://app.example.com", "abc1234")
    assert config.web_dist == Path("/srv/web")
    assert config.session_secret == bytes.fromhex(SECRET)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"TJ_SESSION_SECRET": None}, "TJ_SESSION_SECRET is required"),
        ({"TJ_SESSION_SECRET": "ab" * 8}, "at least 32 bytes"),
        ({"TJ_SESSION_SECRET": "not hex"}, "hexadecimal"),
        ({"TJ_APP_ORIGIN": "http://app.example.com"}, "https in production"),
        ({"TJ_ENV": "staging"}, "TJ_ENV"),
    ],
)
def test_production_refuses_unsafe_or_missing_settings(change, message):
    env = {**PRODUCTION, **change}
    env = {key: value for key, value in env.items() if value is not None}
    with pytest.raises(ConfigError, match=message):
        ApiConfig.from_env(env)


@pytest.mark.parametrize(
    "origin", ["https://app.example.com/", "https://app.example.com/app", "app.example.com", "ftp://x.test", "https://"]
)
def test_the_origin_must_be_exactly_scheme_and_host(origin):
    # It is compared byte for byte with the Origin header, so a trailing slash would reject every write.
    with pytest.raises(ConfigError, match="TJ_APP_ORIGIN"):
        ApiConfig(app_origin=origin)


DEV = {
    "TJ_DATABASE_URL": "postgresql://app_rw@127.0.0.1/tj_dev",
    "TJ_LOGIN_DATABASE_URL": "postgresql://app_login@127.0.0.1/tj_dev",
    "TJ_DEV_USER_ID": "60c33757-c760-4735-b03d-f3f436b74b7e",
}


def test_a_development_user_needs_both_database_urls_and_is_a_uuid():
    config = ApiConfig.from_env(DEV)
    assert str(config.dev_user_id) == DEV["TJ_DEV_USER_ID"]
    assert "app_rw" not in repr(config) and "app_login" not in repr(config)  # URLs hold passwords: never in a repr
    for missing in ("TJ_DATABASE_URL", "TJ_LOGIN_DATABASE_URL"):
        with pytest.raises(ConfigError, match="needs TJ_DATABASE_URL and TJ_LOGIN_DATABASE_URL"):
            ApiConfig.from_env({k: v for k, v in DEV.items() if k != missing})
    with pytest.raises(ConfigError, match="a UUID"):
        ApiConfig.from_env({**DEV, "TJ_DEV_USER_ID": "alice"})


def test_production_refuses_the_development_sign_in_outright():
    with pytest.raises(ConfigError, match="development only"):
        ApiConfig.from_env({**PRODUCTION, **DEV})


def test_the_login_database_url_must_be_postgres():
    with pytest.raises(ConfigError, match="TJ_LOGIN_DATABASE_URL must be a postgresql"):
        ApiConfig.from_env({"TJ_LOGIN_DATABASE_URL": "mysql://x@y/z"})


KINDE = {
    "TJ_KINDE_DOMAIN": "https://roundturn.kinde.com",
    "TJ_KINDE_CLIENT_ID": "0123456789abcdef",
    "TJ_KINDE_CLIENT_SECRET": "very-secret-value",
}


def test_sign_in_settings_are_read_and_the_secret_never_shows_in_a_repr():
    config = ApiConfig.from_env({**PRODUCTION, **KINDE, "TJ_SIGNUP_MODE": "open"})
    assert (config.kinde_domain, config.kinde_client_id, config.signup_mode) == (
        "https://roundturn.kinde.com",
        "0123456789abcdef",
        "open",
    )
    assert config.kinde_client_secret == KINDE["TJ_KINDE_CLIENT_SECRET"]
    assert KINDE["TJ_KINDE_CLIENT_SECRET"] not in repr(config)


def test_new_people_need_an_invite_unless_sign_up_is_opened_on_purpose():
    assert ApiConfig.from_env(PRODUCTION).signup_mode == "invite"
    assert ApiConfig.from_env({**PRODUCTION, "TJ_SIGNUP_MODE": ""}).signup_mode == "invite"
    with pytest.raises(ConfigError, match="TJ_SIGNUP_MODE"):
        ApiConfig.from_env({**PRODUCTION, "TJ_SIGNUP_MODE": "everyone"})


@pytest.mark.parametrize("missing", list(KINDE))
def test_the_three_kinde_settings_go_together(missing):
    with pytest.raises(ConfigError, match="go together"):
        ApiConfig.from_env({**PRODUCTION, **{k: v for k, v in KINDE.items() if k != missing}})


@pytest.mark.parametrize(
    "domain", ["http://roundturn.kinde.com", "roundturn.kinde.com", "https://roundturn.kinde.com/oauth2", "https://"]
)
def test_the_kinde_domain_must_be_an_https_address_without_a_path(domain):
    with pytest.raises(ConfigError, match="TJ_KINDE_DOMAIN must look like"):
        ApiConfig.from_env({**PRODUCTION, **KINDE, "TJ_KINDE_DOMAIN": domain})


# ---- email -----------------------------------------------------------------------------------------------------------

EMAIL = {"TJ_EMAIL_API_KEY": "re_" + "test" + "0123456789", "TJ_EMAIL_FROM": "Roundturn <beta@roundturn.app>"}


def test_the_email_settings_are_read_and_the_key_is_not_in_the_text_of_the_config():
    config = ApiConfig.from_env({**PRODUCTION, **EMAIL})
    assert (config.email_api_key, config.email_from) == (EMAIL["TJ_EMAIL_API_KEY"], EMAIL["TJ_EMAIL_FROM"])
    assert EMAIL["TJ_EMAIL_API_KEY"] not in repr(config)


def test_without_them_there_is_no_email_and_that_is_fine_for_a_server_that_is_not_taking_sign_ups_yet():
    config = ApiConfig.from_env(PRODUCTION)
    assert (config.email_api_key, config.email_from) == (None, None)


@pytest.mark.parametrize("missing", list(EMAIL))
def test_the_two_email_settings_go_together(missing):
    with pytest.raises(ConfigError, match="TJ_EMAIL_API_KEY and TJ_EMAIL_FROM go together"):
        ApiConfig.from_env({**PRODUCTION, **{k: v for k, v in EMAIL.items() if k != missing}})


@pytest.mark.parametrize("sender", ["not an address", "a@b", "a@b.cc\r\nBcc: x@y.zz", "<a@b.cc>", "Name <a@b.cc"])
def test_the_sender_must_be_an_address_and_cannot_carry_a_second_header(sender):
    with pytest.raises(ConfigError, match="TJ_EMAIL_FROM must look like"):
        ApiConfig.from_env({**PRODUCTION, **EMAIL, "TJ_EMAIL_FROM": sender})


def test_a_bare_address_is_a_sender_too():
    assert ApiConfig.from_env({**PRODUCTION, **EMAIL, "TJ_EMAIL_FROM": "beta@roundturn.app"}).email_from


def test_in_production_the_providers_address_must_be_https():
    with pytest.raises(ConfigError, match="TJ_EMAIL_API_URL must be https"):
        ApiConfig.from_env({**PRODUCTION, **EMAIL, "TJ_EMAIL_API_URL": "http://mail.example.com"})
    assert ApiConfig.from_env({**PRODUCTION, **EMAIL, "TJ_EMAIL_API_URL": "https://mail.example.com"})
