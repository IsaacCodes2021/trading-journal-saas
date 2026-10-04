"""Sessions, invites, the audit log and sign-in's user records, against a real Postgres (doc 03 section 4, doc 04)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from tradejournal.api.sessions import CACHE_SECONDS, PgSessionStore
from tradejournal.pg import audit, invites, sessions, users

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def login(system):
    return system.system_session("login")


@pytest.fixture
def token(system, alice):
    with login(system) as conn:
        return sessions.create(conn, alice, now=T0, user_agent="Edge/140", ip_hash="abc")


def live(system, token, when):
    with login(system) as conn:
        return sessions.lookup(conn, token, now=when)


# ---- sessions --------------------------------------------------------------------------------------------------------


def test_a_session_is_a_random_token_and_only_its_hash_is_stored(system, owner, alice, token):
    assert len(token) == 43 and token != sessions.new_token()
    row = owner.execute("SELECT * FROM sessions").fetchone()
    assert bytes(row["token_hash"]) == sessions.hash_token(token)
    assert row["user_id"] == alice and row["user_agent"] == "Edge/140" and row["revoked_at"] is None
    assert row["expires_at"] == T0 + sessions.ABSOLUTE_LIFETIME
    # Nothing in the table, read as text, contains the token: a leaked copy of it cannot be replayed as a cookie.
    dump = str(owner.execute("SELECT row_to_json(sessions) AS j FROM sessions").fetchall())
    assert token not in dump


def test_a_live_session_is_found_and_anything_else_is_not(system, alice, token):
    found = live(system, token, T0 + timedelta(minutes=1))
    assert found is not None and found.user_id == alice and found.mfa_at is None
    assert found.public_id != alice  # what the CSRF token derives from is its own random id
    for bad in (None, "", "nope", token + "x", token[:-1], "x" * 500):
        assert live(system, bad, T0) is None


def test_a_session_ends_after_90_days_even_if_it_is_used_every_day(system, token):
    for day in range(0, 90, 10):  # kept alive by use (the idle timer slides) ...
        assert live(system, token, T0 + day * DAY) is not None, day
    assert live(system, token, T0 + 89 * DAY) is not None
    assert live(system, token, T0 + 90 * DAY + timedelta(seconds=1)) is None  # ... but the absolute limit holds


def test_a_session_ends_after_14_idle_days_but_use_slides_the_timer(system, token):
    assert live(system, token, T0 + 13 * DAY) is not None  # use on day 13 ...
    assert live(system, token, T0 + 26 * DAY) is not None  # ... so day 26 is only 13 idle days later
    assert live(system, token, T0 + 41 * DAY) is None  # 15 idle days: gone
    # Not revived by being asked again: once idle too long it stays ended.
    assert live(system, token, T0 + 26 * DAY + timedelta(minutes=1)) is not None


def test_the_idle_timer_is_not_rewritten_on_every_request(system, owner, token):
    live(system, token, T0 + timedelta(minutes=1))
    assert owner.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"] == T0
    live(system, token, T0 + sessions.TOUCH_EVERY + timedelta(minutes=1))
    assert owner.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"] > T0


def test_revoking_ends_one_session_and_log_out_everywhere_ends_the_rest(system, alice, bob, token):
    with login(system) as conn:
        second = sessions.create(conn, alice, now=T0)
        third = sessions.create(conn, alice, now=T0)
        theirs = sessions.create(conn, bob, now=T0)
        assert sessions.revoke(conn, token, now=T0) is True
        assert sessions.revoke(conn, token, now=T0) is False  # already ended
        assert sessions.revoke_all(conn, alice, except_token=second, now=T0) == 1  # the third
    assert live(system, token, T0) is None and live(system, third, T0) is None
    assert live(system, second, T0) is not None and live(system, theirs, T0) is not None  # the caller's, and bob's


def test_a_suspended_or_deleting_user_loses_every_session_at_once(system, owner, alice, token):
    for status in ("suspended", "pending_deletion"):
        owner.execute(
            "UPDATE users SET status = %s, deletion_scheduled_at = CASE WHEN %s = 'pending_deletion' THEN now() END WHERE id = %s",
            (status, status, alice),
        )
        assert live(system, token, T0) is None, status
    owner.execute("UPDATE users SET status = 'active', deletion_scheduled_at = NULL WHERE id = %s", (alice,))
    assert live(system, token, T0) is not None  # nothing was destroyed, only refused while the status said so


def test_mfa_is_remembered_and_can_be_updated_by_a_step_up(system, alice):
    with login(system) as conn:
        plain = sessions.create(conn, alice, now=T0)
        strong = sessions.create(conn, alice, mfa_at=T0, now=T0)
        sessions.record_mfa(conn, plain, T0 + timedelta(minutes=5))
    assert live(system, strong, T0).mfa_at == T0
    assert live(system, plain, T0).mfa_at == T0 + timedelta(minutes=5)


def test_deleting_a_user_deletes_their_sessions(owner, system, alice, token):
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    assert owner.execute("SELECT count(*) AS n FROM sessions").fetchone()["n"] == 0


# ---- who can touch these tables ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["sessions", "invites", "invite_redemptions", "audit_log"])
def test_the_request_role_has_no_access_to_any_sign_in_table(db, alice, table):
    with db.tenant_session(alice) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"SELECT * FROM {table}")  # noqa: S608 (fixed names)


def test_the_login_role_can_use_sessions_but_not_rewrite_who_they_belong_to_or_delete_them(
    system, owner, alice, bob, token
):
    for statement in (
        "UPDATE sessions SET user_id = %s",
        "UPDATE sessions SET token_hash = decode(repeat('00', 32), 'hex') WHERE %s::text <> ''",
        "DELETE FROM sessions WHERE %s::text <> ''",
    ):
        with login(system) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement, (str(bob),))
    assert owner.execute("SELECT user_id FROM sessions").fetchone()["user_id"] == alice


def test_the_sweep_role_can_delete_expired_sessions_and_see_nothing_else_about_them(system, owner, alice):
    with login(system) as conn:
        sessions.create(conn, alice, now=T0)
    with system.system_session("sweep") as conn:
        assert (
            conn.execute("SELECT id, expires_at FROM sessions").fetchone()["expires_at"]
            == T0 + sessions.ABSOLUTE_LIFETIME
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT token_hash FROM sessions")
    with system.system_session("sweep") as conn:
        assert conn.execute("DELETE FROM sessions WHERE expires_at < %s RETURNING id", (T0 + 100 * DAY,)).fetchone()


# ---- the audit log -----------------------------------------------------------------------------------------------------


def test_the_audit_log_takes_an_event_and_nobody_but_the_owner_can_remove_one(system, owner, alice):
    with login(system) as conn:
        audit.record(conn, "login", user_id=alice, ip_hash="h", meta={"mfa": True})
        audit.record(conn, "login_failed", actor=audit.ACTOR_SYSTEM, meta={"why": "state"})
    rows = owner.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    assert [(r["action"], r["actor"], r["user_id"]) for r in rows] == [
        ("login", "user", alice),
        ("login_failed", "system", None),
    ]
    assert rows[0]["meta"] == {"mfa": True} and rows[0]["actor_user_id"] == alice and rows[1]["actor_user_id"] is None
    for statement in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with login(system) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)  # the login role has INSERT only


def test_even_a_role_with_update_rights_is_stopped_by_the_trigger_and_only_the_owner_may_delete(owner):
    owner.execute("INSERT INTO audit_log (actor, action) VALUES ('system', 'x')")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        owner.execute("UPDATE audit_log SET action = 'y'")  # the owner owns the table and still cannot rewrite history
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        owner.execute("UPDATE audit_log SET meta = '{\"edited\": true}'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        owner.execute(
            "UPDATE audit_log SET user_id = gen_random_uuid()"
        )  # pointing it at someone else is a rewrite too
    assert owner.execute("DELETE FROM audit_log RETURNING id").fetchone()  # retention is the owner's


def test_the_history_outlives_the_account_it_was_about(owner, system, alice):
    with login(system) as conn:
        audit.record(conn, "login", user_id=alice)
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    [row] = owner.execute("SELECT user_id, actor_user_id, action FROM audit_log").fetchall()
    assert (row["user_id"], row["actor_user_id"], row["action"]) == (None, None, "login")


# ---- users at sign-in ---------------------------------------------------------------------------------------------------


def test_a_new_user_is_active_not_an_operator_and_found_by_their_subject(system, owner):
    with login(system) as conn:
        created = users.create(conn, "kp_abc", "new@example.test", now=T0)
        assert users.find_by_subject(conn, "kp_abc") == created and created.status == "active"
        assert users.find_by_subject(conn, "kp_other") is None
    row = owner.execute(
        "SELECT is_admin, email_verified_at, last_login_at FROM users WHERE idp_subject = 'kp_abc'"
    ).fetchone()
    assert row["is_admin"] is False and row["email_verified_at"] == T0 and row["last_login_at"] == T0


def test_a_returning_user_keeps_the_email_the_provider_reports_and_an_email_cannot_be_shared(system, owner):
    with login(system) as conn:
        first = users.create(conn, "kp_one", "one@example.test", now=T0)
        second = users.create(conn, "kp_two", "two@example.test", now=T0)
        users.record_login(conn, first.id, "renamed@example.test", now=T0 + DAY)
        with pytest.raises(users.EmailTaken):
            users.create(conn, "kp_three", "RENAMED@example.test")  # in any capitalization
        with pytest.raises(users.EmailTaken):
            users.record_login(conn, second.id, "renamed@example.test")
        assert users.find_by_subject(conn, "kp_one").email == "renamed@example.test"  # the failed attempts left no mark
    assert (
        owner.execute("SELECT last_login_at FROM users WHERE idp_subject = 'kp_one'").fetchone()["last_login_at"]
        == T0 + DAY
    )


# ---- invites -----------------------------------------------------------------------------------------------------------


def make_invite(owner, **kwargs):
    return invites.create_invite(owner, **kwargs)


def test_a_code_looks_like_the_one_the_page_expects_and_is_stored_hashed(owner):
    code = make_invite(owner, label="Sam")
    assert invites.normalize(code) == code and len(code) == 12 and code.startswith("TJ-")
    assert set(code.replace("-", "")) <= set(invites.ALPHABET) | {"T", "J"}
    row = owner.execute("SELECT * FROM invites").fetchone()
    assert bytes(row["code_hash"]) == invites.hash_code(code) and code not in str(dict(row))
    assert invites.generate_code() != invites.generate_code()


def test_a_valid_code_is_found_however_it_is_typed_and_every_failure_gives_the_same_answer(system, owner):
    code = make_invite(owner)
    typed = code.lower().replace("-", " ")
    with login(system) as conn:
        assert invites.check(conn, code, "a@example.test") is not None
        assert invites.check(conn, typed, "a@example.test") is not None  # "tj 7kq2 m9xa"
        for bad in (None, "", "TJ-ZZZZ-ZZZZ", "nonsense", "x" * 100, code + "-X", "TJ--ABCD"):
            assert invites.check(conn, bad, "a@example.test") is None, bad


def test_revoked_expired_and_wrong_email_invites_are_refused(system, owner):
    revoked = make_invite(owner)
    owner.execute("UPDATE invites SET revoked_at = now() WHERE code_hash = %s", (invites.hash_code(revoked),))
    expired = make_invite(owner, expires_in=timedelta(days=1))
    owner.execute(
        "UPDATE invites SET expires_at = now() - interval '1 second' WHERE code_hash = %s",
        (invites.hash_code(expired),),
    )
    private = make_invite(owner, email="Sam@Example.test")
    with login(system) as conn:
        assert invites.check(conn, revoked, "a@example.test") is None
        assert invites.check(conn, expired, "a@example.test") is None
        assert invites.check(conn, private, "someone@example.test") is None
        assert invites.check(conn, private, "sam@example.test") is not None  # its own address, in any capitalization


def test_an_invite_can_be_used_up_and_two_people_cannot_both_take_the_last_use(system, owner, alice, bob):
    one_use = make_invite(owner, max_uses=1)
    two_uses = make_invite(owner, max_uses=2)
    with login(system) as conn:
        invite = invites.check(conn, one_use, "a@example.test")
        assert invites.redeem(conn, invite, alice) is True
        assert invites.check(conn, one_use, "b@example.test") is None  # used up
        assert invites.redeem(conn, invite, bob) is False  # a second person who checked before the first redeemed
        both = invites.check(conn, two_uses, "a@example.test")
        assert invites.redeem(conn, both, alice) and invites.redeem(conn, both, bob)
        assert invites.check(conn, two_uses, "c@example.test") is None
    count = owner.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"]
    assert count == 3  # alice and bob on the two-use one, alice on the one-use one; the refused attempt left nothing


def test_the_same_person_redeeming_twice_uses_it_once(system, owner, alice):
    code = make_invite(owner, max_uses=3)
    with login(system) as conn:
        invite = invites.check(conn, code, "a@example.test")
        assert invites.redeem(conn, invite, alice) and invites.redeem(conn, invite, alice)
    assert owner.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"] == 1


def test_the_login_role_cannot_create_or_change_an_invite(system, owner):
    code = make_invite(owner)
    for statement in (
        "INSERT INTO invites (code_hash) VALUES (decode(repeat('11', 32), 'hex'))",
        "UPDATE invites SET max_uses = 1000",
        "DELETE FROM invites",
    ):
        with login(system) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
    with login(system) as conn:
        assert invites.check(conn, code, "a@example.test") is not None


def test_the_invite_command_makes_a_code_and_needs_the_owners_connection(owner_url, monkeypatch, capsys):
    monkeypatch.delenv("TJ_OWNER_URL", raising=False)
    assert invites.main(["create"]) == 2
    monkeypatch.setenv("TJ_OWNER_URL", owner_url)
    assert invites.main(["create", "--label", "Sam", "--max-uses", "2", "--days", "30"]) == 0
    code = capsys.readouterr().out.strip()
    assert code.startswith("TJ-") and invites.normalize(code) == code


# ---- the store the API uses ---------------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.wall = T0
        self.mono = 0.0

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def test_the_store_caches_a_live_session_for_a_minute_so_a_request_is_not_a_round_trip(system, alice):
    clock = Clock()
    store = PgSessionStore(system, monotonic=lambda: clock.mono, clock=lambda: clock.wall)
    other = PgSessionStore(system, monotonic=lambda: clock.mono, clock=lambda: clock.wall)
    token = store.create(alice, mfa_at=T0)
    first = store.lookup(token)
    assert first is not None and first.user_id == alice and first.mfa_at == T0 and len(first.id) == 36
    other.revoke(token)  # ended through another instance
    assert store.lookup(token) is not None  # still served from this instance's cache ...
    clock.advance(CACHE_SECONDS + 1)
    assert store.lookup(token) is None  # ... until the minute is up


def test_revoking_through_an_instance_ends_it_there_at_once_and_unknown_tokens_are_never_cached(system, alice):
    clock = Clock()
    store = PgSessionStore(system, monotonic=lambda: clock.mono, clock=lambda: clock.wall)
    token = store.create(alice)
    assert store.lookup(token) is not None
    store.revoke(token)
    assert store.lookup(token) is None
    assert store.lookup("not-a-token") is None and store.lookup(None) is None and store.lookup("") is None
    keep = store.create(alice)
    lose = store.create(alice)
    assert store.lookup(keep) and store.lookup(lose)
    assert store.revoke_all(alice, except_token=keep) == 1
    assert store.lookup(lose) is None and store.lookup(keep) is not None


def test_the_store_honours_the_clock_for_expiry(system, alice):
    clock = Clock()
    store = PgSessionStore(system, monotonic=lambda: clock.mono, clock=lambda: clock.wall)
    token = store.create(alice)
    assert store.lookup(token) is not None
    clock.advance(sessions.IDLE_TIMEOUT.total_seconds() + 10)  # idle too long, and the cache has long expired
    assert store.lookup(token) is None
    assert store.lookup(str(uuid4())) is None


# ---- a person's own sessions: list, end one, end the others -------------------------------------------------------------


def make(system, user, when=T0, **kwargs):
    with login(system) as conn:
        return sessions.create(conn, user, now=when, **kwargs)


def info(system, user, when=T0):
    with login(system) as conn:
        return sessions.list_active(conn, user, now=when)


def test_the_list_holds_a_persons_working_sessions_most_recently_used_first(system, owner, alice, bob):
    old = make(system, alice, T0, user_agent="Chrome/1 Windows")
    new = make(system, alice, T0 + timedelta(hours=1), user_agent="Firefox/1 Linux")
    make(system, bob, T0)
    rows = info(system, alice, T0 + timedelta(hours=2))
    assert [r.user_agent for r in rows] == ["Firefox/1 Linux", "Chrome/1 Windows"]  # not bob's
    assert (
        rows[0].created_at == T0 + timedelta(hours=1)
        and rows[0].expires_at == T0 + timedelta(hours=1) + sessions.ABSOLUTE_LIFETIME
    )
    assert rows[0].public_id != rows[1].public_id
    assert live(system, old, T0).public_id == rows[1].public_id and live(system, new, T0).public_id == rows[0].public_id


def test_the_list_leaves_out_ended_idle_and_past_the_90_day_limit(system, alice):
    revoked, idle, kept_alive = (make(system, alice, T0) for _ in range(3))
    with login(system) as conn:
        sessions.revoke(conn, revoked, now=T0)
    assert len(info(system, alice, T0 + 13 * DAY)) == 2  # the revoked one is gone; 13 idle days is still fine
    assert live(system, kept_alive, T0 + 13 * DAY) is not None  # used, so its idle timer starts again
    assert len(info(system, alice, T0 + 15 * DAY)) == 1  # `idle` has now been unused for 15 days: gone
    for day in range(26, 79, 13):
        assert live(system, kept_alive, T0 + day * DAY) is not None  # used every 13 days, so never idle
    assert len(info(system, alice, T0 + 89 * DAY)) == 1
    assert info(system, alice, T0 + 91 * DAY) == []  # whatever the use, 90 days is the end
    assert live(system, idle, T0 + 15 * DAY) is None


def test_a_listed_session_is_exactly_one_that_sign_in_would_accept(system, alice):
    tokens = [make(system, alice, T0 + d * DAY) for d in (0, 5, 10, 20)]
    for when in (T0 + 3 * DAY, T0 + 16 * DAY, T0 + 30 * DAY, T0 + 95 * DAY):
        accepted = sum(1 for t in tokens if live(system, t, when) is not None)
        assert len(info(system, alice, when)) == accepted, when


def test_ending_one_session_by_its_public_id_ends_only_that_one(system, alice):
    keep, gone = make(system, alice), make(system, alice)
    target = next(r.public_id for r in info(system, alice) if live(system, gone, T0).public_id == r.public_id)
    with login(system) as conn:
        assert sessions.revoke_one(conn, alice, target, now=T0) is True
        assert sessions.revoke_one(conn, alice, target, now=T0) is False  # already ended
    assert live(system, gone, T0) is None and live(system, keep, T0) is not None


def test_a_session_id_belonging_to_someone_else_ends_nothing_and_looks_like_an_unknown_one(system, alice, bob):
    bobs = make(system, bob)
    bobs_id = live(system, bobs, T0).public_id
    with login(system) as conn:
        assert sessions.revoke_one(conn, alice, bobs_id, now=T0) is False
        assert sessions.revoke_one(conn, alice, uuid4(), now=T0) is False
        assert sessions.revoke_others(conn, alice, bobs_id, now=T0) == 0  # alice has none; bob's is not touched
    assert live(system, bobs, T0) is not None


def test_ending_the_others_keeps_the_named_one_and_counts_only_working_ones(system, alice, bob):
    here = make(system, alice, T0 + 50 * DAY)
    other = [make(system, alice, T0 + 50 * DAY), make(system, alice, T0 + 50 * DAY)]
    dead = make(system, alice, T0)  # idle for 50 days: already not working, so not counted
    bobs = make(system, bob, T0 + 50 * DAY)
    when = T0 + 51 * DAY
    keep = live(system, here, when).public_id
    with login(system) as conn:
        assert sessions.revoke_others(conn, alice, keep, now=when) == 2
        assert sessions.revoke_others(conn, alice, keep, now=when) == 0  # nothing left to end
    assert live(system, here, when) is not None and all(live(system, t, when) is None for t in other)
    assert live(system, bobs, when) is not None and live(system, dead, when) is None


def test_the_store_lists_ends_one_and_ends_the_others_and_each_is_audited_in_the_same_transaction(system, owner, alice):
    clock = Clock()
    store = PgSessionStore(system, monotonic=lambda: clock.mono, clock=lambda: clock.wall)
    here = store.create(alice, user_agent="Chrome/1 Windows")
    phone = store.create(alice, user_agent="Safari/1 iPhone")
    tablet = store.create(alice)
    mine = store.lookup(here)
    listed = store.list_for(alice)
    assert {s.id for s in listed} == {mine.id, store.lookup(phone).id, store.lookup(tablet).id}
    phone_id = store.lookup(phone).id

    assert store.revoke_one(alice, phone_id, ip_hash="h1") is True
    assert store.revoke_one(alice, phone_id, ip_hash="h1") is False  # nothing left to end: no second audit row
    assert store.revoke_one(alice, "not-a-uuid") is False
    assert store.lookup(phone) is None  # at once, not after the cache's minute
    assert store.revoke_others(alice, mine.id, ip_hash="h2") == 1
    assert store.lookup(tablet) is None and store.lookup(here) is not None
    rows = owner.execute("SELECT action, user_id, actor, ip_hash, meta FROM audit_log ORDER BY id").fetchall()
    assert [(r["action"], r["ip_hash"], r["meta"]) for r in rows] == [
        ("session_revoked", "h1", {}),
        ("logout_everywhere", "h2", {"ended": 1}),
    ]
    assert all(r["user_id"] == alice and r["actor"] == "user" for r in rows)


def test_the_store_drops_exactly_the_cached_sessions_that_were_ended(system, alice, bob):
    clock = Clock()
    store = PgSessionStore(system, monotonic=lambda: clock.mono, clock=lambda: clock.wall)
    a1, a2, b1 = store.create(alice), store.create(alice), store.create(bob)
    s1, s2, sb = store.lookup(a1), store.lookup(a2), store.lookup(b1)  # all three are now cached
    store.revoke_others(alice, s1.id)
    assert store.lookup(a1) is not None and store.lookup(b1) is not None  # kept: the named one, and another person's
    assert store.lookup(a2) is None  # not served from the cache, though the minute has not passed
    assert len({s1.id, s2.id, sb.id}) == 3


def test_a_development_style_id_that_is_not_a_uuid_ends_nothing_and_is_not_an_error(system, alice):
    store = PgSessionStore(system)
    token = store.create(alice)
    assert store.revoke_others(alice, "dev") == 0 and store.revoke_one(alice, "dev") is False
    assert store.lookup(token) is not None
