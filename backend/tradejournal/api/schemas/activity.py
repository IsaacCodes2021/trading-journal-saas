"""A person's own recent security activity: what they may see of the audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import ApiModel

ActivityKind = Literal[
    "sign_up",
    "sign_in",
    "sign_in_refused",
    "sign_out",
    "other_sign_out",
    "sign_out_everywhere",
    "export",
    "deletion_requested",
    "deletion_cancelled",
]


class ActivityOut(ApiModel):
    at: datetime
    kind: ActivityKind = Field(description="What happened; the page chooses the words")
    ended: int | None = Field(
        default=None, description='For "sign_out_everywhere": how many other places were signed out'
    )
    mfa: bool | None = Field(
        default=None, description='For "sign_in" and "sign_up": whether two-step verification was used'
    )


class ActivityListOut(ApiModel):
    events: list[ActivityOut] = Field(description="This account's own events, newest first")
