"""Tenant isolation, route by route, generated from the OpenAPI document (doc 05 M3 exit criterion; doc 04 T1, R6).

Alice and Bob each have a full journal (accounts, imported trades, notes, a screenshot, categories and tags, fees, a
ledger, rules).
Then Bob calls **every route that names something by id**, using Alice's ids, and the suite checks four things:

1. **It is refused**: a 404, never a 2xx and never a 5xx. Also when only one of several references is Alice's and the
   rest are Bob's own (a trade of Bob's with a tag of Alice's, a tag of Bob's moved into a category of Alice's).
2. **It says nothing**: the answer is *the same* as for an id that does not exist at all (status and sentence), so Bob
   cannot learn whether an id belongs to someone.
3. **Nothing changed**: Alice's journal and Bob's are exactly as they were (no row changed, none linked, none made),
   and the folder of image files holds the same files.
4. **Nothing leaked**: no word of Alice's (the names she gave her things, her notes, her file name) appears in any
   answer Bob gets, including the list routes called with Alice's ids in their filters and the export.

The routes come from `openapi.json`, so a route added later is attacked without anyone remembering to add it. A route
that names an id this file does not know how to supply fails the suite (`test_every_route_is_accounted_for`) instead of
being skipped. A control repeats every attack as Bob on his own ids, to prove the requests themselves are good: if the
bodies were wrong, "refused" would prove nothing.

Sessions here are the in-memory fakes (the real store's isolation is in `test_pg_sessions.py`); everything below the
route is real: the request role, row-level security and the queries.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from api_fakes import FakeAudit, FakeSessions
from fastapi.testclient import TestClient
from helpers import SAMPLE, fixture_bytes
from pg_helpers import NOON_UTC, ORIGIN, PNG_1X1, SECRET, write_headers

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE, Tenant
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.ports import Services
from tradejournal.pg.session import STATEMENT_TIMEOUT_MS

DOCUMENT = json.loads((Path(__file__).resolve().parents[2] / "openapi.json").read_text(encoding="utf-8"))
SCHEMAS = DOCUMENT["components"]["schemas"]

NOWHERE = 2**62  # a valid row id that no one has
DAY = "2024-01-05"  # the day both people write a day note for: the same key, the person is what tells them apart
MONEY_PATTERN = r"^-?\d+(\.\d+)?$"
SYMBOL_ROOT = "NQ"

# What each id-shaped name in a path or a body stands for in the worlds below.
OWNED = {
    "account_id": "account",
    "trade_id": "trade",
    "import_id": "import",
    "group_id": "group",
    "tag_id": "tag",
    "into_id": "tag2",  # the tag a merge moves trades into
    "entry_id": "entry",
    "screenshot_id": "screenshot",
}
# Names that look like ids but are not rows of the person's journal, and why they are not attacked here.
NOT_JOURNAL_ROWS = {
    "session_id": "a sign-in session: the real store's isolation is tested in test_pg_sessions.py",
}
# Filters that take ids in the query string. They are named in Bob's reads and added to every attack on a route that has
# them, so a filter cannot be a way in.
QUERY_IDS = {"account_ids": "account"}
# Write routes that name nothing of anyone's (so there is no foreign id to try). A new write route must be added here on
# purpose, or given an id the suite knows: it cannot slip past unnoticed.
NAMES_NOTHING = {
    ("POST", "/api/v1/account/deletion"),
    ("POST", "/api/v1/accounts"),
    ("POST", "/api/v1/grants/redeem"),
    ("POST", "/api/v1/tag-groups"),
    ("DELETE", "/api/v1/sessions"),
    ("PATCH", "/api/v1/settings"),
    ("PUT", "/api/v1/day-notes/{day}"),  # keyed by the person and the date: tested by name below
}
# Routes that name a session, which is not a journal row (see NOT_JOURNAL_ROWS).
SESSION_ROUTES = {("DELETE", "/api/v1/sessions/{session_id}")}

# Fixed values for fields where "x" is not a valid value.
FIELD_VALUES = {"color": "#3b82f6", "html": "<p>changed</p>"}


@dataclass
class Operation:
    method: str
    path: str
    path_params: list[str]
    query_params: list[str]
    body: dict | None  # the JSON schema of the body, when it is JSON
    multipart: bool
    form_fields: list[str]  # the fields of a multipart body ("file", and "account_id" on an import)

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def references(self) -> list[str]:
        """The id-shaped names this route takes, in path order and then body order."""
        names = list(self.path_params)
        if self.multipart:
            names += [f for f in self.form_fields if f in OWNED]
        elif self.body:
            names += list(self.body.get("properties", {}))
        return [n for n in names if n in OWNED]

    def id_like(self) -> list[str]:
        names = list(self.path_params) + list(self.query_params)
        if self.body:
            names += list(self.body.get("properties", {}))
        return [n for n in names if re.fullmatch(r"(?:[a-z]+_)*ids?", n)]


def _deref(schema: dict) -> dict:
    return SCHEMAS[schema["$ref"].rsplit("/", 1)[-1]] if "$ref" in schema else schema


def operations() -> list[Operation]:
    found = []
    for path, methods in sorted(DOCUMENT["paths"].items()):
        if not path.startswith("/api/v1"):
            continue
        for method, op in methods.items():
            parameters = op.get("parameters", [])
            request = op.get("requestBody")
            content = next(iter(request["content"].items())) if request else None
            multipart = bool(content and content[0].startswith("multipart"))
            found.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    path_params=[p["name"] for p in parameters if p["in"] == "path"],
                    query_params=[p["name"] for p in parameters if p["in"] == "query"],
                    body=_deref(content[1]["schema"]) if content and content[0] == "application/json" else None,
                    multipart=multipart,
                    form_fields=list(_deref(content[1]["schema"]).get("properties", {})) if multipart else [],
                )
            )
    return found


ALL = operations()
BY_KEY = {op.key: op for op in ALL}
ATTACKS = [op for op in ALL if op.references]
READS = [op for op in ALL if op.method == "GET" and not op.references and not op.path_params]


# ---- a journal ----------------------------------------------------------------------------------------------------


@dataclass
class World:
    name: str
    user_id: object
    client: object
    ids: dict[str, int]
    markers: list[str]  # words only this person's journal contains
    files_dir: Path  # where the screenshots are kept (one folder for both people: their keys differ by person)


def _ok(response, what: str, *, status: int | tuple[int, ...] = (200, 201)):
    codes = (status,) if isinstance(status, int) else status
    assert response.status_code in codes, f"{what}: {response.status_code} {response.text[:300]}"
    return response


def client_for(db, system, user_id, files_dir: Path) -> TestClient:
    """A browser signed in as `user_id`, on an app with the real accounts service (export and /session need it)."""
    sessions = FakeSessions()
    token, session = sessions.sign_in(user_id)
    services = Services(sessions=sessions, accounts=PgAccountService(db, system), audit=FakeAudit())
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=Path("/nonexistent"),
        session_secret=SECRET,
        app_version="v-test",
        files_dir=files_dir,
    )
    app = create_app(config, services, database=db, clock=lambda: NOON_UTC)
    return TestClient(app, base_url=ORIGIN, headers={"cookie": f"{SESSION_COOKIE}={token}", **write_headers(session)})


def image_of(name: str) -> bytes:
    """A real PNG with the person's word at the end of it, so an image that reaches the wrong person is caught by its text."""
    return PNG_1X1 + f"{name}-IMAGE".encode()


