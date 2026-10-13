"""Prop-firm rules on Postgres (migration 0019): the firms' templates and their import, the picker, a person's verified
rule sets, payouts with gross, split and fee, an account's purchase date and phase, and who may touch the templates."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from pg_helpers import make_account
from test_pg_system_roles import COLUMN_PRIVILEGES, PRIVILEGES
from test_prop_rules import ROW, SEED, csv_text

from tradejournal import finance
from tradejournal import prop_rules as pr
from tradejournal.pg import accounts, ledger, rule_templates
from tradejournal.pg import rules as pg_rules
from tradejournal.prop_rules import RuleValues

D = Decimal
LUCID_PRO_FUNDED = ("Lucid Trading", "LucidPro", "50K", "funded")


def rule_set(**values) -> RuleValues:
    """A rule set with every field empty but the five that name it."""
    filled = dict.fromkeys(pr.FIELD_NAMES)
    filled.update(firm="Lucid Trading", program="LucidPro", account_size="50K", phase="funded", rule_version="current")
    filled.update(values)
    return RuleValues(values=filled)


def add_template(conn, **values) -> rule_templates.Template:
    rules = rule_set(**values)
    rule_templates.upsert(conn, [rules], by="test")
    return next(t for t in rule_templates.list_all(conn) if t.rules.key() == rules.key())


def offered(conn, purchased_on: date | None) -> dict[int, bool]:
    """The picker's offer for one purchase date: template id -> preselected."""
    return {o.template.id: o.preselect for o in pg_rules.offered_templates(conn, purchased_on)}


# ---- the tables match the module ---------------------------------------------------------------------------------

SQL_TYPES = {
    "text": ("text", None, None),
    "choice": ("text", None, None),
    "money": ("numeric", 20, 2),
    "pct": ("numeric", 6, 2),
    "int": ("integer", 32, 0),
    "date": ("date", None, None),
}


@pytest.mark.parametrize("table", ["rule_templates", "account_rules"])
def test_the_table_has_the_rule_fields_in_the_modules_order_and_types(owner, table):
    rows = owner.execute(
        """SELECT column_name, data_type, numeric_precision, numeric_scale FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""",
        (table,),
    ).fetchall()
    fields = [r for r in rows if r["column_name"] in pr.BY_NAME]
    assert [r["column_name"] for r in fields] == list(pr.FIELD_NAMES)
    for r in fields:
        kind = pr.BY_NAME[r["column_name"]].kind
        assert (r["data_type"], r["numeric_precision"], r["numeric_scale"]) == SQL_TYPES[kind], r["column_name"]


def _insert_rule_row(conn, table: str, account_id: int | None, n: int, name: str, value: str) -> None:
    columns = ["firm", "program", "account_size", "phase", "rule_version"]
    values: list = ["A", "B", "50K", "funded", f"v{n}"]
    if name in columns:
        values[columns.index(name)] = value
    else:
        columns.append(name)
        values.append(value)
    if account_id is not None:  # a history row, so the one-active-rule-set index does not get in the way
        columns += ["account_id", "superseded_at"]
        values += [account_id, "2026-01-01"]
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(values))})",  # noqa: S608 (fixed names)
        values,
    )


@pytest.mark.parametrize("table", ["rule_templates", "account_rules"])
def test_the_database_accepts_exactly_the_choices_the_module_lists(owner, db, alice, table):
    """Migration 0019 repeats each choice list twice in CHECKs; this fails if either drifts from `prop_rules`."""
    with db.tenant_session(alice) as conn:
        account_id = make_account(conn).id if table == "account_rules" else None
        target = conn if table == "account_rules" else owner
        n = 0
        for rule in pr.FIELDS:
            if rule.kind != "choice":
                continue
            for choice in rule.choices:
                n += 1
                _insert_rule_row(target, table, account_id, n, rule.name, choice)
            n += 1
            with pytest.raises(psycopg.errors.CheckViolation), target.transaction():
                _insert_rule_row(target, table, account_id, n, rule.name, "something_else")


# ---- importing the templates ---------------------------------------------------------------------------------------


