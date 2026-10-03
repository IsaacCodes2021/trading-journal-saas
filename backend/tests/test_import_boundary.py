"""The import boundary (doc 03 section 1, doc 04 GATE): tenant routes cannot reach ``system_session``, and admin
routes cannot reach ``tenant_session``.

The two kinds of database access never mix in a route module. A tenant route that could open a system session could
read other users' accounts; an admin route that could open a tenant session could read one user's trades. The check
reads the source of every module under ``api/routers`` and resolves what it imports, relative imports included. It
runs without a database.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTERS = Path(__file__).resolve().parents[1] / "tradejournal" / "api" / "routers"
PACKAGE = "tradejournal.api.routers"
SYSTEM = "tradejournal.pg.system"
TENANT = "tradejournal.pg.session"


def imported_names(source: str, module: str) -> set[str]:
    """Every dotted name a module imports, resolved against `module`'s own dotted name.

    ``from ..pg import system`` is reported as both ``tradejournal.pg`` and ``tradejournal.pg.system``, because the
    imported name may be a submodule.
    """
    names: set[str] = set()
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*base_parts, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def reaches(names: set[str], target: str) -> bool:
    return any(name == target or name.startswith(target + ".") for name in names)


def is_admin_module(path: Path) -> bool:
    return path.stem.startswith("admin") or "admin" in path.relative_to(ROUTERS).parts[:-1]


def route_modules() -> list[Path]:
    return sorted(p for p in ROUTERS.rglob("*.py") if p.name != "__init__.py")


def dotted(path: Path) -> str:
    return PACKAGE + "." + ".".join(path.relative_to(ROUTERS).with_suffix("").parts)


# ---- the rule, applied to the real route modules --------------------------------------------------------------


def test_there_are_route_modules_to_check():
    assert route_modules(), "the boundary test is passing because it found nothing"


@pytest.mark.parametrize("path", route_modules(), ids=lambda p: p.name)
def test_route_modules_keep_to_their_side_of_the_boundary(path):
    names = imported_names(path.read_text(encoding="utf-8"), dotted(path))
    if is_admin_module(path):
        assert not reaches(names, TENANT), f"{path.name} is an admin route and imports the tenant session"
    else:
        assert not reaches(names, SYSTEM), f"{path.name} is a tenant route and imports the system session"


# ---- the checker itself: it must catch every way of writing the forbidden import ------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "from tradejournal.pg.system import system_session",
        "from tradejournal.pg import system",
        "from tradejournal.pg import system as s",
        "import tradejournal.pg.system",
        "import tradejournal.pg.system as s",
        "from ...pg.system import SystemDatabase",
        "from ...pg import system",
    ],
)
def test_the_checker_sees_every_way_to_import_the_system_session(source):
    names = imported_names(source, "tradejournal.api.routers.trades")
    assert reaches(names, SYSTEM), source


@pytest.mark.parametrize(
    "source",
    [
        "from tradejournal.pg.session import tenant_session",
        "from tradejournal.pg import session",
        "from ...pg.session import Database",
        "import tradejournal.pg.session as s",
    ],
)
def test_the_checker_sees_every_way_to_import_the_tenant_session(source):
    names = imported_names(source, "tradejournal.api.routers.admin_users")
    assert reaches(names, TENANT), source


@pytest.mark.parametrize(
    "source",
    [
        "from tradejournal.pg import accounts",
        "from ...pg.accounts import list_accounts",
        "from tradejournal.pg.systematic import x",  # a different module that merely starts with the same letters
        "import tradejournal.pg.sessions_view",
    ],
)
def test_the_checker_does_not_flag_unrelated_imports(source):
    names = imported_names(source, "tradejournal.api.routers.trades")
    assert not reaches(names, SYSTEM) and not reaches(names, TENANT), source


def test_a_nested_admin_package_counts_as_admin():
    nested = ROUTERS / "admin" / "users.py"
    assert is_admin_module(nested) and is_admin_module(ROUTERS / "admin_users.py")
    assert not is_admin_module(ROUTERS / "session.py")
