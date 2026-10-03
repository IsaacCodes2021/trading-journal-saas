"""Postgres storage for the hosted product: schema migrations, tenant sessions, and the ported domain code.

Spike S2 (docs/design/05-features-and-roadmap.md section 2) builds this next to the SQLite modules, which stay until the
port is complete (M2). Pure helpers (validation, constants, dataclasses) are shared with them; only the code that
talks to the database is rewritten here.
"""