def stored_files(files_dir: Path) -> list[str]:
    return sorted(p.relative_to(files_dir).as_posix() for p in files_dir.rglob("*") if p.is_file())


def build_world(db, system, user_id, name: str, files_dir: Path) -> World:
    """A person's whole journal, made through the API."""
    client = client_for(db, system, user_id, files_dir)
    account = _ok(
        client.post(
            "/api/v1/accounts",
            json={
                "name": f"{name}-ACCOUNT",
                "firm": "Apex",
                "account_type": "evaluation",
                "account_size": "50000.00",
                "starting_balance": "50000.00",
            },
        ),
        "account",
    ).json()["id"]
    imported = _ok(
        client.post(
            "/api/v1/imports",
            data={"account_id": str(account)},
            files={"file": (f"{name}-FILE.csv", fixture_bytes(SAMPLE), "text/csv")},
        ),
        "import",
    ).json()["id"]
    trade = _ok(client.get("/api/v1/trades", params={"limit": 5}), "trades").json()["items"][0]["id"]
    _ok(client.post("/api/v1/tag-groups", json={"name": f"{name}-GROUP"}), "group")
    group = next(g["id"] for g in client.get("/api/v1/tags").json()["groups"] if g["name"] == f"{name}-GROUP")
    for tag_name in (f"{name}-TAG", f"{name}-OTHERTAG"):
        _ok(client.post("/api/v1/tags", json={"group_id": group, "name": tag_name}), tag_name)
    tags = {t["name"]: t["id"] for t in client.get("/api/v1/tags").json()["tags"]}
    tag, tag2 = tags[f"{name}-TAG"], tags[f"{name}-OTHERTAG"]
    _ok(client.put(f"/api/v1/trades/{trade}/notes", json={"html": f"<p>{name}-NOTE</p>"}), "note")
    _ok(client.put(f"/api/v1/day-notes/{DAY}", json={"html": f"<p>{name}-DAYNOTE</p>"}), "day note")
    screenshot = _ok(
        client.post(
            f"/api/v1/trades/{trade}/screenshots", files={"file": (f"{name}-SHOT.png", image_of(name), "image/png")}
        ),
        "screenshot",
    ).json()["id"]
    _ok(client.put(f"/api/v1/trades/{trade}/tags/{tag}"), "tag a trade", status=(200, 204))
    _ok(client.put(f"/api/v1/accounts/{account}/fees/{SYMBOL_ROOT}", json={"rate": "1.50"}), "fee")
    entry = _ok(
        client.post(
            f"/api/v1/accounts/{account}/ledger",
            json={"kind": "evaluation_fee", "amount": "100.00", "entry_date": "2024-01-02", "note": f"{name}-LEDGER"},
        ),
        "ledger",
    ).json()["id"]
    _ok(
        client.put(
            f"/api/v1/accounts/{account}/rules",
            json={
                "profit_target": "3000.00",
                "max_drawdown": "2000.00",
                "trailing_type": "eod",
                "trail_lock_enabled": False,
                "trail_lock_level": None,
                "daily_loss_limit": None,
            },
        ),
        "rules",
    )
    ids = {
        "account": account,
        "import": imported,
        "trade": trade,
        "group": group,
        "tag": tag,
        "tag2": tag2,
        "entry": entry,
        "screenshot": screenshot,
    }
    return World(name, user_id, client, ids, [f"{name}-"], files_dir)


