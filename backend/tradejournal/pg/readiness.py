"""The database half of /readyz: can this process serve requests, as far as the database is concerned?

Ready means the database answers within a couple of seconds and has applied every migration this image ships with. A
database that is newer than the image is fine (an old container still running during a deploy); an older one is not,
because the new code would meet a schema it does not know. The answer names a state, never an error message or an
address, since the endpoint is public.
"""

from __future__ import annotations

import logging

import psycopg

from .migrate import latest_version
from .session import Database

log = logging.getLogger("tradejournal.readiness")


class DatabaseReadiness:
    def __init__(self, database: Database, *, expected_version: str | None = None, timeout: float = 2.0) -> None:
        self._database = database
        self._expected = expected_version or latest_version()
        self._timeout = timeout

    def __call__(self) -> tuple[bool, dict[str, str]]:
        try:
            version = self._database.schema_version(self._timeout)
        except psycopg.errors.UndefinedFunction:
            return False, {"database": "schema_behind"}  # the database predates migration 0005
        except Exception as error:  # noqa: BLE001  (any failure means not ready; only its type is logged, never its text)
            log.warning("readiness: the database check failed (%s)", type(error).__name__)
            return False, {"database": "unreachable"}
        if version is None or version < self._expected:
            return False, {"database": "schema_behind"}
        return True, {"database": "ok"}