def test_an_import_inserts_new_rule_sets_updates_changed_ones_and_leaves_the_rest(owner):
    first = csv_text(ROW, {**ROW, "account_size": "100K"})
    counts, problems = rule_templates.import_csv(owner, first, by="cli:owner")
    assert problems == [] and (counts.inserted, counts.updated, counts.unchanged) == (2, 0, 0)
    before = {t.id: t.updated_at for t in rule_templates.list_all(owner)}

    again, _ = rule_templates.import_csv(owner, first, by="admin:op")
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 2)
    assert {t.id: t.updated_at for t in rule_templates.list_all(owner)} == before  # not even touched

    edited = csv_text(
        {**ROW, "max_loss_usd": "2500"},
        {**ROW, "account_size": "100K"},
        {**ROW, "account_size": "150K"},
    )
    counts, _ = rule_templates.import_csv(owner, edited, by="admin:op")
    assert (counts.inserted, counts.updated, counts.unchanged) == (1, 1, 1)
    by_size = {t.rules.values["account_size"]: t for t in rule_templates.list_all(owner)}
    assert by_size["50K"].rules.values["max_loss_usd"] == D("2500.00") and by_size["50K"].updated_by == "admin:op"
    assert by_size["100K"].updated_by == "cli:owner" and by_size["100K"].updated_at == before[by_size["100K"].id]


@pytest.mark.parametrize(
    "change",
    [
        {"flag_reasons": "conflict: two sources|unknown: not stated|third-party only (Tanto)"},
        {"flagged_fields": "", "flag_reasons": ""},
        {"price_usd": "79.99 (69.99 with DLL add-on)"},  # only the source note differs
        {"price_usd": "79.99"},  # the note becomes a value
    ],
)
def test_a_change_to_flags_or_notes_alone_is_an_update(owner, change):
    rule_templates.import_csv(owner, csv_text(ROW), by="test")
    counts, _ = rule_templates.import_csv(owner, csv_text({**ROW, **change}), by="test")
    assert (counts.inserted, counts.updated, counts.unchanged) == (0, 1, 0)


def test_flags_notes_and_typed_values_come_back_as_they_went_in(owner):
    rule_templates.import_csv(owner, csv_text(ROW), by="test")
    [template] = rule_templates.list_all(owner)
    [parsed] = pr.parse_csv(csv_text(ROW)).rows
    assert template.rules == parsed
    assert template.rules.source_notes == {"price_usd": "79.99 (59.99 with DLL add-on)"}


def test_an_import_keeps_the_rows_it_can_read_and_lists_the_others(owner):
    counts, problems = rule_templates.import_csv(owner, csv_text(ROW, {**ROW, "phase": "sim"}), by="test")
    assert counts.inserted == 1 and problems == ["Line 3: has no phase it can use ('sim')."]
    counts, problems = rule_templates.import_csv(owner, "", by="test")
    assert counts.total == 0 and problems == ["Line 1: The file is empty."]
    counts, problems = rule_templates.import_csv(owner, csv_text(), by="test")  # the header alone
    assert counts.total == 0 and problems == ["The file has no rule sets."]


@pytest.mark.skipif(not SEED.exists(), reason="the seed CSV is not in this checkout")
def test_the_seed_imports_whole_and_a_second_import_changes_nothing(owner):
    text = SEED.read_text(encoding="utf-8")
    with owner.transaction():
        counts, problems = rule_templates.import_csv(owner, text, by="cli:owner")
    assert problems == [] and (counts.inserted, counts.updated, counts.unchanged) == (371, 0, 0)
    stored = {t.rules.key(): t for t in rule_templates.list_all(owner)}
    for row in pr.parse_csv(text).rows:
        assert stored[row.key()].rules == row, row.key()  # every value, flag and note survives the database

    with owner.transaction():
        again, problems = rule_templates.import_csv(owner, text, by="cli:owner")
    assert problems == [] and (again.inserted, again.updated, again.unchanged) == (0, 0, 371)
    assert {t.rules.key(): t.updated_at for t in rule_templates.list_all(owner)} == {
        k: t.updated_at for k, t in stored.items()
    }