def snapshot(world: World) -> dict:
    """Everything a person can read about their own journal, so two snapshots can be compared."""
    c, ids = world.client, world.ids
    reads = {
        "accounts": "/api/v1/accounts",
        "overviews": "/api/v1/account-overviews",
        "totals": "/api/v1/business-totals",
        "tags": "/api/v1/tags",
        "trades": "/api/v1/trades?limit=200",
        "trade": f"/api/v1/trades/{ids['trade']}",
        "imports": "/api/v1/imports",
        "fees": f"/api/v1/accounts/{ids['account']}/fees",
        "ledger": f"/api/v1/accounts/{ids['account']}/ledger",
        "dashboard": "/api/v1/dashboard",
        "day note": f"/api/v1/day-notes/{DAY}",
        "days with notes": "/api/v1/day-notes?start=2024-01-01&end=2024-12-31",
    }
    return {name: c.get(url).json() for name, url in reads.items()}


# ---- building a request from the document -------------------------------------------------------------------------


def _example(schema: dict, field: str | None, refs: dict[str, int]):
    schema = _deref(schema)
    if field in OWNED:
        return refs[field]
    if field in FIELD_VALUES:
        return FIELD_VALUES[field]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return _example(next(s for s in schema["anyOf"] if s.get("type") != "null"), field, refs)
    if schema.get("pattern") == MONEY_PATTERN:
        return "10.00"  # the document's example for money is negative, which no amount a person types may be
    if schema.get("examples"):
        return schema["examples"][0]
    kind = schema.get("type")
    if kind == "object":
        return {
            k: _example(v, k, refs) for k, v in schema.get("properties", {}).items() if k in schema.get("required", [])
        }
    if kind == "string":
        return "2024-01-02" if schema.get("format") == "date" else "x" * max(1, schema.get("minLength", 1))
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    raise AssertionError(f"no example for {schema}")


