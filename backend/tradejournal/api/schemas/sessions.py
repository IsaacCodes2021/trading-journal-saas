"""A person's own sign-ins: what they may see, and what ending some of them reports."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import ApiModel


class ActiveSessionOut(ApiModel):
    id: str = Field(description="Opaque; pass it to DELETE /api/v1/sessions/{id}")
    current: bool = Field(description="The session making this request")
    device: str = Field(description='A short label such as "Chrome on Windows"; never the raw User-Agent')
    signed_in_at: datetime
    last_active_at: datetime
    expires_at: datetime = Field(description="When it ends whatever happens (90 days after it began)")
    mfa: bool = Field(description="Whether the sign-in used two-step verification")


class ActiveSessionsOut(ApiModel):
    sessions: list[ActiveSessionOut] = Field(description="Working sessions, most recently used first")
    ended: int = Field(description="How many this call ended (0 for a plain read)")
