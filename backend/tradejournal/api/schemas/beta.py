"""The form that joins the beta."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints

from .base import ApiModel

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class BetaJoinIn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Name
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
    prop_firm: Name
    # A field no person sees or fills in (the page hides it). A script that fills in every field fills in this one.
    website: str = Field(default="", max_length=200)


class BetaJoinOut(ApiModel):
    status: Literal["ok"] = "ok"