def send(client, op: Operation, refs: dict[str, int], *, filter_ids: int | None = None, name: str = "ATTACKER"):
    """`filter_ids` is an account id to put in the `account_ids` filter of a route that has one."""
    url = op.path
    if filter_ids is not None and "account_ids" in op.query_params:
        url += f"?account_ids={filter_ids}"
    for param in op.path_params:
        url = url.replace("{" + param + "}", SYMBOL_ROOT if param == "symbol_root" else str(refs[param]))
    if op.multipart and "account_id" in op.form_fields:
        return client.request(
            op.method,
            url,
            data={"account_id": str(refs["account_id"])},
            files={"file": (f"{name}-ATTACK.csv", fixture_bytes(SAMPLE), "text/csv")},
        )
    if op.multipart:  # a screenshot
        return client.request(op.method, url, files={"file": (f"{name}-ATTACK.png", image_of(name), "image/png")})
    if op.body is not None:
        return client.request(op.method, url, json=_example(op.body, None, refs))
    return client.request(op.method, url)


def sentence(response) -> tuple[int, object]:
    """What an answer tells the caller, with every id in it blanked, so two answers can be compared."""
    try:
        body = response.json()
    except ValueError:
        body = response.text
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, str):
        detail = re.sub(r"\d{3,}", "<id>", detail)
    return response.status_code, detail


# ---- the suite ----------------------------------------------------------------------------------------------------


@pytest.fixture
def worlds(db, system, alice, bob, tmp_path):
    files_dir = tmp_path / "files"
    return build_world(db, system, alice, "ALICE", files_dir), build_world(db, system, bob, "BOB", files_dir)


def _scenarios(op: Operation, alice: World, bob: World):
    """Every way of naming Alice's rows: all of the references, and (when there are several) each one alone with the
    rest Bob's own. Each comes with a twin that names ids nobody has."""
    refs = op.references
    mine, theirs = bob.ids, alice.ids
    yield "all of Alice's", {r: theirs[OWNED[r]] for r in refs}, {r: NOWHERE for r in refs}
    if len(refs) > 1:
        for alone in refs:
            attack = {r: (theirs if r == alone else mine)[OWNED[r]] for r in refs}
            twin = {r: (NOWHERE if r == alone else mine[OWNED[r]]) for r in refs}
            yield f"only {alone} Alice's", attack, twin


def _words_of_the_other(response, other: World) -> list[str]:
    text = response.text
    return [m for m in other.markers if m in text]


def attack(op: Operation, alice: World, bob: World) -> list[str]:
    """Bob names Alice's rows in every way there is. Returns what went wrong, in words (nothing, when isolation holds)."""
    before = snapshot(alice), snapshot(bob)
    files_before = stored_files(alice.files_dir)
    problems: list[str] = []
    for label, attack, twin in _scenarios(op, alice, bob):
        # The twin (ids nobody has) is sent first: it is what a refusal for "no such row" looks like.
        refused_for_nothing = send(bob.client, op, _full(op, bob, twin), filter_ids=NOWHERE)
        response = send(bob.client, op, _full(op, bob, attack), filter_ids=alice.ids["account"])
        where = f"{op.method} {op.path} ({label})"
        if not 400 <= response.status_code < 500:
            problems.append(f"{where}: Bob got {response.status_code}, not a refusal: {response.text[:200]}")
        elif label == "all of Alice's" and response.status_code != 404:
            problems.append(f"{where}: Bob got {response.status_code}, not 404: {response.text[:200]}")
        if sentence(response) != sentence(refused_for_nothing):
            problems.append(
                f"{where}: Bob learns something: {sentence(response)} for Alice's id, "
                f"{sentence(refused_for_nothing)} for an id nobody has"
            )
        leaked = _words_of_the_other(response, alice)
        if leaked:
            problems.append(f"{where}: Alice's words {leaked} are in Bob's answer")
    after = snapshot(alice), snapshot(bob)
    if before[0] != after[0]:
        problems.append(f"{op.method} {op.path}: Alice's journal changed")
    if before[1] != after[1]:
        problems.append(f"{op.method} {op.path}: Bob's journal changed")
    if stored_files(alice.files_dir) != files_before:
        problems.append(f"{op.method} {op.path}: the image files changed (one was kept or removed)")
    return problems