def test_the_command_is_a_dry_run_unless_told_to_apply(owner, owner_url, tmp_path, monkeypatch, capsys):
    path = tmp_path / "rules.csv"
    path.write_text(csv_text(ROW, {**ROW, "account_size": "100K"}), encoding="utf-8")
    monkeypatch.setenv("TJ_OWNER_URL", owner_url)

    assert rule_templates.main(["import", "--csv", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Dry run (nothing kept): would import 2 rule sets: 2 new, 0 changed, 0 unchanged." in out
    assert "--apply" in out
    assert rule_templates.list_all(owner) == []

    assert rule_templates.main(["import", "--csv", str(path), "--apply"]) == 0
    assert "Imported 2 rule sets: 2 new, 0 changed, 0 unchanged." in capsys.readouterr().out
    assert {t.updated_by for t in rule_templates.list_all(owner)} == {"cli:owner"}


def test_the_command_needs_the_owner_address_and_a_readable_file(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TJ_OWNER_URL", raising=False)
    assert rule_templates.main(["import", "--csv", str(tmp_path / "rules.csv")]) == 2
    assert "TJ_OWNER_URL is not set" in capsys.readouterr().err
    monkeypatch.setenv("TJ_OWNER_URL", "postgresql://nobody@127.0.0.1:1/none")  # never reached
    assert rule_templates.main(["import", "--csv", str(tmp_path / "missing.csv")]) == 2
    assert "Cannot read" in capsys.readouterr().err


# ---- the operator's list and edits ------------------------------------------------------------------------------


def test_search_needs_every_word_in_any_case_and_pages(owner):
    pro50 = add_template(owner)
    pro100 = add_template(owner, account_size="100K")
    flex = add_template(owner, firm="Tradeify", program="Select Flex 100%", phase="evaluation")

    def ids(q, limit=10, offset=0):
        found, total = rule_templates.search(owner, q, limit=limit, offset=offset)
        return {t.id for t in found}, total

    assert ids(None) == ({pro50.id, pro100.id, flex.id}, 3)
    assert ids("LUCID 50k") == ({pro50.id}, 1)
    assert ids("100%") == ({flex.id}, 1)  # a % in the query is a character, not a wildcard
    assert ids("lucid evaluation") == (set(), 0)
    first, total = ids("lucid", limit=1)
    second, _ = ids("lucid", limit=1, offset=1)
    assert total == 2 and first | second == {pro50.id, pro100.id} and first != second


def test_an_edit_replaces_values_and_flags_and_drops_the_note_of_a_cell_it_fills(owner):
    rule_templates.import_csv(owner, csv_text({**ROW, "max_loss_usd": "up to 2000"}), by="seed")
    [template] = rule_templates.list_all(owner)
    assert set(template.rules.source_notes) == {"price_usd", "max_loss_usd"}

    edited = RuleValues(
        {**template.rules.values, "price_usd": D("79.99")},
        flagged_fields=["flat_by_time"],
        flag_reasons=["third-party only (Tanto)"],
        source_notes={},  # ignored: the notes are the source's, not the editor's
    )
    after = rule_templates.update(owner, template.id, edited, by="admin:op")
    assert after.rules.values["price_usd"] == D("79.99")
    assert after.rules.source_notes == {"max_loss_usd": "up to 2000"}  # still empty, so its note stays
    assert (after.rules.flagged_fields, after.rules.flag_reasons) == (["flat_by_time"], ["third-party only (Tanto)"])
    assert after.updated_by == "admin:op" and after.updated_at >= template.updated_at


def test_an_edit_of_a_missing_template_or_onto_anothers_name_is_refused(owner):
    one = add_template(owner)
    other = add_template(owner, rule_version="legacy")
    assert rule_templates.update(owner, 999, one.rules, by="admin:op") is None
    with pytest.raises(psycopg.errors.UniqueViolation):
        rule_templates.update(owner, other.id, one.rules, by="admin:op")


# ---- the picker -----------------------------------------------------------------------------------------------------


def test_the_picker_offers_the_versions_in_force_on_the_purchase_date_and_preselects_the_latest(owner, conn):
    current = add_template(owner, rule_version="current", effective_from=date(2025, 11, 28))
    legacy = add_template(owner, rule_version="legacy")  # the research could not date it
    assert offered(conn, date(2025, 11, 1)) == {legacy.id: True}  # the current rules did not exist yet
    assert offered(conn, date(2025, 11, 28)) == {current.id: True, legacy.id: False}  # in force on its first day
    assert offered(conn, date(2026, 1, 1)) == {current.id: True, legacy.id: False}
    assert offered(conn, None) == {current.id: False, legacy.id: False}  # no date: everything, nothing chosen


def test_the_later_dated_version_wins_and_between_undated_ones_the_last_added(owner, conn):
    older = add_template(owner, program="A", rule_version="2025", effective_from=date(2025, 1, 1))
    newer = add_template(owner, program="A", rule_version="2026", effective_from=date(2026, 1, 1))
    first = add_template(owner, program="B", rule_version="old")
    last = add_template(owner, program="B", rule_version="new")
    evaluation = add_template(owner, program="A", phase="evaluation", rule_version="only")  # a choice of its own
    assert offered(conn, date(2026, 6, 1)) == {
        older.id: False,
        newer.id: True,
        first.id: False,
        last.id: True,
        evaluation.id: True,
    }


@pytest.mark.skipif(not SEED.exists(), reason="the seed CSV is not in this checkout")
def test_the_picker_on_the_seed_lucidpro_50k_funded(owner, conn):
    with owner.transaction():
        rule_templates.import_csv(owner, SEED.read_text(encoding="utf-8"), by="cli:owner")

    def lucid(day):
        return {
            o.template.rules.values["rule_version"].split()[0]: o.preselect
            for o in pg_rules.offered_templates(conn, day)
            if tuple(o.template.rules.values[k] for k in ("firm", "program", "account_size", "phase"))
            == LUCID_PRO_FUNDED
        }

    assert lucid(date(2025, 11, 1)) == {"legacy": True}
    assert lucid(date(2026, 1, 1)) == {"current": True, "legacy": False}


# ---- a person's rule sets ------------------------------------------------------------------------------------------


def test_saving_a_rule_set_supersedes_the_previous_one_and_keeps_it_as_history(conn):
    account = make_account(conn, account_type="funded")
    assert pg_rules.active(conn, account.id) is None
    first = pg_rules.save(conn, account.id, rule_set(max_loss_usd=D("2000")), source_template_id=None)
    second = pg_rules.save(conn, account.id, rule_set(max_loss_usd=D("2500")), source_template_id=None)
    assert pg_rules.active(conn, account.id) == second
    assert second.rules.values["max_loss_usd"] == D("2500.00")
    assert [r.id for r in pg_rules.history(conn, account.id)] == [second.id, first.id]
    assert pg_rules.all_active(conn) == {account.id: second}
    rows = conn.execute("SELECT id, superseded_at FROM account_rules ORDER BY id").fetchall()
    assert [(r["id"], r["superseded_at"] is None) for r in rows] == [(first.id, False), (second.id, True)]


def test_editing_a_template_never_changes_a_saved_rule_set(owner, conn):
    template = add_template(owner, max_loss_usd=D("2000"), profit_split="90/10")
    account = make_account(conn, account_type="funded")
    pg_rules.save(conn, account.id, template.rules, source_template_id=template.id)

    changed = {**template.rules.values, "max_loss_usd": D("3000"), "profit_split": "80/20"}
    rule_templates.update(owner, template.id, RuleValues(changed), by="admin:op")
    assert rule_templates.get(owner, template.id).rules.values["max_loss_usd"] == D("3000.00")

    kept = pg_rules.active(conn, account.id)
    assert (kept.rules.values["max_loss_usd"], kept.rules.values["profit_split"]) == (D("2000.00"), "90/10")
    assert kept.source_template_id == template.id  # remembered, never followed


def test_removing_a_template_keeps_the_rule_sets_copied_from_it(owner, db, alice):
    template = add_template(owner, max_loss_usd=D("2000"))
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
        pg_rules.save(conn, account.id, template.rules, source_template_id=template.id)
    owner.execute("DELETE FROM rule_templates WHERE id = %s", (template.id,))
    with db.tenant_session(alice) as conn:
        kept = pg_rules.active(conn, account.id)
    assert kept.source_template_id is None and kept.rules.values["max_loss_usd"] == D("2000.00")


def test_flags_and_notes_are_kept_only_for_fields_that_exist(conn):
    account = make_account(conn)
    rules = RuleValues(
        rule_set().values,
        flagged_fields=["max_loss_usd", "lunch_budget"],
        flag_reasons=["unknown: not stated", "unknown"],
        source_notes={"price_usd": "79.99 (59.99 with DLL add-on)", "lunch_budget": "20"},
    )
    saved = pg_rules.save(conn, account.id, rules, source_template_id=None).rules
    assert (saved.flagged_fields, saved.flag_reasons) == (["max_loss_usd"], ["unknown: not stated"])
    assert saved.source_notes == {"price_usd": "79.99 (59.99 with DLL add-on)"}


def test_saving_needs_a_real_account_and_a_real_template(conn):
    account = make_account(conn)
    with pytest.raises(LookupError, match="no account"):
        pg_rules.save(conn, 999, rule_set(), source_template_id=None)
    with pytest.raises(LookupError, match="no rule template"):
        pg_rules.save(conn, account.id, rule_set(), source_template_id=999)
    assert pg_rules.history(conn, account.id) == []


def test_the_database_allows_one_active_rule_set_per_account(conn):
    account = make_account(conn)
    pg_rules.save(conn, account.id, rule_set(), source_template_id=None)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute(
            "INSERT INTO account_rules (account_id, firm, program, account_size, phase, rule_version) "
            "VALUES (%s, 'A', 'B', '50K', 'funded', 'v1')",
            (account.id,),
        )


def test_deleting_an_account_deletes_its_rule_sets(conn):
    account = make_account(conn)
    pg_rules.save(conn, account.id, rule_set(), source_template_id=None)
    pg_rules.save(conn, account.id, rule_set(), source_template_id=None)
    conn.execute("DELETE FROM accounts WHERE id = %s", (account.id,))
    assert conn.execute("SELECT count(*) AS n FROM account_rules").fetchone()["n"] == 0


def test_a_person_cannot_see_or_change_anothers_rule_sets(owner, db, alice, bob):
    template = add_template(owner)
    with db.tenant_session(alice) as a:
        account = make_account(a)
        pg_rules.save(a, account.id, rule_set(max_loss_usd=D("2000")), source_template_id=template.id)
    with db.tenant_session(bob) as b:
        assert pg_rules.active(b, account.id) is None
        assert pg_rules.all_active(b) == {} and pg_rules.history(b, account.id) == []
        with pytest.raises(LookupError):  # Alice's account looks like a missing one
            pg_rules.save(b, account.id, rule_set(), source_template_id=None)
        assert b.execute("UPDATE account_rules SET max_loss_usd = 1").rowcount == 0
        with pytest.raises(psycopg.errors.ForeignKeyViolation), b.transaction():
            b.execute(
                "INSERT INTO account_rules (account_id, firm, program, account_size, phase, rule_version) "
                "VALUES (%s, 'A', 'B', '50K', 'funded', 'v1')",
                (account.id,),
            )
        assert offered(b, None) == {template.id: False}  # the templates are everyone's
    with db.tenant_session(alice) as a:
        assert pg_rules.active(a, account.id).rules.values["max_loss_usd"] == D("2000.00")


# ---- payouts -----------------------------------------------------------------------------------------------------


def test_a_payout_keeps_its_gross_split_and_fee_and_its_amount_is_the_net(conn):
    account = make_account(conn, account_type="funded")
    payout = ledger.add_payout(
        conn, account.id, entry_date=date(2026, 3, 2), gross=D("2000"), split_pct=D("90"), fee=D("25"), note="  first "
    )
    assert (payout.kind, payout.amount, payout.note) == ("payout", D("1800.00"), "first")
    assert (payout.gross_amount, payout.profit_split_pct, payout.payout_fee) == (D("2000.00"), D("90.00"), D("25.00"))
    assert payout.payout_number == 1
    assert ledger.payouts(conn, account.id) == [payout]


@pytest.mark.parametrize(
    ("gross", "split", "net"),
    [("333.33", "90", "300.00"), ("0.05", "10", "0.01"), ("2.25", "50", "1.13"), ("1000", "33.33", "333.30")],
)
def test_the_net_is_rounded_half_up_to_the_cent_as_the_database_checks_it(conn, gross, split, net):
    account = make_account(conn, account_type="funded")
    payout = ledger.add_payout(conn, account.id, entry_date=date(2026, 3, 2), gross=D(gross), split_pct=D(split))
    assert payout.amount == D(net) == ledger.net_of(D(gross), D(split))


def test_a_split_with_more_than_two_decimals_is_kept_to_two_and_the_net_follows_it(conn):
    account = make_account(conn, account_type="funded")
    payout = ledger.add_payout(conn, account.id, entry_date=date(2026, 3, 2), gross=D("1000"), split_pct=D("33.333"))
    assert (payout.profit_split_pct, payout.amount) == (D("33.33"), D("333.30"))  # the database's check agrees
    with pytest.raises(ValueError, match="more than 0%"):
        ledger.add_payout(conn, account.id, entry_date=date(2026, 3, 2), gross=D("1000"), split_pct=D("0.004"))


@pytest.mark.parametrize(
    ("gross", "split", "fee", "message"),
    [
        ("0", "90", "0", "gross amount must be more than zero"),
        ("-5", "90", "0", "gross amount must be more than zero"),
        ("100", "0", "0", "split must be more than 0%"),
        ("100", "100.01", "0", "at most 100%"),
        ("100", "90", "-1", "fee cannot be negative"),
        ("0.01", "10", "0", "at least one cent"),
    ],
)
def test_a_payout_that_does_not_add_up_is_refused(conn, gross, split, fee, message):
    account = make_account(conn, account_type="funded")
    with pytest.raises(ValueError, match=message):
        ledger.add_payout(conn, account.id, entry_date=date(2026, 3, 2), gross=D(gross), split_pct=D(split), fee=D(fee))
    with pytest.raises(LookupError):
        ledger.add_payout(conn, 999, entry_date=date(2026, 3, 2), gross=D("100"), split_pct=D("90"))
    assert ledger.payouts(conn, account.id) == []


def test_payouts_are_numbered_by_date_after_a_late_one_or_a_removed_one(conn):
    account = make_account(conn, account_type="funded")
    other = make_account(conn, "Apex PA 02", account_type="funded")

    def pay(account_id, day):
        return ledger.add_payout(conn, account_id, entry_date=day, gross=D("1000"), split_pct=D("90"))

    def numbers(account_id=account.id):
        return {e.id: e.payout_number for e in ledger.entries(conn, account_id)}

    march, april = pay(account.id, date(2026, 3, 2)), pay(account.id, date(2026, 4, 1))
    fee = ledger.add_entry(conn, account.id, "activation_fee", D("85"), date(2026, 1, 5))
    late = pay(account.id, date(2026, 2, 2))  # logged late: it was the first
    same_day = pay(account.id, date(2026, 4, 1))  # two on one day go in the order they were logged
    assert numbers() == {fee.id: None, late.id: 1, march.id: 2, april.id: 3, same_day.id: 4}

    ledger.delete_entry(conn, march.id)
    assert numbers() == {fee.id: None, late.id: 1, april.id: 2, same_day.id: 3}
    old_way = ledger.add_entry(conn, account.id, "payout", D("500"), date(2026, 5, 1))  # no gross or split
    assert numbers()[old_way.id] == 4 and old_way.gross_amount is None

    first_elsewhere = pay(other.id, date(2026, 6, 1))
    assert numbers(other.id) == {first_elsewhere.id: 1}  # each account counts its own


def test_payouts_recorded_before_the_numbers_were_kept_get_theirs_on_the_next_change(conn):
    account = make_account(conn, account_type="funded")
    conn.execute(
        "INSERT INTO account_ledger (account_id, kind, amount, entry_date) VALUES (%s, 'payout', 700, '2026-01-10')",
        (account.id,),
    )
    pay = ledger.add_payout(conn, account.id, entry_date=date(2026, 2, 1), gross=D("1000"), split_pct=D("90"))
    assert [(e.amount, e.payout_number) for e in ledger.payouts(conn, account.id)] == [
        (D("700.00"), 1),
        (D("900.00"), 2),
    ]
    assert pay.payout_number == 2


def test_business_totals_count_what_was_received_and_a_payout_fee_as_a_cost(conn):
    account = make_account(conn, account_type="funded")
    ledger.add_entry(conn, account.id, "activation_fee", D("85"), date(2026, 1, 5))
    ledger.add_payout(conn, account.id, entry_date=date(2026, 3, 2), gross=D("2000"), split_pct=D("90"), fee=D("25"))
    ledger.add_entry(conn, account.id, "payout", D("500"), date(2026, 4, 1))  # logged the old way: no fee
    totals = finance.business_totals(ledger.entries(conn))
    assert (totals.costs, totals.payouts, totals.net) == (D("110.00"), D("2300.00"), D("2190.00"))


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        ("kind, amount, payout_fee", "'reset_fee', 80, 5"),  # a cost carries no payout fields
        ("kind, amount, payout_number", "'other_cost', 80, 1"),
        ("kind, amount, gross_amount, profit_split_pct", "'payout', 1800.01, 2000, 90"),  # net is not gross x split
        ("kind, amount, gross_amount", "'payout', 1800, 2000"),  # a gross without its split
        ("kind, amount, profit_split_pct", "'payout', 1800, 90"),  # a split without its gross
        ("kind, amount, gross_amount, profit_split_pct", "'payout', 0.01, 1, 0"),
        ("kind, amount, gross_amount, profit_split_pct", "'payout', 1010, 1000, 101"),
        ("kind, amount, payout_number", "'payout', 100, 0"),
        ("kind, amount, payout_fee", "'payout', 100, -1"),
    ],
)
def test_the_database_refuses_payout_fields_that_do_not_add_up(conn, columns, values):
    account = make_account(conn)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            f"INSERT INTO account_ledger (account_id, entry_date, {columns}) VALUES (%s, '2026-01-02', {values})",  # noqa: S608
            (account.id,),
        )


