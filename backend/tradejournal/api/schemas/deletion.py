"""Deleting the account: what is asked, and what is promised."""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from .base import ApiModel


class DeletionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled field is an error, not ignored

    email: str = Field(
        max_length=320, description="The account's own email address, typed out: a guard against a stray call"
    )


class DeletionOut(ApiModel):
    scheduled_for: datetime = Field(
        description="When the account and everything in it will be deleted, unless the person signs in first"
    )
