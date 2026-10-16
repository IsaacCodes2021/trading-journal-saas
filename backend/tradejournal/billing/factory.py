"""Which provider a configuration asks for (`TJ_BILLING_PROVIDER`)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .fake import FakeBilling
from .provider import BillingProvider


def provider_for(config, *, clock: Callable[[], datetime]) -> BillingProvider | None:
    """The provider named by the configuration, or None when none is. The configuration has already refused the fake one in
    production and a switch that is on with no provider."""
    if config.billing_provider == "fake":
        return FakeBilling(secret=config.fake_billing_secret, clock=clock)
    if config.billing_provider == "paddle":
        raise ValueError("TJ_BILLING_PROVIDER=paddle: the Paddle adapter is not in this build")
    return None


def provider_from_env(env) -> BillingProvider | None:
    """For a job that is not the web server (the sweep): the provider its own settings name, or None. The fake provider
    lives inside one process, so it is refused here."""
    name = env.get("TJ_BILLING_PROVIDER", "").strip().lower()
    if not name:
        return None
    if name == "fake":
        raise ValueError("TJ_BILLING_PROVIDER=fake cannot be used by a scheduled job")
    if name == "paddle":
        raise ValueError("TJ_BILLING_PROVIDER=paddle: the Paddle adapter is not in this build")
    raise ValueError("TJ_BILLING_PROVIDER must be 'fake' or 'paddle'")
