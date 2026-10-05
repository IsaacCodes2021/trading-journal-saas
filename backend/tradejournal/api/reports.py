"""Breaking the trades down: by contract, weekday, time of day, how long they were held, tag and account (F-P4).

Pure, like `dashboard.py`: trades in, numbers out. Each group's numbers are `finance.summarize` over that group's trades,
so a win, a profit factor and a drawdown mean here exactly what they mean on the dashboard.

How the groups are made, and why:

- **symbol**: the contract's root ("NQ"), not the month, so the roll from one contract to the next does not split it.
- **weekday**: the weekday of the *trading day* (the person's own day boundary), the same notion as the calendar. All seven
  are listed, Monday first, so the rows line up whatever the data.
- **hour**: the hour the trade was *entered*, in the person's own timezone. Only hours with trades are listed, earliest first.
- **hold**: how long the trade lasted, in seven fixed steps from "Under 1 minute" to "Over 2 hours". All seven are listed.
- **tag**: one trade counts once for each tag it carries (so the groups add up to more than the trades), and a trade with
  no tag is in "Untagged". Best net result first.
- **account**: one group per account that has trades in view, best net result first.

Groups with no fixed shape (symbol, hour, tag, account) are listed only if they have trades; weekday and hold always show
every step.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import tzinfo

from .. import finance
from ..journal import Tag
from ..queries import TradeRecord
from .dashboard import summary_of
from .schemas.journal import ReportBy, ReportGroupOut, ReportOut

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# (label, the first number of seconds that is NOT in the step). The last step has no end.
HOLD_STEPS: tuple[tuple[str, int | None], ...] = (
    ("Under 1 minute", 60),
    ("1 to 5 minutes", 300),
    ("5 to 15 minutes", 900),
    ("15 to 30 minutes", 1800),
    ("30 to 60 minutes", 3600),
    ("1 to 2 hours", 7200),
    ("Over 2 hours", None),
)


def hold_step(seconds: int) -> int:
    """Which of `HOLD_STEPS` a hold time falls in: a step includes its lower edge and excludes its upper (60 s is "1 to 5")."""
    for index, (_, end) in enumerate(HOLD_STEPS):
        if end is None or seconds < end:
            return index
    return len(HOLD_STEPS) - 1  # unreachable: the last step has no end


def _net(group: Sequence[TradeRecord]):
    return sum((t.net_pnl for t in group), finance.ZERO)


def _group(key: str, label: str, detail: str | None, trades: Sequence[TradeRecord]) -> ReportGroupOut:
    return ReportGroupOut(
        key=key,
        label=label,
        detail=detail,
        summary=summary_of(trades),
    )


def _by_best_result(groups: Mapping[str, tuple[str, str | None, list[TradeRecord]]]) -> list[ReportGroupOut]:
    """Best net result first; ties by label, so the order is the same every time."""
    ordered = sorted(groups.items(), key=lambda item: (-_net(item[1][2]), item[1][0].casefold(), item[0]))
    return [_group(key, label, detail, trades) for key, (label, detail, trades) in ordered]


def _collect(
    trades: Sequence[TradeRecord], describe: Callable[[TradeRecord], tuple[str, str, str | None]]
) -> dict[str, tuple[str, str | None, list[TradeRecord]]]:
    groups: dict[str, tuple[str, str | None, list[TradeRecord]]] = {}
    for trade in trades:
        key, label, detail = describe(trade)
        groups.setdefault(key, (label, detail, []))[2].append(trade)
    return groups


def build_report(
    by: ReportBy,
    trades: Sequence[TradeRecord],
    tags: Mapping[int, Sequence[Tag]],
    group_names: Mapping[int, str],
    timezone: tzinfo,
) -> ReportOut:
    """`trades` in the order they closed. `tags` maps a trade id to its tags and `group_names` a tag's category to its name
    (both only read for `by="tag"`); `timezone` is the person's own, for the hour a trade was entered."""
    if by == "symbol":
        groups = _by_best_result(_collect(trades, lambda t: (t.symbol_root, t.symbol_root, None)))
    elif by == "account":
        groups = _by_best_result(
            _collect(
                trades,
                lambda t: (
                    str(t.account_id),
                    t.account_name,
                    f"{t.firm} · {'Funded' if t.account_type == 'funded' else 'Evaluation'}",
                ),
            )
        )
    elif by == "weekday":
        days: dict[int, list[TradeRecord]] = defaultdict(list)
        for trade in trades:
            days[trade.trade_date.weekday()].append(trade)
        groups = [_group(str(i), name, None, days[i]) for i, name in enumerate(WEEKDAYS)]
    elif by == "hour":
        hours: dict[int, list[TradeRecord]] = defaultdict(list)
        for trade in trades:
            hours[trade.entry_at.astimezone(timezone).hour].append(trade)
        groups = [_group(str(h), f"{h:02d}:00", None, hours[h]) for h in sorted(hours)]
    elif by == "hold":
        steps: dict[int, list[TradeRecord]] = defaultdict(list)
        for trade in trades:
            steps[hold_step(trade.hold_seconds)].append(trade)
        groups = [_group(str(i), label, None, steps[i]) for i, (label, _) in enumerate(HOLD_STEPS)]
    else:  # tag
        by_tag: dict[str, tuple[str, str | None, list[TradeRecord]]] = {}
        for trade in trades:
            carried = tags.get(trade.id, ())
            if not carried:
                by_tag.setdefault("untagged", ("Untagged", None, []))[2].append(trade)
            for tag in carried:
                by_tag.setdefault(str(tag.id), (tag.name, group_names.get(tag.group_id), []))[2].append(trade)
        groups = _by_best_result(by_tag)
    return ReportOut(by=by, groups=groups, summary=summary_of(trades))
