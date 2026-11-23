"""Copy-trading groups (migration 0026, `docs/new-feature-copy-trading/decisions.csv`). Mirrors `lib/data/model.ts`."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .base import ApiModel
from .journal import AccountRefOut, RowId
from .money import Money

GroupName = Annotated[str, Field(min_length=1, max_length=80)]
Note = Annotated[str, Field(max_length=500)]


class CopyGroupRefOut(ApiModel):
    id: int
    name: str


class MembershipEventOut(ApiModel):
    id: int
    account_id: int
    event_type: Literal["added", "removed"]
    effective_date: date | None  # null: an "added" that counts every leader trade
    note: str
    created_at: datetime


class CopyGroupMemberOut(ApiModel):
    account: AccountRefOut
    role: Literal["leader", "follower"]
    active: bool  # in the group now (a follower that was removed keeps its past copies, so it is still listed)
    archived: bool  # the account itself is archived (passed, failed or closed)
    trade_count: int  # the leader's stored trades, or the copies this follower got from this group
    net_pnl: Money  # derived for a follower: the leader's net on each trade it copied (an estimate, decision 5)
    other_groups: list[CopyGroupRefOut]  # the other groups this account leads or is in now (highlighted when any)


class CopyGroupOut(ApiModel):
    id: int
    name: str
    leader: AccountRefOut
    leader_archived: bool  # an archived leader gets a warning: its group gets no new trades unless it is restored
    created_at: datetime
    fills_acknowledged_at: datetime
    members: list[CopyGroupMemberOut]  # the leader first, then followers by name
    net_pnl: Money  # every member's net added up, the leader's included
    events: list[MembershipEventOut]


class NewCopyGroupIn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    name: GroupName
    leader_account_id: RowId
    follower_account_ids: Annotated[list[RowId], Field(min_length=1, max_length=200)]
    copying_since: date | None = None  # followers count the leader's trades from this trading day; none: all of them
    acknowledge_fills: Literal[True]  # copies do not always fill the same: the group is not made without this
    note: Note = ""


class RenameCopyGroupIn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    name: GroupName


class MembershipChangeIn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RowId
    effective_date: date | None = None  # the trading day the change takes effect; none: today. Never earlier.
    note: Note = ""
