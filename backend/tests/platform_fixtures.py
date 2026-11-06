"""The reconstructed platform exports in tests/fixtures/platforms, written from session.json.

Every futures platform exports trades in its own layout. Until real files arrive (the import plan's Phase 0), each layout
here is rebuilt from public sources (docs/research/platform-exports.md) and filled with the one made-up session in
session.json, so every file has to import to the same trades. This module is the only writer of those files: change a
layout here, then run, from backend/,

    .venv\\Scripts\\python.exe tests\\platform_fixtures.py

A test checks that the files on disk are exactly what this writes. Values the sources did not show (an ID's shape, a fee,
an excursion) are illustrative; README.md in that folder lists what is confirmed and what is not.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PLATFORMS_DIR = Path(__file__).parent / "fixtures" / "platforms"
SESSION_FILE = PLATFORMS_DIR / "session.json"

LOS_ANGELES = ZoneInfo("America/Los_Angeles")  # the trader's computer, as in the Tradovate fixtures beside this folder
NEW_YORK = ZoneInfo("America/New_York")
CHICAGO = ZoneInfo("America/Chicago")
BERLIN = ZoneInfo("Europe/Berlin")

MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Illustrative costs, per contract. No two sources agree on a firm's rates, and the import does not read them (the
# account's fee schedule decides fees), so these only have to be plausible and consistent between the files.
COMMISSION_PER_SIDE = {"NQ": "1.29", "ES": "1.29", "MNQ": "0.52", "MES": "0.52", "CL": "1.54", "GC": "1.64"}
TOPSTEPX_FEES = {"NQ": "2.80", "ES": "2.80", "MNQ": "0.72", "MES": "0.72", "CL": "3.04", "GC": "3.24"}
TOPSTEPX_COMMISSIONS = {"NQ": "1.00", "ES": "1.00", "MNQ": "0.50", "MES": "0.50", "CL": "1.00", "GC": "1.00"}
CQG_ROOTS = {"NQ": "ENQ", "MNQ": "MNQ", "ES": "EP", "MES": "MES", "CL": "CLE", "GC": "GCE"}


# ---- the session -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fill:
    id: str
    number: int
    order: str
    at: datetime
    contract: str
    side: str
    qty: int
    price: Decimal


@dataclass(frozen=True)
class Order:
    id: str
    number: int
    contract: str
    side: str
    qty: int
    type: str
    limit: Decimal | None
    stop: Decimal | None
    role: str
    created_at: datetime
    status: str
    ended_at: datetime


@dataclass(frozen=True)
class Pair:
    id: str
    number: int
    contract: str
    direction: str
    qty: int
    entry: Fill
    exit: Fill
    gross_pnl: Decimal
    trade: int  # the number of the trade (flat to flat, one direction) the pair belongs to
    trade_date: date


@dataclass(frozen=True)
class Session:
    account: str
    contracts: dict[str, dict[str, Any]]
    orders: list[Order]
    fills: list[Fill]
    pairs: list[Pair]

    def order(self, order_id: str) -> Order:
        return next(o for o in self.orders if o.id == order_id)

    def fills_of(self, order: Order) -> list[Fill]:
        return [f for f in self.fills if f.order == order.id]

    def root(self, contract: str) -> str:
        return self.contracts[contract]["root"]

    def tick(self, contract: str) -> Decimal:
        return Decimal(self.contracts[contract]["tick"])


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _number(identifier: str) -> int:
    return int(identifier[1:])


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def load_session(path: Path = SESSION_FILE) -> Session:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fills = [
        Fill(
            f["id"], _number(f["id"]), f["order"], utc(f["at"]), f["contract"], f["side"], f["qty"], Decimal(f["price"])
        )
        for f in raw["fills"]
    ]
    by_id = {f.id: f for f in fills}
    trade_of = {pair: trade for trade in raw["trades"] for pair in trade["pairs"]}
    orders = [
        Order(
            o["id"],
            _number(o["id"]),
            o["contract"],
            o["side"],
            o["qty"],
            o["type"],
            _decimal(o["limit"]),
            _decimal(o["stop"]),
            o["role"],
            utc(o["created_at"]),
            o["status"],
            utc(o["ended_at"]),
        )
        for o in raw["orders"]
    ]
    pairs = [
        Pair(
            p["id"],
            _number(p["id"]),
            p["contract"],
            p["direction"],
            p["qty"],
            by_id[p["entry_fill"]],
            by_id[p["exit_fill"]],
            Decimal(p["gross_pnl"]),
            _number(trade_of[p["id"]]["id"]),
            date.fromisoformat(trade_of[p["id"]]["trade_date"]),
        )
        for p in raw["fill_pairs"]
    ]
    return Session(raw["account"], raw["contracts"], orders, fills, pairs)


# ---- formatting helpers ----------------------------------------------------------------------------------------------


def plain(value: Decimal) -> str:
    """25030.00 -> "25030", 6720.50 -> "6720.5": a number as a program prints it, not a price as a screen shows it."""
    return format(value.normalize(), "f")


def price(session: Session, contract: str, value: Decimal, decimals: int | None = None) -> str:
    """A price with the contract's own decimals (NQ 25012.25, CL 58.42, GC 4102.3), or a fixed number of them."""
    places = -session.tick(contract).as_tuple().exponent if decimals is None else decimals
    return f"{value:.{places}f}"


