"""Redeeming a plan code."""

from __future__ import annotations

from pydantic import Field

from .base import ApiModel


class RedeemRequest(ApiModel):
    code: str = Field(
        min_length=1, max_length=64, description="The plan code, as it was given (case and spaces do not matter)"
    )
