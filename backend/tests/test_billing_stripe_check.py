"""`python -m tradejournal.billing.stripe_check`: the read-only check that Stripe is ready to be switched on. It passes only
when the product, the price, the portal, the webhook endpoint and the secret are as the connector needs them, says what to
fix otherwise, changes nothing, and never prints a key or a secret."""

from __future__ import annotations

import httpx
import pytest
from stripe_world import API_KEY, ORIGIN, PRICE, PRODUCT, WEBHOOK_SECRET, FakeStripeApi

from tradejournal.billing import stripe_check
from tradejournal.billing.stripe import API_VERSION

ENV = {
    "TJ_STRIPE_ENV": "test",
    "TJ_STRIPE_API_KEY": API_KEY,
    "TJ_STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "TJ_STRIPE_PRODUCT_ID": PRODUCT,
    "TJ_STRIPE_PRICE_ID": PRICE,
    "TJ_APP_ORIGIN": ORIGIN,
}


def run(api, env=None):
    lines: list[str] = []
    code = stripe_check.run(
        env if env is not None else ENV, http=httpx.Client(transport=httpx.MockTransport(api.handle)), out=lines.append
    )
    return code, lines


def test_everything_in_place_passes_and_only_reads():
    api = FakeStripeApi().ready()
    code, lines = run(api)
    assert code == 0 and lines[-1].startswith("Ready") and sum(line.startswith("PASS") for line in lines) == 5
    assert {method for method, *_ in api.requests} == {"GET"}
    assert all(API_KEY not in line and WEBHOOK_SECRET not in line for line in lines)


@pytest.mark.parametrize(
    ("spoil", "said"),
    [
        (lambda api: api.prices[PRICE].update(unit_amount=1900), "not USD 15 a month"),
        (lambda api: api.prices[PRICE].update(tax_behavior="inclusive"), "tax is not added on top"),
        (lambda api: api.prices[PRICE]["recurring"].update(trial_period_days=7), "its own trial"),
        (lambda api: api.products[PRODUCT].update(livemode=True), "not in test mode"),
        (lambda api: api.products[PRODUCT].update(tax_code="txcd_99999999"), "tax code"),
        (lambda api: api.portal_configurations.clear(), "no default customer portal"),
        (
            lambda api: api.portal_configurations[0]["features"]["subscription_cancel"].update(mode="immediately"),
            "cancels at once",
        ),
        (lambda api: api.webhook_endpoints.clear(), "no webhook endpoint sends to"),
        (lambda api: api.webhook_endpoints[0].update(api_version="2025-03-31.basil"), API_VERSION),
        (lambda api: api.webhook_endpoints[0].update(enabled_events=["invoice.paid"]), "does not send"),
        (lambda api: api.webhook_endpoints[0].update(status="disabled"), "disabled"),
    ],
)
def test_each_thing_out_of_place_fails_and_says_what_to_fix(spoil, said):
    api = FakeStripeApi().ready()
    spoil(api)
    code, lines = run(api)
    assert code == 1 and lines[-1] == "Not ready: 1 to fix."
    assert [line for line in lines if line.startswith("FAIL")][0].find(said) > 0


def test_every_event_at_once_counts_as_every_event():
    api = FakeStripeApi().ready()
    api.webhook_endpoints[0]["enabled_events"] = ["*"]
    assert run(api)[0] == 0


def test_settings_that_are_wrong_stop_before_stripe_is_asked():
    api = FakeStripeApi().ready()
    for env, said in (({}, "no TJ_STRIPE_* settings"), ({**ENV, "TJ_STRIPE_ENV": "live"}, "is a test key")):
        code, lines = run(api, env)
        assert code == 2 and said in lines[0]
    assert api.requests == []


def test_no_origin_or_no_secret_fails_those_lines_only():
    api = FakeStripeApi().ready()
    env = {k: v for k, v in ENV.items() if k not in ("TJ_APP_ORIGIN", "TJ_STRIPE_WEBHOOK_SECRET")}
    code, lines = run(api, env)
    failed = [line for line in lines if line.startswith("FAIL")]
    assert code == 1 and len(failed) == 2 and "TJ_APP_ORIGIN" in failed[0] and "SECRET" in failed[1]


def test_stripe_away_is_a_failure_not_a_crash():
    api = FakeStripeApi().ready()
    api.answers += [httpx.Response(503)] * 30
    code, lines = run(api)
    assert code == 1 and any("did not answer as expected" in line for line in lines)


def test_the_env_file_is_read_and_its_values_never_printed(tmp_path, capsys, monkeypatch):
    env_file = tmp_path / "env.stripe"
    env_file.write_text("# a comment\n" + "".join(f"{k}={v}\n" for k, v in ENV.items()), encoding="utf-8")
    assert stripe_check.read_env_file(env_file) == ENV
    monkeypatch.setattr(stripe_check, "run", lambda env: print(sorted(env)) or 0)
    assert stripe_check.main(["--env-file", str(env_file)]) == 0
    printed = capsys.readouterr().out
    assert "TJ_STRIPE_API_KEY" in printed and API_KEY not in printed
    assert stripe_check.main(["--env-file", str(tmp_path / "missing")]) == 2