def test_a_person_cannot_log_or_see_a_payout_on_anothers_account(db, alice, bob):
    with db.tenant_session(alice) as a:
        account = make_account(a, account_type="funded")
        payout = ledger.add_payout(a, account.id, entry_date=date(2026, 3, 2), gross=D("1000"), split_pct=D("90"))
    with db.tenant_session(bob) as b:
        with pytest.raises(LookupError):
            ledger.add_payout(b, account.id, entry_date=date(2026, 3, 1), gross=D("1000"), split_pct=D("90"))
        assert ledger.payouts(b, account.id) == []
        ledger.delete_entry(b, payout.id)  # deletes nothing: row-level security hides the row
        ledger.renumber_payouts(b, account.id)  # numbers nothing
    with db.tenant_session(alice) as a:
        assert ledger.payouts(a, account.id) == [payout]


# ---- an account's purchase date, phase and the evaluation-passed prompt ------------------------------------------------


def test_purchase_date_and_phase_are_kept_and_a_phase_that_matches_the_type_is_stored_empty(conn):
    evaluation = make_account(conn, purchase_date=date(2026, 1, 5), phase="evaluation")
    assert (evaluation.purchase_date, evaluation.phase, evaluation.phase_stored) == (
        date(2026, 1, 5),
        "evaluation",
        None,
    )
    funded = make_account(conn, "Lucid PA 01", account_type="funded", phase="funded")
    assert (funded.phase, funded.phase_stored, funded.purchase_date) == ("funded", None, None)
    live = make_account(conn, "Lucid Live", account_type="funded", phase="live")
    assert (live.phase, live.phase_stored) == ("live", "live")

    details = {"name": "Lucid Live", "firm": "Apex", "account_size": D("50000"), "starting_balance": D("50000")}
    updated = accounts.update_account(
        conn, live.id, **details, account_type="funded", purchase_date=date(2026, 2, 1), phase=None
    )
    assert (updated.purchase_date, updated.phase, updated.phase_stored) == (date(2026, 2, 1), "funded", None)
    cleared = accounts.update_account(conn, live.id, **details, account_type="funded", purchase_date=None, phase="live")
    assert (cleared.purchase_date, cleared.phase) == (None, "live")


