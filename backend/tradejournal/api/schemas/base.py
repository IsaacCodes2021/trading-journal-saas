"""The base class every API model extends."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    # Responses always contain defaulted fields, so the *response* schema marks them required while the request
    # schema keeps them optional. This pairs with `openapi-typescript --default-non-nullable false` (doc 07 section 2):
    # without both, defaulted fields become required in request types or optional in response types.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
