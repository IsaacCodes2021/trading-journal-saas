"""Connections for the few jobs that act without a tenant (doc 03 section 1), one narrow database role each.

    system = SystemDatabase({"login": login_url, "admin": admin_url})
    system.open()
    with system.system_session("login") as conn:
        conn.execute("SELECT id FROM users WHERE idp_subject = %s", (subject,))

The profiles are ``login`` (find or create a user at sign-in), ``webhook`` (billing events), ``sweep`` (background
jobs) and ``admin`` (the operator API). Each connects as its own role, whose grant list is in migration 0004: the
``admin`` role has no grant on any tenant table, so even a query bug cannot return a user's trades.

This module is the only way to reach those roles, and ``pg.session`` is the only way to reach ``app_rw``. Route
modules import one or the other, never both: tenant routes must not import this module, and admin routes must not
import ``tenant_session`` (``tests/test_import_boundary.py`` checks both).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Literal, get_args

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Profile = Literal["login", "webhook", "sweep", "admin"]
PROFILES: tuple[str, ...] = get_args(Profile)


class SystemDatabase:
    """One small pool per profile, each connecting as that profile's role."""

    def __init__(self, urls: Mapping[str, str], *, max_size: int = 3) -> None:
        unknown = set(urls) - set(PROFILES)
        if unknown:
            raise ValueError(f"unknown system profiles: {sorted(unknown)}; the profiles are {', '.join(PROFILES)}")
        self._pools = {
            profile: ConnectionPool(
                url,
                min_size=1,
                max_size=max_size,
                kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
                open=False,
            )
            for profile, url in urls.items()
        }

    def open(self) -> None:
        for pool in self._pools.values():
            pool.open(wait=True)

    def close(self) -> None:
        for pool in self._pools.values():
            pool.close()

    @contextmanager
    def system_session(self, profile: Profile) -> Iterator[psycopg.Connection]:
        """One transaction as one system role: committed on success, rolled back if the block raises."""
        if profile not in PROFILES:
            raise ValueError(f"unknown system profile {profile!r}; the profiles are {', '.join(PROFILES)}")
        pool = self._pools.get(profile)
        if pool is None:
            raise LookupError(f"no connection is configured for the {profile!r} profile")
        with pool.connection() as conn:
            yield conn