@pytest.mark.parametrize(
    ("account_type", "phase", "message"),
    [
        ("evaluation", "live", "the phase must be evaluation for evaluation accounts"),
        ("evaluation", "funded", "the phase must be evaluation for evaluation accounts"),
        ("funded", "evaluation", "the phase must be funded or live for funded accounts"),
        ("funded", "sim", "the phase must be funded or live for funded accounts"),
    ],
)
def test_a_phase_that_does_not_fit_the_type_is_refused(conn, account_type, phase, message):
    with pytest.raises(ValueError, match=message):
        make_account(conn, account_type=account_type, phase=phase)
    account = make_account(conn, account_type=account_type)
    with pytest.raises(ValueError, match=message):
        accounts.update_account(
            conn,
            account.id,
            name="Apex 07",
            firm="Apex",
            account_type=account_type,
            account_size=D("50000"),
            starting_balance=D("50000"),
            phase=phase,
        )


@pytest.mark.parametrize(
    ("purchased", "message"),
    [(date.today() + timedelta(days=5), "cannot be in the future"), (date(1999, 12, 31), "too long ago")],
)
def test_a_purchase_date_must_be_a_real_past_day(conn, purchased, message):
    with pytest.raises(ValueError, match=message):
        make_account(conn, purchase_date=purchased)
    assert make_account(conn, purchase_date=date.today() + timedelta(days=1)).purchase_date is not None  # time zones


