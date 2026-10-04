"""How many imports run at once (api/slots.py): the unit, the dependency through the real app, and the configuration."""

from __future__ import annotations

import importlib
import pkgutil
import sys
import threading
import time
from uuid import uuid4

import pytest
from api_fakes import make_services
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tradejournal.api import routers
from tradejournal.api.app import create_app
from tradejournal.api.config import MAX_CONCURRENT_IMPORTS, ApiConfig, ConfigError
from tradejournal.api.deps import SESSION_COOKIE, ImportSlot, hold_import_slot
from tradejournal.api.slots import BUSY, MINE, RETRY_AFTER_SECONDS, ImportSlots

ORIGIN = "https://app.example.com"


# ---- the unit ----------------------------------------------------------------------------------------------------


def test_a_person_gets_a_place_and_gives_it_back():
    slots = ImportSlots(2)
    ann = uuid4()
    assert slots.take(ann) is None
    assert slots.running == 1
    slots.release(ann)
    assert slots.running == 0


def test_a_person_runs_one_at_a_time():
    slots = ImportSlots(5)
    ann = uuid4()
    assert slots.take(ann) is None
    assert slots.take(ann) == MINE
    assert slots.running == 1  # the refusal took nothing
    slots.release(ann)
    assert slots.take(ann) is None


def test_the_total_is_a_ceiling_and_the_refusal_says_the_server_is_busy():
    slots = ImportSlots(2)
    ann, bob, cy = uuid4(), uuid4(), uuid4()
    assert (slots.take(ann), slots.take(bob)) == (None, None)
    assert slots.take(cy) == BUSY
    assert slots.running == 2
    slots.release(bob)
    assert slots.take(cy) is None  # a place came back


def test_a_person_who_has_a_place_is_told_so_even_when_the_server_is_full():
    slots = ImportSlots(1)
    ann = uuid4()
    slots.take(ann)
    assert slots.take(ann) == MINE  # not "busy": the useful answer for them is that their own import is running


def test_releasing_a_place_that_was_never_taken_is_harmless():
    slots = ImportSlots(1)
    slots.release(uuid4())
    assert slots.running == 0
    assert slots.take(uuid4()) is None


def test_a_limit_below_one_is_refused():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            ImportSlots(bad)


def test_the_two_refusals_say_different_things_and_neither_is_empty():
    assert MINE != BUSY
    assert MINE and BUSY
    assert RETRY_AFTER_SECONDS == 5


class _SlowToCount(set):
    """A set that takes a moment to say how big it is, which is exactly the moment a second thread needs to sneak in
    between "is there room?" and "now there is one more"."""

    def __len__(self) -> int:
        time.sleep(0.01)
        return super().__len__()


def test_looking_and_taking_are_one_step_so_a_slow_look_cannot_let_extra_people_in():
    slots = ImportSlots(3)
    slots._running = _SlowToCount()
    people = [uuid4() for _ in range(24)]
    results: list[str | None] = []
    lock = threading.Lock()
    together = threading.Barrier(len(people))

    def go(person):
        together.wait()  # everyone asks at the same moment
        answer = slots.take(person)
        with lock:
            results.append(answer)

    threads = [threading.Thread(target=go, args=(p,)) for p in people]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(None) == 3
    assert results.count(BUSY) == 21


