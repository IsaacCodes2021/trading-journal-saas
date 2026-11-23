"""Copy-trading groups: one leader account whose trades count for its followers too (migration 0026).

Making a group, renaming it, adding and removing followers write only the group and its membership log; no trade is
written or copied (`docs/new-feature-copy-trading/decisions.csv`, decisions 1 to 3).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Response

from ... import finance
from ...accounts import Account
from ...pg import accounts as pg_accounts
from ...pg import copy_groups as pg_groups
from ...pg import settings as pg_settings
from ...timeutil import trading_date
from ..accounts_view import account_ref
from ..cents import cents
from ..deps import ERROR_RESPONSES, ClockDep, TenantDep, WriteTenantDep
from ..refusals import refusals
from ..schemas import Problem
from ..schemas.copy_groups import (
    CopyGroupMemberOut,
    CopyGroupOut,
    CopyGroupRefOut,
    MembershipChangeIn,
    MembershipEventOut,
    NewCopyGroupIn,
    RenameCopyGroupIn,
)
from ..schemas.journal import PathId

router = APIRouter(
    prefix="/api/v1",
    tags=["copy-groups"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}},
)

_DUPLICATE = "You already have a group with that name, or that account already leads a group."


def groups_out(
    groups: list[pg_groups.CopyGroup],
    accounts: dict[int, Account],
    totals: dict[tuple[int, int], tuple[int, Decimal]],
) -> list[CopyGroupOut]:
    """Each group with its members' trade counts and net P&L (derived for followers) and the group's sum."""
    current: dict[int, list[pg_groups.CopyGroup]] = {}  # account id to the groups it leads or is in now
    for group in groups:
        current.setdefault(group.leader_account_id, []).append(group)
        for follower in group.follower_ids():
            if group.is_active(follower):
                current.setdefault(follower, []).append(group)

    def member(group: pg_groups.CopyGroup, account_id: int, role: str) -> CopyGroupMemberOut:
        account = accounts[account_id]
        count, net = totals.get((group.id, account_id), (0, finance.ZERO))
        return CopyGroupMemberOut(
            account=account_ref(account),
            role=role,  # type: ignore[arg-type]
            active=role == "leader" or group.is_active(account_id),
            archived=account.archived,
            trade_count=count,
            net_pnl=cents(net),
            other_groups=[
                CopyGroupRefOut(id=other.id, name=other.name)
                for other in current.get(account_id, [])
                if other.id != group.id
            ],
        )

    out: list[CopyGroupOut] = []
    for group in groups:
        followers = sorted(group.follower_ids(), key=lambda a: (accounts[a].name.casefold(), a))
        members = [member(group, group.leader_account_id, "leader")] + [member(group, a, "follower") for a in followers]
        leader = accounts[group.leader_account_id]
        out.append(
            CopyGroupOut(
                id=group.id,
                name=group.name,
                leader=account_ref(leader),
                leader_archived=leader.archived,
                created_at=group.created_at,
                fills_acknowledged_at=group.fills_acknowledged_at,
                members=members,
                net_pnl=cents(sum((m.net_pnl for m in members), finance.ZERO)),
                events=[
                    MembershipEventOut(
                        id=e.id,
                        account_id=e.account_id,
                        event_type=e.event_type,  # type: ignore[arg-type]
                        effective_date=e.effective_date,
                        note=e.note,
                        created_at=e.created_at,
                    )
                    for e in group.events
                ],
            )
        )
    return out


def _all_out(conn) -> list[CopyGroupOut]:
    accounts = {a.id: a for a in pg_accounts.list_accounts(conn)}
    return groups_out(pg_groups.list_groups(conn), accounts, pg_groups.member_totals(conn))


def _one_out(conn, copy_group_id: int) -> CopyGroupOut:
    return next(g for g in _all_out(conn) if g.id == copy_group_id)


@router.get("/copy-groups", response_model=list[CopyGroupOut])
def list_copy_groups(tenant: TenantDep) -> list[CopyGroupOut]:
    """Every copy-trading group, by name, with each member's net P&L (all time, archived accounts included)."""
    with tenant.session() as conn:
        return _all_out(conn)


@router.post("/copy-groups", response_model=CopyGroupOut, status_code=201)
def create_copy_group(body: NewCopyGroupIn, tenant: WriteTenantDep, clock: ClockDep) -> CopyGroupOut:
    """A leader account and its followers. Imports stay on the leader; the followers' trades are worked out from it."""
    with refusals(what="account", duplicate=_DUPLICATE), tenant.session() as conn:
        group = pg_groups.create_group(
            conn,
            body.name,
            leader_account_id=body.leader_account_id,
            follower_account_ids=body.follower_account_ids,
            acknowledged_at=clock(),
            copying_since=body.copying_since,
            note=body.note,
        )
        return _one_out(conn, group.id)


@router.put("/copy-groups/{copy_group_id}", response_model=CopyGroupOut)
def rename_copy_group(copy_group_id: PathId, body: RenameCopyGroupIn, tenant: WriteTenantDep) -> CopyGroupOut:
    with refusals(what="group", duplicate=_DUPLICATE), tenant.session() as conn:
        pg_groups.rename_group(conn, copy_group_id, body.name)
        return _one_out(conn, copy_group_id)


@router.delete("/copy-groups/{copy_group_id}", status_code=204)
def delete_copy_group(copy_group_id: PathId, tenant: WriteTenantDep) -> Response:
    """End a group. Its membership log goes with it; every trade stays on the leader."""
    with refusals(what="group"), tenant.session() as conn:
        pg_groups.delete_group(conn, copy_group_id)
    return Response(status_code=204)


@router.post("/copy-groups/{copy_group_id}/followers", response_model=CopyGroupOut)
def add_copy_follower(
    copy_group_id: PathId, body: MembershipChangeIn, tenant: WriteTenantDep, clock: ClockDep
) -> CopyGroupOut:
    """The account copies the leader from the given trading day (today when none), never an earlier one."""
    with refusals(what="group or account"), tenant.session() as conn:
        today = trading_date(clock(), pg_settings.trading_session(conn))
        pg_groups.add_follower(
            conn,
            copy_group_id,
            body.account_id,
            effective_date=body.effective_date or today,
            today=today,
            note=body.note,
        )
        return _one_out(conn, copy_group_id)


@router.post("/copy-groups/{copy_group_id}/followers/remove", response_model=CopyGroupOut)
def remove_copy_follower(
    copy_group_id: PathId, body: MembershipChangeIn, tenant: WriteTenantDep, clock: ClockDep
) -> CopyGroupOut:
    """The account stops copying from the given trading day (today when none). Its past copies stay counted."""
    with refusals(what="follower"), tenant.session() as conn:
        today = trading_date(clock(), pg_settings.trading_session(conn))
        pg_groups.remove_follower(
            conn,
            copy_group_id,
            body.account_id,
            effective_date=body.effective_date or today,
            today=today,
            note=body.note,
        )
        return _one_out(conn, copy_group_id)