def us_clock(moment: datetime, zone: ZoneInfo) -> str:
    """10/14/2026 6:31:05 AM: Windows' United States short date and long time."""
    local = moment.astimezone(zone)
    hour = local.hour % 12 or 12
    return f"{local.month}/{local.day}/{local.year} {hour}:{local:%M:%S} {'AM' if local.hour < 12 else 'PM'}"


def with_offset(moment: datetime, zone: ZoneInfo) -> str:
    """10/14/2026 06:31:05 -07:00: TopstepX's times, in the browser's zone with its offset."""
    local = moment.astimezone(zone)
    offset = local.strftime("%z")
    return f"{local:%m/%d/%Y %H:%M:%S} {offset[:3]}:{offset[3:]}"


def cme_trading_date(moment: datetime) -> date:
    """The CME Globex trading day: the session that opens at 18:00 New York time counts toward the next day."""
    return (moment.astimezone(NEW_YORK) + timedelta(hours=6)).date()


def money(value: Decimal, *, negative: str = "minus") -> str:
    """$165.00, -$150.00, or $(150.00) (Tradovate's way)."""
    amount = f"${abs(value):,.2f}"
    if value >= 0:
        return amount
    return f"$({abs(value):,.2f})" if negative == "parentheses" else f"-{amount}"


def month_code(session: Session, contract: str) -> tuple[str, str]:
    """("Z", "2026") for a December 2026 contract."""
    year, month = session.contracts[contract]["month"].split("-")
    return MONTH_CODES[int(month) - 1], year


def ninjatrader_instrument(session: Session, contract: str) -> str:
    """NQ 12-26: NinjaTrader's root, a space, then the contract month and two-digit year."""
    year, month = session.contracts[contract]["month"].split("-")
    return f"{session.root(contract)} {month}-{year[2:]}"


def sierra_symbol(session: Session, contract: str) -> str:
    """NQZ26_FUT_CME: Sierra Chart's own symbol style (its CME data feeds)."""
    code, year = month_code(session, contract)
    return f"{session.root(contract)}{code}{year[2:]}_FUT_{session.contracts[contract]['exchange']}"


def cqg_symbol(session: Session, contract: str) -> str:
    """ENQZ6: the CQG symbol a Quantower trader connected through CQG sees (EP is ES, GCE is GC)."""
    code, year = month_code(session, contract)
    return f"{CQG_ROOTS[session.root(contract)]}{code}{year[-1]}"