@pytest.fixture
def switch_often():
    """Ask the interpreter to switch threads as often as it can, so a missing lock shows up as a race."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)


def test_a_crowd_taking_at_once_never_gets_more_places_than_there_are(switch_often):
    slots = ImportSlots(5)
    people = [uuid4() for _ in range(200)]
    granted: list[object] = []
    peak = 0
    lock = threading.Lock()
    start = threading.Barrier(len(people))

    def go(person):
        nonlocal peak
        start.wait()
        if slots.take(person) is None:
            with lock:
                granted.append(person)
                peak = max(peak, slots.running)

    threads = [threading.Thread(target=go, args=(p,)) for p in people]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 5
    assert peak <= 5
    assert slots.running == 5


# ---- through the real app ----------------------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path):
    services, sessions, accounts = make_services()

    def build(total: int):
        config = ApiConfig(
            app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=b"k" * 32, max_concurrent_imports=total
        )
        app = create_app(config, services)
        held = threading.Event()  # set when a request is inside the route
        let_go = threading.Event()  # set by the test to finish it

        @app.post("/api/v1/slow", dependencies=[ImportSlot])
        def slow() -> dict:
            held.set()
            assert let_go.wait(10)
            return {"ok": True}

        @app.post("/api/v1/quick", dependencies=[ImportSlot])
        def quick() -> dict:
            return {"ok": True}

        @app.post("/api/v1/breaks", dependencies=[ImportSlot])
        def breaks() -> dict:
            raise RuntimeError("the import failed halfway")

        return TestClient(app, base_url=ORIGIN, raise_server_exceptions=False), app, held, let_go

    def person(email):
        account = accounts.add(email)
        token, _ = sessions.sign_in(account.user.id)
        return {"cookie": f"{SESSION_COOKIE}={token}"}

    return build, person


def _in_background(client, path, headers):
    box: dict = {}

    def run():
        box["response"] = client.post(path, headers=headers)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


def test_while_a_person_has_an_import_running_a_second_is_refused_with_when_to_come_back(world):
    build, person = world
    client, _, held, let_go = build(total=3)
    ann = person("ann@example.test")
    thread, box = _in_background(client, "/api/v1/slow", ann)
    assert held.wait(5)
    refused = client.post("/api/v1/quick", headers=ann)
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
    assert refused.headers["content-type"] == "application/problem+json"
    assert refused.json()["detail"] == MINE
    let_go.set()
    thread.join(10)
    assert box["response"].status_code == 200
    assert client.post("/api/v1/quick", headers=ann).status_code == 200  # the place came back


def test_when_the_server_is_full_someone_else_is_refused_and_told_it_is_busy(world):
    build, person = world
    client, _, held, let_go = build(total=1)
    ann, bob = person("ann@example.test"), person("bob@example.test")
    thread, _ = _in_background(client, "/api/v1/slow", ann)
    assert held.wait(5)
    refused = client.post("/api/v1/quick", headers=bob)
    assert (refused.status_code, refused.json()["detail"]) == (429, BUSY)
    let_go.set()
    thread.join(10)
    assert client.post("/api/v1/quick", headers=bob).status_code == 200


def test_two_people_can_run_at_once_when_two_are_allowed(world):
    build, person = world
    client, _, held, let_go = build(total=2)
    ann, bob = person("ann@example.test"), person("bob@example.test")
    thread, _ = _in_background(client, "/api/v1/slow", ann)
    assert held.wait(5)
    assert client.post("/api/v1/quick", headers=bob).status_code == 200
    let_go.set()
    thread.join(10)


def test_a_refused_request_never_runs_the_route(world):
    build, person = world
    client, _, held, let_go = build(total=1)
    ann, bob = person("ann@example.test"), person("bob@example.test")
    thread, _ = _in_background(client, "/api/v1/slow", ann)
    assert held.wait(5)
    held.clear()
    assert client.post("/api/v1/slow", headers=bob).status_code == 429
    assert not held.is_set()  # the second `slow` never started
    let_go.set()
    thread.join(10)


def test_a_place_is_given_back_when_the_import_fails(world):
    build, person = world
    client, app, _, _ = build(total=1)
    ann = person("ann@example.test")
    assert client.post("/api/v1/breaks", headers=ann).status_code == 500
    assert app.state.import_slots.running == 0
    assert client.post("/api/v1/quick", headers=ann).status_code == 200


def test_a_place_is_given_back_when_another_check_on_the_route_refuses(world, tmp_path):
    build, person = world
    client, app, _, _ = build(total=1)
    ann = person("ann@example.test")
    from fastapi import Depends, HTTPException

    def refuses() -> None:
        raise HTTPException(403, "no")

    @app.post("/api/v1/checked", dependencies=[ImportSlot, Depends(refuses)])
    def checked() -> dict:
        return {}

    assert client.post("/api/v1/checked", headers=ann).status_code == 403
    assert app.state.import_slots.running == 0


def test_a_stranger_is_turned_away_before_taking_a_place(world):
    build, _ = world
    client, app, _, _ = build(total=1)
    assert client.post("/api/v1/quick").status_code == 401
    assert client.post("/api/v1/quick", headers={"cookie": f"{SESSION_COOKIE}=nope"}).status_code == 401
    assert app.state.import_slots.running == 0


def test_the_slot_limit_does_not_depend_on_the_rate_limit_switch(world, tmp_path):
    build, person = world
    from tradejournal.api.ratelimit import RateLimits

    services, sessions, accounts = make_services()
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=tmp_path / "none",
        session_secret=b"k" * 32,
        max_concurrent_imports=1,
        rate_limits=RateLimits(enabled=False),
    )
    app = create_app(config, services)
    held, let_go = threading.Event(), threading.Event()

    @app.post("/api/v1/slow", dependencies=[ImportSlot])
    def slow() -> dict:
        held.set()
        let_go.wait(10)
        return {}

    client = TestClient(app, base_url=ORIGIN)
    account = accounts.add("ann@example.test")
    headers = {"cookie": f"{SESSION_COOKIE}={sessions.sign_in(account.user.id)[0]}"}
    thread, _ = _in_background(client, "/api/v1/slow", headers)
    assert held.wait(5)
    assert client.post("/api/v1/slow", headers=headers).status_code == 429
    let_go.set()
    thread.join(10)


# ---- which routes ------------------------------------------------------------------------------------------------


def test_exactly_the_four_heavy_import_routes_hold_a_place():
    # The routers are read directly: the app holds them as included routers, whose routes are built when it is served.
    held = set()
    for info in pkgutil.iter_modules(routers.__path__):
        module = importlib.import_module(f"{routers.__name__}.{info.name}")
        for name in ("router", "public"):
            for route in getattr(getattr(module, name, None), "routes", []):
                if isinstance(route, APIRoute) and hold_import_slot in {d.call for d in route.dependant.dependencies}:
                    held.update((method, f"{route.path}") for method in route.methods)
    assert held == {
        ("POST", "/api/v1/imports/preview"),
        ("POST", "/api/v1/imports"),
        ("POST", "/api/v1/imports/{import_id}/undo-preview"),
        ("POST", "/api/v1/imports/{import_id}/undo"),
    }


# ---- the configuration -------------------------------------------------------------------------------------------


def test_two_imports_at_once_is_the_default():
    assert ApiConfig().max_concurrent_imports == 2


def test_the_app_is_built_with_the_configured_number(tmp_path):
    app = create_app(ApiConfig(web_dist=tmp_path / "none", max_concurrent_imports=7))
    assert app.state.import_slots.total == 7


def test_it_is_read_from_the_environment():
    assert (
        ApiConfig.from_env({"TJ_MAX_CONCURRENT_IMPORTS": "4", "TJ_SESSION_SECRET": "ab" * 32}).max_concurrent_imports
        == 4
    )
    assert (
        ApiConfig.from_env({"TJ_MAX_CONCURRENT_IMPORTS": " 3 ", "TJ_SESSION_SECRET": "ab" * 32}).max_concurrent_imports
        == 3
    )
    assert ApiConfig.from_env({}).max_concurrent_imports == 2  # an unset or empty value leaves the default
    assert ApiConfig.from_env({"TJ_MAX_CONCURRENT_IMPORTS": ""}).max_concurrent_imports == 2


@pytest.mark.parametrize("text", ["many", "2.5", "-1", "0", str(MAX_CONCURRENT_IMPORTS + 1)])
def test_a_value_that_is_not_a_sensible_number_is_refused_with_its_name(text):
    with pytest.raises(ConfigError, match="TJ_MAX_CONCURRENT_IMPORTS"):
        ApiConfig.from_env({"TJ_MAX_CONCURRENT_IMPORTS": text})


def test_the_ends_of_the_range_are_accepted():
    assert ApiConfig(max_concurrent_imports=1).max_concurrent_imports == 1
    assert ApiConfig(max_concurrent_imports=MAX_CONCURRENT_IMPORTS).max_concurrent_imports == MAX_CONCURRENT_IMPORTS