@pytest.mark.parametrize("op", ATTACKS, ids=lambda op: f"{op.method} {op.path}")
def test_bob_cannot_reach_alices_rows_by_naming_their_ids(op, worlds):
    problems = attack(op, *worlds)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("op", ATTACKS, ids=lambda op: f"{op.method} {op.path}")
def test_the_suite_can_fail_it_catches_a_server_that_runs_bobs_requests_as_alice(op, worlds, monkeypatch):
    """A suite that cannot fail proves nothing. With a (deliberate) bug that runs every request as Alice, each attack must
    be reported."""
    alice, bob = worlds
    monkeypatch.setattr(
        Tenant,
        "session",
        lambda self, *, snapshot=False, statement_timeout_ms=STATEMENT_TIMEOUT_MS: self.database.tenant_session(
            alice.user_id, snapshot=snapshot, statement_timeout_ms=statement_timeout_ms
        ),
    )
    assert attack(op, alice, bob), f"{op.method} {op.path}: Bob read or changed Alice's journal and nothing noticed"


def _full(op: Operation, world: World, overrides: dict[str, int]) -> dict[str, int]:
    """Every id the route needs: the person's own, except where `overrides` names another."""
    return {r: world.ids[OWNED[r]] for r in op.references} | overrides


@pytest.mark.parametrize("op", ATTACKS, ids=lambda op: f"{op.method} {op.path}")
def test_control_the_same_requests_work_on_bobs_own_rows(op, worlds):
    """If these bodies were wrong the attacks above would be refused for that reason and prove nothing."""
    _, bob = worlds
    if op.key == ("POST", "/api/v1/accounts/{account_id}/restore"):
        _ok(
            bob.client.post(
                f"/api/v1/accounts/{bob.ids['account']}/archive",
                json={"outcome": "closed", "ended_on": "2024-02-01", "note": ""},
            ),
            "archive first",
        )
    response = send(bob.client, op, _full(op, bob, {}))
    assert response.status_code < 300, f"{op.method} {op.path}: {response.status_code} {response.text[:300]}"


def _text_of(response) -> str:
    if response.headers.get("content-type", "").startswith("application/zip"):
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return "\n".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())
    return response.text


@pytest.mark.parametrize("op", READS, ids=lambda op: f"{op.method} {op.path}")
def test_what_bob_reads_never_holds_alices_words_even_when_he_names_her_rows(op, worlds):
    alice, bob = worlds
    params: dict[str, object] = {}
    if "account_ids" in op.query_params:
        params["account_ids"] = [alice.ids["account"]]
    if "q" in op.query_params:
        params["q"] = "ALICE-NOTE"
    if op.path == "/api/v1/day-notes":  # a window that holds the day both people wrote a note for
        params.update(start="2024-01-01", end="2024-12-31")
    response = bob.client.get(op.path, params=params)
    assert response.status_code < 500, f"{op.path}: {response.status_code}"
    text = _text_of(response)
    assert not [m for m in alice.markers if m in text], f"{op.path} holds words of Alice's: {text[:300]}"
    if op.path == "/api/v1/export":
        assert "BOB-" in text  # the control: Bob's own export has his words in it, so a clean answer means something


def test_reading_alices_own_routes_does_show_her_words_so_the_check_above_can_fail(worlds):
    alice, bob = worlds
    for path in ("/api/v1/accounts", "/api/v1/tags", "/api/v1/imports", "/api/v1/export"):
        assert any(m in _text_of(alice.client.get(path)) for m in alice.markers), path
    assert "ALICE-" not in _text_of(bob.client.get("/api/v1/accounts"))


# ---- day notes (keyed by the person and a date, not by a row id, so the document-driven attacks do not name them) -------