def us_date(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def table(
    header: list[str], rows: list[list[str]], *, delimiter: str = ",", newline: str = "\n", quote_all=False
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        delimiter=delimiter,
        lineterminator=newline,
        quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
    )
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


@dataclass(frozen=True)
class PositionStep:
    before: int
    after: int


def position_steps(session: Session) -> dict[str, PositionStep]:
    """The signed position in each contract just before and just after every fill."""
    held: dict[str, int] = defaultdict(int)
    steps = {}
    for fill in sorted(session.fills, key=lambda f: (f.at, f.number)):
        before = held[fill.contract]
        held[fill.contract] += fill.qty if fill.side == "buy" else -fill.qty
        steps[fill.id] = PositionStep(before, held[fill.contract])
    return steps


def opens(step: PositionStep) -> bool:
    """True when a fill adds to a position or starts one (an entry), not when it reduces or reverses one."""
    return abs(step.after) > abs(step.before) and step.before * step.after >= 0


def average_fill(fills: list[Fill]) -> Decimal | None:
    quantity = sum(f.qty for f in fills)
    return sum((f.price * f.qty for f in fills), Decimal(0)) / quantity if quantity else None


# ---- the layouts -----------------------------------------------------------------------------------------------------


def tradovate_performance(session: Session) -> str:
    """Tradovate, Account Reports, Performance: one row per fill pair (the layout today's importer reads)."""
    header = (
        "symbol,_priceFormat,_priceFormatType,_tickSize,buyFillId,sellFillId,qty,buyPrice,sellPrice,pnl,"
        "boughtTimestamp,soldTimestamp,duration"
    ).split(",")
    rows = []
    for pair in session.pairs:
        buy, sell = (pair.entry, pair.exit) if pair.direction == "long" else (pair.exit, pair.entry)
        tick = session.tick(pair.contract)
        seconds = int(abs((sell.at - buy.at).total_seconds()))
        minutes, rest = divmod(seconds, 60)
        rows.append(
            [
                pair.contract,
                str(tick.as_tuple().exponent),
                "0",
                str(tick),
                str(tradovate_fill_id(buy)),
                str(tradovate_fill_id(sell)),
                str(pair.qty),
                price(session, pair.contract, buy.price),
                price(session, pair.contract, sell.price),
                money(pair.gross_pnl, negative="parentheses"),
                f"{buy.at.astimezone(LOS_ANGELES):%m/%d/%Y %H:%M:%S}",
                f"{sell.at.astimezone(LOS_ANGELES):%m/%d/%Y %H:%M:%S}",
                f"{minutes}min {rest}sec" if minutes else f"{rest}sec",
            ]
        )
    return table(header, rows)


def tradovate_fill_id(fill: Fill) -> int:
    return 41870000100 + fill.number * 10


def tradovate_orders(session: Session) -> str:
    """Tradovate, Account Reports, Orders: one row per order, filled or not, newest first. Enum cells begin with a
    space (" Buy", " Filled"), which the journals that read this file strip."""
    header = (
        "orderId,Account,Order ID,B/S,Contract,Product,Product Description,avgPrice,filledQty,Fill Time,lastCommandId,"
        "Status,_priceFormat,_priceFormatType,_tickSize,spreadDefinitionId,Version ID,Timestamp,Date,Quantity,Text,Type,"
        "Limit Price,Stop Price,decimalLimit,decimalStop,Filled Qty,Avg Fill Price,decimalFillAvg"
    ).split(",")
    rows = []
    for order in sorted(session.orders, key=lambda o: (o.ended_at, o.number), reverse=True):
        fills = session.fills_of(order)
        average = average_fill(fills)
        filled = sum(f.qty for f in fills)
        info = session.contracts[order.contract]
        tick = session.tick(order.contract)
        created = order.created_at.astimezone(LOS_ANGELES)
        last_fill = max((f.at for f in fills), default=None)
        rows.append(
            [
                str(9100000000 + order.number),
                session.account,
                str(9100000000 + order.number),
                f" {order.side.title()}",
                order.contract,
                info["root"],
                info["description"],
                plain(average) if average is not None else "",
                str(filled),
                f"{last_fill.astimezone(LOS_ANGELES):%m/%d/%Y %H:%M:%S}" if last_fill else "",
                str(9200000000 + order.number),
                " Filled" if order.status == "filled" else " Canceled",
                str(tick.as_tuple().exponent),
                "0",
                str(tick),
                "",
                str(9300000000 + order.number),
                f"{created:%m/%d/%Y %H:%M:%S}",
                f"{created:%m/%d/%y}",
                str(order.qty),
                "",
                f" {order.type.title()}",
                price(session, order.contract, order.limit) if order.limit else "",
                price(session, order.contract, order.stop) if order.stop else "",
                plain(order.limit) if order.limit else "",
                plain(order.stop) if order.stop else "",
                str(filled),
                price(session, order.contract, average) if average is not None else "",
                plain(average) if average is not None else "",
            ]
        )
    return table(header, rows)


def topstepx_trades(session: Session) -> str:
    """TopstepX (ProjectX), Trades tab, Export: one row per closed lot, times with the browser's offset, PnL gross,
    fees and commissions in two columns."""
    header = (
        "Id,ContractName,EnteredAt,ExitedAt,EntryPrice,ExitPrice,Fees,PnL,Size,Type,TradeDay,TradeDuration,Commissions"
    ).split(",")
    rows = []
    for pair in session.pairs:
        root = session.root(pair.contract)
        held = pair.exit.at - pair.entry.at
        hours, rest = divmod(int(held.total_seconds()), 3600)
        minutes, seconds = divmod(rest, 60)
        fraction = (pair.number * 2166590) % 10_000_000
        rows.append(
            [
                str(3142050000 + pair.number),
                pair.contract,
                with_offset(pair.entry.at, LOS_ANGELES),
                with_offset(pair.exit.at, LOS_ANGELES),
                f"{pair.entry.price:.9f}",
                f"{pair.exit.price:.9f}",
                f"{Decimal(TOPSTEPX_FEES[root]) * pair.qty:.5f}",
                f"{pair.gross_pnl:.9f}",
                str(pair.qty),
                pair.direction.title(),
                f"{us_date(pair.trade_date)} 00:00:00 -05:00",
                f"{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:07d}",
                f"{Decimal(TOPSTEPX_COMMISSIONS[root]) * pair.qty:.5f}",
            ]
        )
    return table(header, rows)


def topstepx_orders(session: Session) -> str:
    """TopstepX (ProjectX), Orders tab, Export: one row per order. Side is the book taken (Bid buys, Ask sells)."""
    header = (
        "Id,AccountName,ContractName,Status,Type,Size,Side,CreatedAt,TradeDay,FilledAt,CancelledAt,TriggeredAt,"
        "StopPrice,LimitPrice,ExecutePrice,TriggeredPrice,PositionDisposition,CreationDisposition,RejectionReason,"
        "ExchangeOrderId,PlatformOrderId"
    ).split(",")
    creation = {"entry": "Trader", "reversal": "Trader", "target": "TakeProfit", "stop": "StopLoss"}
    rows = []
    for order in sorted(session.orders, key=lambda o: (o.created_at, o.number)):
        fills = session.fills_of(order)
        average = average_fill(fills)
        filled = order.status == "filled"
        if not filled:
            disposition = "Undetermined"
        else:
            disposition = "Opening" if order.role == "entry" else "Closing"
        rows.append(
            [
                str(3614074600 + order.number),
                session.account,
                order.contract,
                "Filled" if filled else "Cancelled",
                order.type.title(),
                str(order.qty),
                "Bid" if order.side == "buy" else "Ask",
                with_offset(order.created_at, LOS_ANGELES),
                f"{us_date(cme_trading_date(order.created_at))} 00:00:00 -05:00",
                with_offset(order.ended_at, LOS_ANGELES) if filled else "",
                "" if filled else with_offset(order.ended_at, LOS_ANGELES),
                with_offset(order.ended_at, LOS_ANGELES) if filled and order.type == "stop" else "",
                plain(order.stop) if order.stop else "",
                plain(order.limit) if order.limit else "",
                plain(average) if average is not None else "",
                plain(order.stop) if filled and order.stop else "",
                disposition,
                creation[order.role],
                "",
                str(7000000000 + order.number),
                str(8000000000 + order.number),
            ]
        )
    return table(header, rows)


EXECUTION_NAMES = {"entry": "Entry", "reversal": "Entry", "target": "Target1", "stop": "Stop1"}


def _ninjatrader_execution_rows(session: Session, clock: Callable[[datetime], str], number: Callable[[str], str]):
    steps = position_steps(session)
    rows = []
    for fill in sorted(session.fills, key=lambda f: (f.at, f.number)):
        step = steps[fill.id]
        order = session.order(fill.order)
        position = "-" if step.after == 0 else f"{abs(step.after)} {'L' if step.after > 0 else 'S'}"
        commission = Decimal(COMMISSION_PER_SIDE[session.root(fill.contract)]) * fill.qty
        rows.append(
            [
                ninjatrader_instrument(session, fill.contract),
                fill.side.title(),
                str(fill.qty),
                number(price(session, fill.contract, fill.price)),
                clock(fill.at),
                str(tradovate_fill_id(fill)),
                "Entry" if opens(step) else "Exit",
                position,
                str(9100000000 + order.number),
                EXECUTION_NAMES[order.role],
                commission,
                "1",
                session.account,
                "Tradovate",
            ]
        )
    return rows


NINJATRADER_EXECUTIONS = (
    "Instrument,Action,Quantity,Price,Time,ID,E/X,Position,Order ID,Name,Commission,Rate,Account,Connection".split(",")
)


def ninjatrader_executions(session: Session) -> str:
    """NinjaTrader 8, Trade Performance, display Executions, Generate, right-click, Export: one row per fill, with the
    Windows region's formats (here the United States) and the computer's time zone."""
    rows = _ninjatrader_execution_rows(session, lambda moment: us_clock(moment, LOS_ANGELES), lambda text: text)
    for row in rows:
        row[10] = f"${row[10]:.2f}"
    return table(NINJATRADER_EXECUTIONS, rows, newline="\r\n")


def ninjatrader_executions_europe(session: Session) -> str:
    """The same NinjaTrader export from a computer set to a German region: semicolons (the Windows list separator),
    day-first dates, decimal commas, and the Berlin time zone."""
    rows = _ninjatrader_execution_rows(
        session,
        lambda moment: f"{moment.astimezone(BERLIN):%d.%m.%Y %H:%M:%S}",
        lambda text: text.replace(".", ","),
    )
    for row in rows:
        row[10] = f"{row[10]:.2f} $".replace(".", ",")
    return table(NINJATRADER_EXECUTIONS, rows, delimiter=";", newline="\r\n")


def ninjatrader_trades(session: Session) -> str:
    """NinjaTrader 8, Trade Performance, display Trades, Export: one row per fill pair (NinjaTrader pairs first in,
    first out). Profit and Cum. net profit are after commission; MAE, MFE and ETD are illustrative."""
    header = (
        "Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,"
        "Entry name,Exit name,Profit,Cum. net profit,Commission,Clearing fee,Exchange fee,IP fee,NFA fee,MAE,MFE,ETD,Bars"
    ).split(",")
    rows = []
    running = Decimal(0)
    zero = money(Decimal(0))
    for pair in session.pairs:
        root = session.root(pair.contract)
        commission = Decimal(COMMISSION_PER_SIDE[root]) * pair.qty * 2
        profit = pair.gross_pnl - commission
        running += profit
        one_tick = session.tick(pair.contract) * Decimal(session.contracts[pair.contract]["point_value"]) * pair.qty
        mae = max(-pair.gross_pnl, Decimal(0)) + one_tick
        mfe = max(pair.gross_pnl, Decimal(0)) + one_tick
        rows.append(
            [
                str(pair.number),
                ninjatrader_instrument(session, pair.contract),
                session.account,
                "",
                pair.direction.title(),
                str(pair.qty),
                price(session, pair.contract, pair.entry.price),
                price(session, pair.contract, pair.exit.price),
                us_clock(pair.entry.at, LOS_ANGELES),
                us_clock(pair.exit.at, LOS_ANGELES),
                EXECUTION_NAMES[session.order(pair.entry.order).role],
                EXECUTION_NAMES[session.order(pair.exit.order).role],
                money(profit),
                money(running),
                money(commission),
                zero,
                zero,
                zero,
                zero,
                money(mae),
                money(mfe),
                money(mfe - pair.gross_pnl),
                "1",
            ]
        )
    return table(header, rows, newline="\r\n")


def rithmic_completed_orders(session: Session) -> str:
    """R|Trader Pro, Order History, one day, Export as CSV: a "Working Orders" section and a "Completed Orders"
    section, every cell quoted, times in the platform's own time-zone setting (here Chicago)."""
    header = [
        "Account",
        "Status",
        "Remarks",
        "Buy/Sell",
        "Qty To Fill",
        "Symbol",
        "Avg Fill Price",
        "Limit Price",
        "Stop Price",
        "Order Type",
        "Order Number",
        "Create Time",
        "Update Time",
        "Qty Filled",
        "Commission Fill Rate",
    ]
    kinds = {"market": "Market", "limit": "Limit", "stop": "Stop Market"}
    rows = []
    for order in sorted(session.orders, key=lambda o: (o.ended_at, o.number)):
        fills = session.fills_of(order)
        average = average_fill(fills)
        rows.append(
            [
                session.account,
                "Filled" if order.status == "filled" else "Cancelled",
                "",
                "B" if order.side == "buy" else "S",
                str(order.qty),
                order.contract,
                price(session, order.contract, average, 4) if average is not None else "",
                price(session, order.contract, order.limit, 4) if order.limit else "",
                price(session, order.contract, order.stop, 4) if order.stop else "",
                kinds[order.type],
                str(1700000000 + order.number),
                f"{order.created_at.astimezone(CHICAGO):%Y-%m-%d %H:%M:%S}",
                f"{order.ended_at.astimezone(CHICAGO):%Y-%m-%d %H:%M:%S}",
                str(sum(f.qty for f in fills)),
                COMMISSION_PER_SIDE[session.root(order.contract)],
            ]
        )
    working = table(["Account", "Status", "Buy/Sell", "Qty To Fill", "Symbol"], [], newline="\r\n", quote_all=True)
    completed = table(header, rows, newline="\r\n", quote_all=True)
    return f"Working Orders\r\n{working}\r\n\r\nCompleted Orders\r\n{completed}"


def quantower_trades(session: Session) -> str:
    """Quantower, Trades panel, a date range, right-click, Export data, all columns, comma separated: one row per fill,
    here from a CQG connection (CQG's symbols), times in the computer's zone. P/L appears on closing fills only."""
    header = [
        "Account",
        "Date/Time",
        "Symbol",
        "Description",
        "Symbol type",
        "Expiration date",
        "Strike price",
        "Side",
        "Order Type",
        "Quantity",
        "Price",
        "Gross P/L",
        "Fee",
        "Net P/L",
        "Trade Value",
        "Trade ID",
        "Order ID",
        "Position ID",
        "Connection",
        "Comment",
        "Exchange",
    ]
    realized: dict[str, Decimal] = defaultdict(Decimal)
    for pair in session.pairs:
        realized[pair.exit.id] += pair.gross_pnl
    position_of = {}  # a reversal fill closes one position and opens the next: it is listed under the one it closes
    for pair in session.pairs:
        position_of.setdefault(pair.entry.id, pair.trade)
        position_of.setdefault(pair.exit.id, pair.trade)
    rows = []
    for fill in sorted(session.fills, key=lambda f: (f.at, f.number)):
        info = session.contracts[fill.contract]
        order = session.order(fill.order)
        fee = -Decimal(COMMISSION_PER_SIDE[info["root"]]) * fill.qty
        gross = realized.get(fill.id)
        code, year = month_code(session, fill.contract)
        rows.append(
            [
                session.account,
                us_clock(fill.at, LOS_ANGELES),
                cqg_symbol(session, fill.contract),
                f"{info['description']} {MONTH_NAMES[MONTH_CODES.index(code)]} {year[2:]}",
                "Futures",
                us_date(date.fromisoformat(info["last_trade"])),
                "",
                fill.side.title(),
                order.type.title(),
                str(fill.qty),
                price(session, fill.contract, fill.price),
                f"{gross:.2f}" if gross is not None else "",
                f"{fee:.2f}",
                f"{(gross or Decimal(0)) + fee:.2f}",
                f"{fill.price * fill.qty * Decimal(info['point_value']):.2f}",
                str(5550000000 + fill.number),
                str(9100000000 + order.number),
                str(6660000000 + position_of[fill.id]),
                "CQG",
                "",
                info["exchange"],
            ]
        )
    return table(header, rows, newline="\r\n")


def sierra_chart_trade_activity(session: Session) -> str:
    """Sierra Chart, Trade > Trade Activity Log, Trade Activity tab, Save Log As: tab-separated, every activity type
    the display shows (here fills plus the two cancelled orders), times in the Global Time Zone setting (here New
    York), written with two spaces between date and time."""
    header = [
        "ActivityType",
        "DateTime",
        "TransDateTime",
        "Symbol",
        "OrderActionSource",
        "InternalOrderID",
        "ServiceOrderID",
        "OrderType",
        "Quantity",
        "BuySell",
        "Price",
        "Price2",
        "OrderStatus",
        "FillPrice",
        "FilledQuantity",
        "TradeAccount",
        "OpenClose",
        "ParentInternalOrderID",
        "PositionQuantity",
        "FillExecutionServiceID",
        "HighDuringPosition",
        "LowDuringPosition",
        "Note",
        "AccountBalance",
        "ExchangeOrderID",
        "ClientOrderID",
        "TimeInForce",
        "Username",
        "IsAutomated",
    ]
    steps = position_steps(session)
    parents = {}
    for order in session.orders:
        if order.role in ("target", "stop"):
            entry = max(
                (
                    o
                    for o in session.orders
                    if o.contract == order.contract and o.role == "entry" and o.created_at <= order.created_at
                ),
                key=lambda o: o.created_at,
            )
            parents[order.id] = str(100 + entry.number)

    def stamp(moment: datetime) -> str:
        local = moment.astimezone(NEW_YORK)
        return f"{local:%Y-%m-%d}  {local:%H:%M:%S}.000000"

    def order_price(order: Order) -> str:
        level = order.limit or order.stop
        return price(session, order.contract, level) if level else ""

    events = []
    for fill in session.fills:
        order = session.order(fill.order)
        step = steps[fill.id]
        row = [
            "Fills",
            stamp(fill.at),
            stamp(fill.at),
            sierra_symbol(session, fill.contract),
            "",
            str(100 + order.number),
            str(9100000000 + order.number),
            order.type.title(),
            str(fill.qty),
            fill.side.title(),
            order_price(order),
            "",
            "Filled",
            price(session, fill.contract, fill.price),
            str(fill.qty),
            session.account,
            "Open" if opens(step) else "Close",
            parents.get(order.id, ""),
            str(step.after),
            str(tradovate_fill_id(fill)),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
        events.append((fill.at, fill.number, row))
    for order in session.orders:
        if order.status != "cancelled":
            continue
        row = [
            "Orders",
            stamp(order.ended_at),
            stamp(order.ended_at),
            sierra_symbol(session, order.contract),
            "",
            str(100 + order.number),
            str(9100000000 + order.number),
            order.type.title(),
            str(order.qty),
            order.side.title(),
            order_price(order),
            "",
            "Canceled",
            "",
            "0",
            session.account,
            "",
            parents.get(order.id, ""),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
        events.append((order.ended_at, 100 + order.number, row))
    rows = [row for _, _, row in sorted(events, key=lambda event: (event[0], event[1]))]
    return table(header, rows, delimiter="\t", newline="\r\n")


@dataclass(frozen=True)
class Layout:
    file: str
    platform: str
    export: str
    render: Callable[[Session], str]


LAYOUTS = (
    Layout("tradovate_performance.csv", "Tradovate", "Performance", tradovate_performance),
    Layout("tradovate_orders.csv", "Tradovate", "Orders", tradovate_orders),
    Layout("topstepx_trades.csv", "TopstepX (ProjectX)", "Trades", topstepx_trades),
    Layout("topstepx_orders.csv", "TopstepX (ProjectX)", "Orders", topstepx_orders),
    Layout("ninjatrader8_executions.csv", "NinjaTrader 8", "Executions", ninjatrader_executions),
    Layout(
        "ninjatrader8_executions_de.csv", "NinjaTrader 8", "Executions, German region", ninjatrader_executions_europe
    ),
    Layout("ninjatrader8_trades.csv", "NinjaTrader 8", "Trades", ninjatrader_trades),
    Layout("rithmic_rtrader_completed_orders.csv", "Rithmic R|Trader Pro", "Order History", rithmic_completed_orders),
    Layout("quantower_trades.csv", "Quantower", "Trades", quantower_trades),
    Layout("sierrachart_trade_activity_log.txt", "Sierra Chart", "Trade Activity Log", sierra_chart_trade_activity),
)


def write_all() -> None:
    session = load_session()
    for layout in LAYOUTS:
        (PLATFORMS_DIR / layout.file).write_bytes(layout.render(session).encode("utf-8"))
        print(f"wrote {layout.file}")


if __name__ == "__main__":
    write_all()
