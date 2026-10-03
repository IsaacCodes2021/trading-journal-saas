"""Errors are RFC 9457 `application/problem+json` (doc 07 section 2)."""

from __future__ import annotations

from .base import ApiModel

PROBLEM_MEDIA_TYPE = "application/problem+json"


class FieldError(ApiModel):
    field: str
    message: str


class Problem(ApiModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    errors: list[FieldError] | None = None  # present on 422 only