@pytest.mark.parametrize(
    ("account_type", "phase"),
    [("evaluation", "live"), ("evaluation", "funded"), ("funded", "evaluation"), ("funded", "sim")],
)
def test_the_database_refuses_a_phase_that_does_not_fit_the_type(conn, account_type, phase):
    account = make_account(conn, account_type=account_type)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE accounts SET phase = %s WHERE id = %s", (phase, account.id))


def test_dismissing_the_pass_prompt_keeps_the_first_time(db, alice, bob):
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
        assert account.pass_prompt_dismissed_at is None
        first = accounts.dismiss_pass_prompt(conn, account.id).pass_prompt_dismissed_at
    assert first is not None
    with db.tenant_session(alice) as conn:  # a later transaction, so now() has moved on
        assert accounts.dismiss_pass_prompt(conn, account.id).pass_prompt_dismissed_at == first
        with pytest.raises(LookupError):
            accounts.dismiss_pass_prompt(conn, 999)
    with db.tenant_session(bob) as conn, pytest.raises(LookupError):
        accounts.dismiss_pass_prompt(conn, account.id)


# ---- who may touch the templates ------------------------------------------------------------------------------------

TEMPLATE_GRANTS = {
    "app_rw": {"SELECT"},
    "app_admin": {"SELECT", "INSERT", "UPDATE"},
    "app_login": set(),
    "app_webhook": set(),
    "app_sweep": set(),
}


