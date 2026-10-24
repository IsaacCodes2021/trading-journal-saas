"""`--billing --stripe` on the development server (api/devserver.py): a test payment with Stripe's test mode on this machine,
beside the Paddle sandbox that plain `--billing` still starts (tests/pg/test_pg_devserver.py)."""

from __future__ import annotations

import pytest
from stripe_world import API_KEY, LIVE_KEY, PRICE, PRODUCT, WEBHOOK_SECRET
from test_pg_devserver import WEBHOOK_URL, env_files, paddle_files

from tradejournal.api.devserver import DevServerError, build_config


def stripe_files(tmp_path, app_url, login_url, **stripe):
    """The files `--real-sign-in --billing --stripe` reads; `stripe` changes or blanks a TJ_STRIPE_* setting."""
    env, kinde = env_files(tmp_path, app_url, login_url)
    env.write_text(env.read_text() + f"TJ_DEV_WEBHOOK_URL={WEBHOOK_URL}\n", encoding="utf-8")
    values = {
        "TJ_STRIPE_ENV": "test",
        "TJ_STRIPE_API_KEY": API_KEY,
        "TJ_STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "TJ_STRIPE_PRODUCT_ID": PRODUCT,
        "TJ_STRIPE_PRICE_ID": PRICE,
        **stripe,
    }
    stripe_file = tmp_path / "env.stripe"
    stripe_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return env, kinde, stripe_file


def with_stripe(env, kinde, stripe_file):
    return build_config(
        env,
        origin="http://localhost:5173",
        user_id=None,
        real_sign_in=True,
        kinde_env_file=kinde,
        billing=True,
        stripe=True,
        stripe_env_file=stripe_file,
    )


def test_billing_with_stripe_turns_on_with_the_test_mode_settings_and_the_webhooks_own_role(
    tmp_path, app_url, login_url
):
    config = with_stripe(*stripe_files(tmp_path, app_url, login_url))
    assert (config.billing_enabled, config.billing_provider, config.paddle) == (True, "stripe", None)
    assert config.stripe is not None and config.stripe.environment == "test" and config.stripe.complete_for_web
    assert config.webhook_database_url == WEBHOOK_URL
    assert API_KEY not in repr(config) and WEBHOOK_SECRET not in repr(config)


def test_the_app_built_with_stripe_has_the_stripe_connector(tmp_path, app_url, login_url, alice):
    from tradejournal.api.app import create_app
    from tradejournal.billing.stripe import StripeBilling

    billing = create_app(with_stripe(*stripe_files(tmp_path, app_url, login_url))).state.services.billing
    assert billing is not None and isinstance(billing.provider, StripeBilling)


def test_plain_billing_is_still_the_paddle_sandbox(tmp_path, app_url, login_url):
    env, kinde, paddle_file = paddle_files(tmp_path, app_url, login_url)
    config = build_config(
        env,
        origin="http://localhost:5173",
        user_id=None,
        real_sign_in=True,
        kinde_env_file=kinde,
        billing=True,
        paddle_env_file=paddle_file,
        stripe_env_file=tmp_path / "not-read",
    )
    assert (config.billing_provider, config.stripe) == ("paddle", None)


def test_stripe_needs_billing(tmp_path, app_url, login_url):
    env, kinde, stripe_file = stripe_files(tmp_path, app_url, login_url)
    with pytest.raises(DevServerError, match="--stripe goes with --billing"):
        build_config(
            env, origin="http://localhost:5173", user_id=None, real_sign_in=True, kinde_env_file=kinde, stripe=True
        )


@pytest.mark.parametrize(
    ("stripe", "said"),
    [
        ({"TJ_STRIPE_WEBHOOK_SECRET": ""}, "env.stripe has no value for TJ_STRIPE_WEBHOOK_SECRET"),
        ({"TJ_STRIPE_ENV": "live"}, "for Stripe's test mode only"),
        ({"TJ_STRIPE_API_KEY": LIVE_KEY}, "TJ_STRIPE_API_KEY is a live key"),
    ],
)
def test_stripe_takes_test_mode_only_and_says_what_is_wrong_never_a_value(tmp_path, app_url, login_url, stripe, said):
    with pytest.raises(DevServerError, match=said) as problem:
        with_stripe(*stripe_files(tmp_path, app_url, login_url, **stripe))
    assert API_KEY not in str(problem.value) and LIVE_KEY not in str(problem.value)


def test_stripe_without_its_file_says_so(tmp_path, app_url, login_url):
    env, kinde, stripe_file = stripe_files(tmp_path, app_url, login_url)
    with pytest.raises(DevServerError, match="env.stripe.missing does not exist: it holds TJ_STRIPE_ENV"):
        with_stripe(env, kinde, stripe_file.with_name("env.stripe.missing"))