def test_bob_reads_his_own_day_note_for_the_same_date_and_never_alices(worlds):
    alice, bob = worlds
    mine = alice.client.get(f"/api/v1/day-notes/{DAY}").json()["notes_html"]
    theirs = bob.client.get(f"/api/v1/day-notes/{DAY}").json()["notes_html"]
    assert "ALICE-DAYNOTE" in mine and "BOB-DAYNOTE" in theirs  # the control: the same date holds a note for each
    assert "ALICE-" not in theirs and "BOB-" not in mine


def test_bob_writing_or_emptying_a_day_note_never_touches_alices(worlds):
    alice, bob = worlds
    before = snapshot(alice)
    bob.client.put(f"/api/v1/day-notes/{DAY}", json={"html": "<p>BOB-changed</p>"})
    bob.client.put(f"/api/v1/day-notes/{DAY}", json={"html": ""})
    assert snapshot(alice) == before and "BOB-" not in json.dumps(snapshot(alice))


def test_the_day_note_check_can_fail_a_server_that_runs_bobs_request_as_alice_shows_her_note(worlds, monkeypatch):
    alice, bob = worlds
    monkeypatch.setattr(
        Tenant,
        "session",
        lambda self, *, snapshot=False, statement_timeout_ms=STATEMENT_TIMEOUT_MS: self.database.tenant_session(
            alice.user_id, snapshot=snapshot, statement_timeout_ms=statement_timeout_ms
        ),
    )
    assert "ALICE-DAYNOTE" in bob.client.get(f"/api/v1/day-notes/{DAY}").json()["notes_html"]
    assert bob.client.get("/api/v1/day-notes", params={"start": "2024-01-01", "end": "2024-12-31"}).json()["days"] == [
        DAY
    ]


# ---- the image route (it is not under /api/v1, so the document-driven attacks above do not reach it) ----------------


def test_bob_cannot_fetch_alices_image_and_cannot_tell_that_it_exists(worlds):
    alice, bob = worlds
    assert alice.client.get(f"/files/{alice.ids['screenshot']}").content == image_of("ALICE")  # the control
    assert bob.client.get(f"/files/{bob.ids['screenshot']}").content == image_of("BOB")
    theirs = bob.client.get(f"/files/{alice.ids['screenshot']}")
    nothing = bob.client.get(f"/files/{NOWHERE}")
    assert theirs.status_code == 404
    assert sentence(theirs) == sentence(nothing)
    assert b"ALICE-" not in theirs.content


def test_the_image_check_can_fail_a_server_that_runs_bobs_request_as_alice_serves_her_image(worlds, monkeypatch):
    alice, bob = worlds
    monkeypatch.setattr(
        Tenant,
        "session",
        lambda self, *, snapshot=False, statement_timeout_ms=STATEMENT_TIMEOUT_MS: self.database.tenant_session(
            alice.user_id, snapshot=snapshot, statement_timeout_ms=statement_timeout_ms
        ),
    )
    assert bob.client.get(f"/files/{alice.ids['screenshot']}").content == image_of("ALICE")


def test_every_route_is_accounted_for():
    """Each route is attacked (it names a row), read (a list), or in the short list of ones that name nothing."""
    known = set(OWNED) | set(NOT_JOURNAL_ROWS) | set(QUERY_IDS)
    unknown = {op.key: op.id_like() for op in ALL if set(op.id_like()) - known}
    assert not unknown, f"routes name ids this suite cannot supply: {unknown}"
    unaccounted = [
        op.key
        for op in ALL
        if op.method != "GET" and not op.references and op.key not in NAMES_NOTHING and op.key not in SESSION_ROUTES
    ]
    assert not unaccounted, f"write routes that name nothing and are not listed in NAMES_NOTHING: {unaccounted}"
    stale = [key for key in NAMES_NOTHING | SESSION_ROUTES if key not in BY_KEY]
    assert not stale, f"routes listed here that no longer exist: {stale}"
    assert {op.key for op in ATTACKS} >= {
        ("PUT", "/api/v1/trades/{trade_id}/notes"),
        ("DELETE", "/api/v1/accounts/{account_id}/ledger/{entry_id}"),
        ("POST", "/api/v1/tags/{tag_id}/merge"),
        ("POST", "/api/v1/imports"),
    }
    assert len(ATTACKS) >= 25, "the document has lost most of its routes"
