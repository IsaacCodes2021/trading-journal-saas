"""Redeeming a plan code."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from .base import ApiModel


class RedeemRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled field is an error, not ignored

    code: str = Field(
        min_length=1, max_length=64, description="The plan code, as it was given (case and spaces do not matter)"
    )
