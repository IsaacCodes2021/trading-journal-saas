"""What the single-page app learns about the signed-in user in its first call (doc 07 section 2)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, field_validator

from .base import ApiModel

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
Theme = Literal["dark", "light"]


def _timezone(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("not a timezone name such as 'America/New_York'") from None
    return value


def _csv_timezone(value: str | None) -> str | None:
    """A timezone name, or "" meaning "the same as my timezone" (a `null` would mean "leave it alone")."""
    return value if value == "" else _timezone(value)


def _hhmm(value: str | None) -> str | None:
    if value is not None and not _HHMM.fullmatch(value):
        raise ValueError("must be a 24-hour time such as '18:00'")
    return value


class UserOut(ApiModel):
    id: UUID
    email: str


class SettingsOut(ApiModel):
    timezone: str
    csv_timezone: str | None  # the zone CSV files are read in; null means the same as `timezone`
    trading_day_timezone: str
    trading_day_start: str  # HH:MM, wall time in trading_day_timezone
    theme: Theme


class SettingsPatch(ApiModel):
    """Only the fields present change; `null` means the same as leaving the field out."""

    model_config = ConfigDict(extra="forbid")  # a misspelled field is an error, not a silent no-op

    timezone: str | None = None
    csv_timezone: str | None = None  # "" goes back to following `timezone`
    trading_day_timezone: str | None = None
    trading_day_start: str | None = None
    theme: Theme | None = None

    _check_timezone = field_validator("timezone", "trading_day_timezone")(_timezone)
    _check_csv_timezone = field_validator("csv_timezone")(_csv_timezone)
    _check_start = field_validator("trading_day_start")(_hhmm)


class PlanOut(ApiModel):
    effective: str
    source: Literal["override", "grant", "subscription", "free"]
    expires_at: datetime | None


class FlagsOut(ApiModel):
    is_admin: bool
    mfa_fresh: bool  # the identity provider reported MFA within the last 15 minutes
    recently_authenticated: (
        bool  # they authenticated at the provider within the last 10 minutes (needed to delete the account)
    )


class AccessOut(ApiModel):
    """May the person change things? "ended" means their time-limited full access (a beta code, a promo code, the trial) ran
    out or was ended by an operator, or (with billing on) their subscription ended: they can still look at everything and
    download it, and cannot create, change or delete."""

    state: Literal["active", "ended"]
    ends_at: datetime | None  # when time-limited access ends (active) or ended (ended); none: it has no end date
    reason: (
        Literal["expired", "revoked", "lapsed"] | None
    )  # why, once ended ("lapsed": a subscription, billing on only)


class SessionOut(ApiModel):
    user: UserOut
    settings: SettingsOut
    plan: PlanOut
    access: AccessOut
    entitlements: dict[str, int | bool]
    flags: FlagsOut
    csrf_token: str  # sent back as X-CSRF-Token on every unsafe request
    app_version: str  # the build id; the SPA shows a "reload" banner when it differs from its own
