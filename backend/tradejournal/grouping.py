"""Group fill pairs into round-trip trades.

A Tradovate Performance row pairs one buy fill with one sell fill, so a trade that
scaled in or out shows up as several rows. Rows of the same symbol and direction
belong to one trade when:

1. they share an exit fill (the trade scaled in),
2. they share an entry fill (the trade scaled out), or
3. the position never returned to flat between them.

On clean data rule 3 already implies 1 and 2; the fill-ID rules are kept as an
explicit guarantee. Positions are walked separately per direction, so a reversal
(one fill that closes a long and opens a short) becomes two trades.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from .models import FillPair


@dataclass
class FillEvent:
    """One execution, aggregated over every fill pair that references it."""

    at: datetime
    fill_id: int
    delta: int  # signed position change: buys add, sells subtract
    rows: list[int] = field(default_factory=list)


def position_events(pairs: Sequence[FillPair], indices: Iterable[int]) -> list[FillEvent]:
    """The fills behind the given pairs, in execution order.

    Rows that share a fill ID are the same execution, so their quantities add up.
    Fill IDs increase with execution time, which orders fills within the same second.
    """
    events: dict[int, FillEvent] = {}
    for i in indices:
        pair = pairs[i]
        for fill_id, at, delta in (
            (pair.buy_fill_id, pair.bought_at, pair.qty),
            (pair.sell_fill_id, pair.sold_at, -pair.qty),
        ):
            event = events.setdefault(fill_id, FillEvent(at, fill_id, 0))
            event.delta += delta
            event.rows.append(i)
    return sorted(events.values(), key=lambda e: (e.at, e.fill_id))


def max_position(pairs: Sequence[FillPair]) -> int:
    """Largest number of contracts held at once during a trade."""
    position = peak = 0
    for event in position_events(pairs, range(len(pairs))):
        position += event.delta
        peak = max(peak, abs(position))
    return peak


def row_order(pair: FillPair) -> tuple:
    return (pair.entry_at, pair.entry_fill_id, pair.exit_at, pair.exit_fill_id)


def group_fill_pairs(pairs: Sequence[FillPair]) -> list[list[int]]:
    """Group fill pairs (all from one account) into trades.

    Returns lists of indices into `pairs`, one list per trade, each ordered by entry
    and the lists ordered by trade entry time.
    """
    union_find = _UnionFind(len(pairs))
    by_side: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, pair in enumerate(pairs):
        by_side[(pair.symbol, pair.direction)].append(i)

    for indices in by_side.values():
        _union_shared_fills(union_find, pairs, indices)
        _union_open_positions(union_find, pairs, indices)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(pairs)):
        groups[union_find.find(i)].append(i)
    ordered = [sorted(group, key=lambda i: row_order(pairs[i])) for group in groups.values()]
    return sorted(ordered, key=lambda group: row_order(pairs[group[0]]))


def _union_shared_fills(union_find: _UnionFind, pairs: Sequence[FillPair], indices: list[int]) -> None:
    # Within one direction a fill is only ever an entry or only ever an exit, so the
    # fill ID alone identifies "same entry" (rule 2) and "same exit" (rule 1).
    first_row_with_fill: dict[int, int] = {}
    for i in indices:
        for fill_id in (pairs[i].entry_fill_id, pairs[i].exit_fill_id):
            union_find.union(i, first_row_with_fill.setdefault(fill_id, i))


def _union_open_positions(union_find: _UnionFind, pairs: Sequence[FillPair], indices: list[int]) -> None:
    long_side = pairs[indices[0]].direction == "long"
    position = 0
    segment_anchor: int | None = None
    for event in position_events(pairs, indices):
        for i in event.rows:
            if segment_anchor is None:
                segment_anchor = i
            union_find.union(segment_anchor, i)
        position += event.delta
        # Flat ends the trade. Crossing to the wrong side can only come from
        # inconsistent data; start over instead of merging everything after it.
        if position == 0 or (position < 0 if long_side else position > 0):
            position = 0
            segment_anchor = None


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[max(root_a, root_b)] = min(root_a, root_b)
