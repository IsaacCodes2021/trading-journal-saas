"""Is Stripe ready to be switched on? A read-only check, run before `TJ_BILLING_PROVIDER=stripe` (docs/runbooks/billing.md).

    cd backend
    .venv\\Scripts\\python.exe -m tradejournal.billing.stripe_check --env-file .env.stripe

It reads the settings the server would (`TJ_STRIPE_*` and `TJ_APP_ORIGIN`), from the environment or from a git-ignored file
given with --env-file, and asks Stripe, changing nothing:

    the key works and belongs to the mode TJ_STRIPE_ENV names (test or live)
    the product and its price exist, are active, and are Roundturn Pro at USD 15 a month, tax added on top, no trial
    the customer portal's default settings cancel at the period's end and allow no plan change
    a webhook endpoint sends to <TJ_APP_ORIGIN>/webhooks/billing, is enabled, uses the API version the connector reads,
    and sends every event the connector needs
    the webhook's signing secret is set (Stripe never shows it again, so only its presence can be checked)

Each line says PASS or FAIL and what to fix; no key, secret or address is printed. Exit 0 when everything passes, 1 when
something fails, 2 when the settings themselves are wrong. A restricted key needs read access to products, prices, the
customer portal and webhook endpoints for this check.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from . import price
from .stripe import API_VERSION, EVENTS, StripeBilling, StripeSettings
from .types import BillingError

TAX_CODE = "txcd_10103000"  # software as a service, personal use (the plan's P2.3)

Check = Callable[[StripeBilling, StripeSettings, str | None], str | None]


def check_product(stripe: StripeBilling, settings: StripeSettings, origin: str | None) -> str | None:
    if settings.product_id is None:
        return "TJ_STRIPE_PRODUCT_ID is not set"
    product = stripe.read("check_product", f"/v1/products/{settings.product_id}")
    if product.get("livemode") is not settings.livemode:
        return f"the product is not in {settings.environment} mode: the key and TJ_STRIPE_ENV disagree with it"
    if product.get("active") is not True:
        return "the product is archived"
    if product.get("tax_code") != TAX_CODE:
        return f"the product's tax code is not {TAX_CODE} (software as a service, personal use)"
    return None


def check_price(stripe: StripeBilling, settings: StripeSettings, origin: str | None) -> str | None:
    if settings.price_id is None:
        return "TJ_STRIPE_PRICE_ID is not set"
    found = stripe.read("check_price", f"/v1/prices/{settings.price_id}")
    recurring = found.get("recurring") if isinstance(found.get("recurring"), dict) else {}
    problems = [
        ("not active", found.get("active") is True),
        ("not a price of TJ_STRIPE_PRODUCT_ID", found.get("product") == settings.product_id),
        (f"not {price.PRICE_TEXT}", found.get("unit_amount") == price.PRICE_CENTS),
        ("not in US dollars", found.get("currency") == price.CURRENCY.lower()),
        ("not monthly", (recurring.get("interval"), recurring.get("interval_count")) == ("month", 1)),
        ("tax is not added on top (tax behavior exclusive)", found.get("tax_behavior") == "exclusive"),
        ("it has its own trial (free days come from each checkout)", not recurring.get("trial_period_days")),
    ]
    wrong = [what for what, fine in problems if not fine]
    return "the price is " + "; ".join(wrong) if wrong else None


def check_portal(stripe: StripeBilling, settings: StripeSettings, origin: str | None) -> str | None:
    listed = stripe.read("check_portal", "/v1/billing_portal/configurations", {"is_default": "true", "limit": "1"})
    rows = listed.get("data") if isinstance(listed.get("data"), list) else []
    if not rows:
        return "no default customer portal settings (Settings > Billing > Customer portal)"
    features = rows[0].get("features") if isinstance(rows[0].get("features"), dict) else {}
    cancel = features.get("subscription_cancel") or {}
    update = features.get("subscription_update") or {}
    if cancel.get("enabled") and cancel.get("mode") != "at_period_end":
        return "the portal cancels at once: set it to cancel at the end of the billing period"
    if update.get("enabled"):
        return "the portal lets people change plan or quantity: turn subscription updates off"
    return None


def check_webhook(stripe: StripeBilling, settings: StripeSettings, origin: str | None) -> str | None:
    if origin is None:
        return "TJ_APP_ORIGIN is not set, so the webhook address cannot be checked"
    address = f"{origin}/webhooks/billing"
    listed = stripe.read("check_webhook", "/v1/webhook_endpoints", {"limit": "100"})
    rows = [row for row in listed.get("data") or [] if isinstance(row, dict) and row.get("url") == address]
    if not rows:
        return f"no webhook endpoint sends to {address}"
    endpoint = rows[0]
    if endpoint.get("status") != "enabled":
        return "the webhook endpoint is disabled"
    if endpoint.get("api_version") != API_VERSION:
        return f"the webhook endpoint's API version is not {API_VERSION}: make a new endpoint with that version"
    sent = set(endpoint.get("enabled_events") or [])
    missing = [] if "*" in sent else [event for event in EVENTS if event not in sent]
    return f"the webhook endpoint does not send {', '.join(missing)}" if missing else None


def check_secret(stripe: StripeBilling, settings: StripeSettings, origin: str | None) -> str | None:
    return None if settings.webhook_secret else "TJ_STRIPE_WEBHOOK_SECRET is not set (the endpoint's signing secret)"


CHECKS: tuple[tuple[str, Check], ...] = (
    ("product: Roundturn Pro, active, in the right mode", check_product),
    ("price: USD 15 a month, tax on top, no trial", check_price),
    ("customer portal: cancel at period end, no plan changes", check_portal),
    ("webhook endpoint: address, enabled, API version, events", check_webhook),
    ("webhook signing secret is set", check_secret),
)


def run(env: Mapping[str, str], *, http: httpx.Client | None = None, out: Callable[[str], Any] = print) -> int:
    try:
        settings = StripeSettings.from_env(env)
    except ValueError as problem:  # names the setting, never its value
        out(f"FAIL settings: {problem}")
        return 2
    if settings is None:
        out("FAIL settings: no TJ_STRIPE_* settings found")
        return 2
    origin = env.get("TJ_APP_ORIGIN", "").strip() or None
    try:
        stripe = StripeBilling(settings, app_origin=origin, http=http, sleep=lambda _seconds: None)
    except ValueError:
        out("FAIL settings: TJ_APP_ORIGIN must look like https://app.example.com")
        return 2
    mode = "Managed Payments (Stripe sells, as merchant of record)" if settings.managed_payments else "Roundturn sells"
    out(f"Stripe {settings.environment} mode, {mode}, API version {API_VERSION}")
    failed = 0
    for name, check in CHECKS:
        try:
            problem = check(stripe, settings, origin)
        except BillingError as error:
            problem = f"Stripe did not answer as expected ({error.reason})"
        if problem is None:
            out(f"PASS {name}")
        else:
            failed += 1
            out(f"FAIL {name}: {problem}")
    out("Ready: TJ_BILLING_PROVIDER=stripe can be switched on." if not failed else f"Not ready: {failed} to fix.")
    return 0 if not failed else 1


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments are skipped. Values are never printed."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--env-file", type=Path, help="a git-ignored KEY=VALUE file with the TJ_STRIPE_* settings")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.env_file is not None:
        try:
            env.update(read_env_file(args.env_file))
        except OSError:
            print(f"FAIL settings: cannot read {args.env_file.name}", file=sys.stderr)
            return 2
    return run(env)


if __name__ == "__main__":
    raise SystemExit(main())
