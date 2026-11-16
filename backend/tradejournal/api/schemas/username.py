"""A person's username: what may be asked for, and what is answered (migration 0024)."""

from __future__ import annotations

import re

from pydantic import ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from .base import ApiModel

USERNAME_MIN = 3
USERNAME_MAX = 20
_SHAPE = re.compile(r"[A-Za-z0-9_]+")

# Names that could pass for the service or its staff. Refused like a name someone else holds, so nobody can claim them.
RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "api",
        "billing",
        "help",
        "me",
        "mod",
        "moderator",
        "null",
        "official",
        "operator",
        "root",
        "security",
        "settings",
        "staff",
        "support",
        "system",
        "undefined",
    }
)


def is_reserved(username: str) -> bool:
    """Whether nobody may hold this name: one of the reserved names, or anything with the product's name in it."""
    folded = username.casefold()
    return folded in RESERVED_USERNAMES or "roundturn" in folded


class UsernameIn(ApiModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled field is an error, not a silent no-op

    username: str = Field(
        description="3 to 20 letters, digits or underscores. Spaces around it are dropped. Unique whatever the capitals.",
        json_schema_extra={"minLength": USERNAME_MIN, "maxLength": USERNAME_MAX, "pattern": "^[A-Za-z0-9_]+$"},
    )

    @field_validator("username")
    @classmethod
    def _shape(cls, value: str) -> str:
        value = value.strip()
        if len(value) < USERNAME_MIN:
            raise PydanticCustomError("username_short", f"Use at least {USERNAME_MIN} characters.")
        if len(value) > USERNAME_MAX:
            raise PydanticCustomError("username_long", f"Use at most {USERNAME_MAX} characters.")
        if not _SHAPE.fullmatch(value):
            raise PydanticCustomError("username_shape", "Use only letters, numbers and underscores.")
        return value


class UsernameOut(ApiModel):
    username: str = Field(description="The username as saved, with the capitals it was typed with")


class UsernamePromptOut(ApiModel):
    ask_username: bool = Field(
        description="Whether the prompt is still to be shown (false once the person says not now)"
    )
