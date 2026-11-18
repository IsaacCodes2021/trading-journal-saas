"""The futures contracts Roundturn can work out P&L for: dollars per point and tick size.

A platform that exports fills without P&L (TradeSea's Orders export) needs each contract's point value to turn prices
into dollars, and its tick size to tell a whole number of ticks from a rounded average. This table sets money, so every
value here is checked by `tests/test_contracts.py` against the dollar value of one tick, the figure exchanges and
brokers publish, and the owner reads it before it changes.

Prices are decimals in the contract's own quoting units: index points, dollars per barrel, cents per bushel for grains,
cents per pound for livestock, and points of par for Treasuries (110.515625, not 110'16.5). A platform that writes
fractions converts them before using this table.

`finance.POINT_VALUES`, the twelve roots Tradovate's P&L is checked against, is left as it is; a test keeps the two in
agreement.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Contract:
    root: str
    name: str
    exchange: str
    point_value: Decimal  # dollars for a move of 1.00 in the quoted price, one contract
    tick_size: Decimal  # the smallest price step

    @property
    def tick_value(self) -> Decimal:
        """Dollars for one tick, one contract."""
        return self.point_value * self.tick_size


def _c(root: str, name: str, exchange: str, point_value: str, tick_size: str) -> Contract:
    return Contract(root, name, exchange, Decimal(point_value), Decimal(tick_size))


# Root, name, exchange, dollars per point, tick size. The dollar value of one tick is in the comment.
_TABLE = (
    # Equity indexes
    _c("ES", "E-mini S&P 500", "CME", "50", "0.25"),  # $12.50
    _c("MES", "Micro E-mini S&P 500", "CME", "5", "0.25"),  # $1.25
    _c("NQ", "E-mini Nasdaq-100", "CME", "20", "0.25"),  # $5.00
    _c("MNQ", "Micro E-mini Nasdaq-100", "CME", "2", "0.25"),  # $0.50
    _c("RTY", "E-mini Russell 2000", "CME", "50", "0.1"),  # $5.00
    _c("M2K", "Micro E-mini Russell 2000", "CME", "5", "0.1"),  # $0.50
    _c("YM", "E-mini Dow ($5)", "CBOT", "5", "1"),  # $5.00
    _c("MYM", "Micro E-mini Dow", "CBOT", "0.5", "1"),  # $0.50
    _c("EMD", "E-mini S&P MidCap 400", "CME", "100", "0.1"),  # $10.00
    _c("NKD", "Nikkei 225 (dollar)", "CME", "5", "5"),  # $25.00
    # Energy
    _c("CL", "Crude oil", "NYMEX", "1000", "0.01"),  # $10.00
    _c("MCL", "Micro crude oil", "NYMEX", "100", "0.01"),  # $1.00
    _c("QM", "E-mini crude oil", "NYMEX", "500", "0.025"),  # $12.50
    _c("NG", "Henry Hub natural gas", "NYMEX", "10000", "0.001"),  # $10.00
    _c("QG", "E-mini natural gas", "NYMEX", "2500", "0.005"),  # $12.50
    _c("RB", "RBOB gasoline", "NYMEX", "42000", "0.0001"),  # $4.20
    _c("HO", "Heating oil", "NYMEX", "42000", "0.0001"),  # $4.20
    # Metals
    _c("GC", "Gold", "COMEX", "100", "0.1"),  # $10.00
    _c("MGC", "Micro gold", "COMEX", "10", "0.1"),  # $1.00
    _c("SI", "Silver", "COMEX", "5000", "0.005"),  # $25.00
    _c("SIL", "Micro silver (1,000 oz)", "COMEX", "1000", "0.005"),  # $5.00
    _c("HG", "Copper", "COMEX", "25000", "0.0005"),  # $12.50
    _c("MHG", "Micro copper", "COMEX", "2500", "0.0005"),  # $1.25
    _c("PL", "Platinum", "NYMEX", "50", "0.1"),  # $5.00
    _c("PA", "Palladium", "NYMEX", "100", "0.5"),  # $50.00
    # Interest rates (points of par)
    _c("ZT", "2-year Treasury note", "CBOT", "2000", "0.00390625"),  # $7.8125
    _c("ZF", "5-year Treasury note", "CBOT", "1000", "0.0078125"),  # $7.8125
    _c("ZN", "10-year Treasury note", "CBOT", "1000", "0.015625"),  # $15.625
    _c("TN", "Ultra 10-year Treasury note", "CBOT", "1000", "0.015625"),  # $15.625
    _c("ZB", "Treasury bond", "CBOT", "1000", "0.03125"),  # $31.25
    _c("UB", "Ultra Treasury bond", "CBOT", "1000", "0.03125"),  # $31.25
    # Currencies
    _c("6E", "Euro FX", "CME", "125000", "0.00005"),  # $6.25
    _c("M6E", "Micro EUR/USD", "CME", "12500", "0.0001"),  # $1.25
    _c("6B", "British pound", "CME", "62500", "0.0001"),  # $6.25
    _c("M6B", "Micro GBP/USD", "CME", "6250", "0.0001"),  # $0.625
    _c("6J", "Japanese yen", "CME", "12500000", "0.0000005"),  # $6.25
    _c("6A", "Australian dollar", "CME", "100000", "0.00005"),  # $5.00
    _c("M6A", "Micro AUD/USD", "CME", "10000", "0.0001"),  # $1.00
    _c("6C", "Canadian dollar", "CME", "100000", "0.00005"),  # $5.00
    _c("6S", "Swiss franc", "CME", "125000", "0.00005"),  # $6.25
    _c("6N", "New Zealand dollar", "CME", "100000", "0.00005"),  # $5.00
    _c("6M", "Mexican peso", "CME", "500000", "0.00001"),  # $5.00
    # Grains (cents per bushel, except soybean oil in cents per pound and soybean meal in dollars per short ton)
    _c("ZC", "Corn", "CBOT", "50", "0.25"),  # $12.50
    _c("ZW", "Chicago SRW wheat", "CBOT", "50", "0.25"),  # $12.50
    _c("KE", "KC HRW wheat", "CBOT", "50", "0.25"),  # $12.50
    _c("ZS", "Soybeans", "CBOT", "50", "0.25"),  # $12.50
    _c("ZO", "Oats", "CBOT", "50", "0.25"),  # $12.50
    _c("ZL", "Soybean oil", "CBOT", "600", "0.01"),  # $6.00
    _c("ZM", "Soybean meal", "CBOT", "100", "0.1"),  # $10.00
    # Livestock (cents per pound)
    _c("LE", "Live cattle", "CME", "400", "0.025"),  # $10.00
    _c("GF", "Feeder cattle", "CME", "500", "0.025"),  # $12.50
    _c("HE", "Lean hogs", "CME", "400", "0.025"),  # $10.00
    # Crypto
    _c("BTC", "Bitcoin", "CME", "5", "5"),  # $25.00
    _c("MBT", "Micro Bitcoin", "CME", "0.1", "5"),  # $0.50
    _c("ETH", "Ether", "CME", "50", "0.5"),  # $25.00
    _c("MET", "Micro Ether", "CME", "0.1", "0.5"),  # $0.05
)

CONTRACTS: dict[str, Contract] = {c.root: c for c in _TABLE}


def contract_for(root: str) -> Contract | None:
    """The contract for a root ("NQ"), or None when Roundturn does not know it. Never guessed."""
    return CONTRACTS.get(root.strip().upper())


def price_format(tick_size: Decimal) -> tuple[str, str]:
    """The `_priceFormat` and `_priceFormatType` Tradovate writes for a contract with this tick size: the negative
    number of decimals, and "0" for decimal prices. Stored with fill pairs from platforms that have no such columns, so
    their rows look like Tradovate's; nothing on screen reads them."""
    exponent = tick_size.normalize().as_tuple().exponent
    return str(min(int(exponent), 0)), "0"
