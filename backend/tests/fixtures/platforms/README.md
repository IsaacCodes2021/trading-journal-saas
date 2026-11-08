# Other platforms' exports, reconstructed

Ten files, one per export layout of the futures platforms traders use most, each describing **the same** made-up session:
`session.json` (Wednesday 14 October 2026, 7 trades, -696.50 gross). The import engine planned in
`docs/design/trade-import-export-plan.md` must turn every file into exactly those trades. Today's importer already does it
for `tradovate_performance.csv` (`tests/test_platform_fixtures.py`).

These are **not real exports.** Each layout is rebuilt from public sources (`docs/research/platform-exports.md`, with the
confidence of every detail); IDs, fees and excursions are illustrative. When a real file arrives, compare it with its
fixture and correct the generator.

**Never edit a file here by hand.** `tests/platform_fixtures.py` writes them all from `session.json`, and a test fails
when a file differs from what it writes. Change the generator (or the session), then run, from `backend/`:

```
.venv\Scripts\python.exe tests\platform_fixtures.py
```

| File | Platform, export | A row is | Times written as | Separator, line ends | Confidence |
|---|---|---|---|---|---|
| `tradovate_performance.csv` | Tradovate, Performance | a fill pair | `10/14/2026 06:31:05`, Los Angeles | comma, LF | high (the app's own) |
| `tradovate_orders.csv` | Tradovate, Orders | an order (fills merged), newest first | `10/14/2026 06:31:05`, Los Angeles; `Date` `10/14/26` | comma, LF | high names, medium layout |
| `topstepx_trades.csv` | TopstepX (ProjectX), Trades | a closed lot | `10/14/2026 06:31:05 -07:00` | comma, LF | high |
| `topstepx_orders.csv` | TopstepX (ProjectX), Orders | an order; `Bid` buys, `Ask` sells | `10/14/2026 06:31:05 -07:00` | comma, LF | high |
| `ninjatrader8_executions.csv` | NinjaTrader 8, Executions | a fill | `10/14/2026 6:31:05 AM`, Los Angeles | comma, CRLF | high |
| `ninjatrader8_executions_de.csv` | NinjaTrader 8, Executions, German Windows region | a fill | `14.10.2026 15:31:05`, Berlin; decimal commas | semicolon, CRLF | medium |
| `ninjatrader8_trades.csv` | NinjaTrader 8, Trades | a fill pair; `Profit` after commission | `10/14/2026 6:31:05 AM`, Los Angeles | comma, CRLF | high names |
| `rithmic_rtrader_completed_orders.csv` | R\|Trader Pro, Order History | an order, in the "Completed Orders" section | `2026-10-14 08:31:05`, Chicago | comma, all quoted, CRLF | medium |
| `quantower_trades.csv` | Quantower, Trades (CQG symbols: `ENQZ6`, `EPZ6`) | a fill | `10/14/2026 6:31:05 AM`, Los Angeles | comma, CRLF | medium |
| `sierrachart_trade_activity_log.txt` | Sierra Chart, Trade Activity Log | a fill (`ActivityType` `Fills`), plus two cancelled-order rows | `2026-10-14  09:31:05.000000` (two spaces), New York | tab, CRLF | high names |

What the session puts each importer through: a scale-out (T1), a scale-in (T2), one order filled in two parts in the
same second and a cancelled target (T3), crude oil's 0.01 tick and $1,000 point (T4), a reversal (one sell of 2 while
long 1: T5 and T6), and a gold trade after 18:00 New York time that counts toward 15 October (T7). Order-level files
(Tradovate Orders, R|Trader Pro) merge T3's two fills into one average price, 6720.50; the trade's P&L is the same.

Not here yet: ATAS (an Excel workbook; reading one needs a new library), TradingView (no confirmed header), and the long
tail, which the planned mapping screen covers.