@pytest.mark.parametrize(("role", "granted"), TEMPLATE_GRANTS.items())
def test_the_catalog_grants_on_the_templates_are_exactly_these(owner, role, granted):
    for privilege in PRIVILEGES:
        function = "has_any_column_privilege" if privilege in COLUMN_PRIVILEGES else "has_table_privilege"
        has = owner.execute(f"SELECT {function}(%s, 'rule_templates', %s) AS ok", (role, privilege)).fetchone()["ok"]  # noqa: S608
        assert has == (privilege in granted), (role, privilege)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO rule_templates (firm, program, account_size, phase, rule_version) VALUES ('A', 'B', '50K', 'funded', 'v')",
        "UPDATE rule_templates SET max_loss_usd = 1",
        "DELETE FROM rule_templates",
        "TRUNCATE rule_templates",
    ],
)
def test_a_person_reads_the_templates_but_cannot_change_them(owner, db, alice, statement):
    template = add_template(owner)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tenant_session(alice) as conn:
        conn.execute(statement)
    with db.tenant_session(alice) as conn:
        assert rule_templates.get(conn, template.id) == template


def test_the_operator_imports_and_edits_templates_but_cannot_remove_them(system, owner):
    with system.system_session("admin") as conn:
        counts, problems = rule_templates.import_csv(conn, csv_text(ROW), by="admin:op@example.test")
    assert problems == [] and counts.inserted == 1
    [template] = rule_templates.list_all(owner)
    with system.system_session("admin") as conn:
        changed = RuleValues({**template.rules.values, "max_loss_usd": D("2500")})
        rule_templates.update(conn, template.id, changed, by="admin:op@example.test")
    for statement in ("DELETE FROM rule_templates", "TRUNCATE rule_templates"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
            conn.execute(statement)
    after = rule_templates.get(owner, template.id)
    assert after.rules.values["max_loss_usd"] == D("2500.00") and after.updated_by == "admin:op@example.test"
