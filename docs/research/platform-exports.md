# What the popular futures platforms export (research for the import engine)

**Written:** 2026-10-21, for `docs/design/trade-import-export-plan.md` (Phase 0). **Result:** ten test files in
`backend/tests/fixtures/platforms/`, one per export layout below, all describing the same made-up trading session.

No real export from these platforms was available, so each layout was rebuilt from what other people have published:
the platforms' own documentation where it exists, the import guides of competing journals, and above all the source code
of open-source trading journals that parse these files (several of them quote a real export line by line). Only facts
were taken from that code (column names, value formats, quirks); no code was copied. A real file from a beta tester
still beats all of this: when one arrives, compare it with the fixture and correct the generator
(`backend/tests/platform_fixtures.py`).

Abbreviations: CSV (comma-separated values), TSV (tab-separated values), XLSX (an Excel workbook), FIFO (first in,
first out: the oldest open contract is the first one closed), CQG and Rithmic (the two data and order-routing networks
most non-Tradovate prop accounts run on), UTC (Coordinated Universal Time).

## Which platforms, and why these

Most futures prop firms offer some mix of Tradovate, NinjaTrader, TradingView (on top of Tradovate), the Rithmic family
(R|Trader Pro, Quantower, Sierra Chart, ATAS, Bookmap) and ProjectX's web platform (TopstepX at Topstep, under other names
at about 17 firms) ([pipback](https://pipback.com/blog/best-trading-platforms-for-futures-prop-firms);
[TradeZella's ProjectX firm list](https://help.tradezella.com/en/articles/11943188-projectx-how-to-import-trades-from-projectx-prop-firms-platforms-into-tradezella-using-the-file-upload-method)).
The open-source futures journals built importers for the same set, which is a fair proxy for what traders ask for. No
source gives real usage shares; the beta sign-ups' prop firms (plan task 0.3) will.

## Confidence

- **High:** the column names are confirmed by two or more independent sources, at least one of them reading real files.
- **Medium:** the names come from one guide or one parser, or several agree on names but not on order or value formats.
- **VERIFY:** a detail the fixture had to choose without evidence. Listed per platform; the import must not depend on
  any of them until a real file confirms it.

## The layouts

### Tradovate, Performance (built today)

The layout the app already imports, from the owner's own files. A row is one buy fill matched to one sell fill. The
fixture `tradovate_performance.csv` is this layout for the shared session, and today's importer reads it into exactly the
expected trades (a test proves it). **Confidence: high** (real files).

### Tradovate, Orders

Account Reports, Orders tab, Download. **One row per order**, filled, cancelled or rejected; partial fills are merged into
one average price. Journals ask for this file instead of Performance because they pair fills themselves
([TradesViz](https://www.tradesviz.com/brokers/Tradovate)). No commissions: those are in the separate Cash History export.

- Columns used by every parser: `orderId`, `Account`, `B/S`, `Contract`, `Product`, `Status`, `Fill Time`, `Date`,
  `Filled Qty`, `Avg Fill Price`, plus `avgPrice`, `filledQty`, `decimalFillAvg` (TradeNote, LuxAlgo's journal, The
  Trader Log, a converter that writes Tradovate-shaped files). **High.**
- Enum cells begin with a space (`" Buy"`, `" Filled"`): TradeNote strips them. **Medium.**
- `Date` is month-first with a two-digit or four-digit year; times are in the computer's local zone. **Medium.**
- VERIFY: the full column list and order. The fixture follows the Performance file's style (`_priceFormat`,
  `_priceFormatType`, `_tickSize`) and adds `lastCommandId`, `spreadDefinitionId`, `Version ID`; the converter lists
  `Side` and `Order Time` instead. Whether a cancelled order is spelled `Canceled` or `Cancelled`.
- Also seen: a **Position History** export (`Position ID`, `Buy Price`, `Sell Price`, `P/L`, `Paired Qty`,
  `Bought Timestamp`, `Sold Timestamp`), from one parser only. No fixture yet.

### TopstepX (ProjectX), Trades

Trades tab, Export, a date range (an empty file means the range was too narrow). One parser quotes a real export from
28 September 2026, header and row:

```
Id,ContractName,EnteredAt,ExitedAt,EntryPrice,ExitPrice,Fees,PnL,Size,Type,TradeDay,TradeDuration,Commissions
```

- Times are **month-first with the browser's UTC offset** (`09/28/2026 10:43:54 +02:00`); `TradeDay` is midnight at
  `-05:00`. Prices carry nine decimals, `PnL` is **gross** (before fees), `Fees` (exchange and NFA) and `Commissions` are
  two columns, `Type` is `Long` or `Short`, `TradeDuration` is a .NET time span with seven decimals. **High** (the quoted
  file, The Trader Log, TopSignal, Deltalytix and MMplatinum agree on the names).
- VERIFY: how a scale-in or scale-out is split into rows. The fixture writes one row per FIFO lot pair.

### TopstepX (ProjectX), Orders

```
Id,AccountName,ContractName,Status,Type,Size,Side,CreatedAt,TradeDay,FilledAt,CancelledAt,TriggeredAt,StopPrice,
LimitPrice,ExecutePrice,TriggeredPrice,PositionDisposition,CreationDisposition,RejectionReason,ExchangeOrderId,PlatformOrderId
```

- **`Side` is the book taken: `Bid` buys, `Ask` sells.** `PositionDisposition` is `Opening`, `Closing` or
  `Undetermined`; `CreationDisposition` is `Trader`, `StopLoss` or `TakeProfit`; prices are short decimals (`31246.5`). A
  stop that was moved keeps only its last price. **High** (a quoted real export; TradeNote and LuxAlgo read the same
  names).
- VERIFY: the `PositionDisposition` of a reversing order (the fixture says `Closing`).

### NinjaTrader 8, Executions

Trade Performance window, display *Executions*, Generate, right-click, Export.

```
Instrument,Action,Quantity,Price,Time,ID,E/X,Position,Order ID,Name,Commission,Rate,Account,Connection
```

- One row per fill. `Instrument` is `NQ 12-26`, `Action` `Buy`/`Sell`, `E/X` `Entry`/`Exit`, `Commission` carries a
  currency sign. **High** (TradeZella's guide, a NinjaTrader forum user's file, TradeNote, LuxAlgo).
- **The file follows the computer's Windows region:** a forum user's export was semicolon-separated, and the date format
  changed with the Windows short-date setting. NinjaTrader has no setting of its own for it. So the fixture comes twice:
  United States formats (`ninjatrader8_executions.csv`) and a German region (`ninjatrader8_executions_de.csv`:
  semicolons, `14.10.2026 15:31:05`, decimal commas, Berlin time). **Medium** for the German variant.
- Exports can carry several accounts (an `Account` column), and copy-traders' files repeat the same fills per account
  (LuxAlgo's fixture). Columns can be removed or reordered by the trader.
- VERIFY: whether a reversing fill is one row or two; the shape of `ID` per connection; the `Name` values.

### NinjaTrader 8, Trades

The same window, display *Trades*. One row per round trip, paired first in, first out.

```
Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,Entry name,
Exit name,Profit,Cum. net profit,Commission,Clearing fee,Exchange fee,IP fee,NFA fee,MAE,MFE,ETD,Bars
```

- **High** for the names (three parsers; note the lower-case `fee`). Times like `10/14/2026 6:31:05 AM` on a United
  States computer. **The headers are translated when NinjaTrader runs in another language** (Deltalytix maps the French
  ones: `Compte`, `Prix de sortie`, `Heure de sortie`). `MAE` and `MFE` (the worst and best open profit during the trade)
  are data no other file gives.
- VERIFY: whether `Profit` is after commission (Deltalytix says so; the fixture follows it) and how negative money is
  written (`-$150.00` in the fixture, `($150.00)` possible).

### Rithmic R|Trader Pro, Order History

Order History, an account and **one day** (one day per file), add the *Qty Filled* and *Commission Fill Rate* columns
in the Completed Orders section, Export as CSV.

- The file has **two sections**, `Working Orders` then `Completed Orders`, each a heading line and a quoted header, every
  cell in quotes, prices with four decimals (`"21861.0000"`). This is from a real file the app already refuses
  (`backend/tests/test_parser.py`). **High.**
- Completed Orders columns: `Account`, `Status`, `Remarks`, `Buy/Sell` (`B`/`S`), `Qty To Fill`, `Symbol` (`NQZ6`),
  `Qty Filled`, `Avg Fill Price`, `Limit Price`, `Order Number`, `Create Time`, `Update Time`, `Commission Fill Rate`
  (TradeZella, TradeNote, Deltalytix). **Medium** for the order.
- Times in R|Trader Pro's own time-zone setting, as `2026-10-14 08:31:05` (TradeNote); Deltalytix also reads
  `14/10/2026 08:31`. Treasury prices can come in 32nds (`110'16`). One file can hold several accounts.
- VERIFY: the default time zone; whether the time headers carry a zone suffix (TradeNote matches any header that
  *contains* `Update Time`); the `Order Type` words.

### Quantower, Trades

Trades panel, a date range, right-click, Export data, *Check all columns*, comma separated. One row per execution.

- TradeZella lists 21 headers: `Account`, `Date/Time`, `Symbol`, `Description`, `Symbol type`, `Expiration date`,
  `Strike price`, `Side`, `Order Type`, `Quantity`, `Price`, `Gross P/L`, `Fee`, `Net P/L`, `Trade Value`, `Trade ID`,
  `Order ID`, `Position ID`, `Connection`, `Comment`, `Exchange`. **Medium.**
- **Symbols depend on the connection:** through CQG they are CQG's (`EP` is ES, `ENQ` NQ, `CLE` CL, `GCE` GC; Deltalytix
  keeps a table of about 60), through Rithmic the usual `NQZ6`. The fixture uses CQG's. Times like
  `10/14/2026 6:31:05 AM` in the computer's zone.
- VERIFY: P/L on opening fills (blank in the fixture), the sign of `Fee` (negative in the fixture), the date formats.

### Sierra Chart, Trade Activity Log

Trade > Trade Activity Log, Trade Activity tab, Display Settings, *Fills*, File > Save Log As.

- **Tab-separated**; field names without spaces (`ActivityType`, `DateTime`, `Symbol`, `BuySell`, `Quantity`,
  `FillPrice`, `FilledQuantity`, `PositionQuantity`, `OpenClose`, `FillExecutionServiceID`, ...). Only rows the display
  shows are saved, so a file can also hold order and position rows: keep `ActivityType` = `Fills`. **High** for the field
  list ([Sierra Chart's documentation](https://www.sierrachart.com/index.php?page=doc/TradeActivityLog.php)) and the
  format (a tear-sheet tool's loader).
- `DateTime` is `2026-10-14  09:31:05.000000`, **with two spaces**, in Sierra Chart's Global Time Zone setting. Symbols
  look like `MESM26_FUT_CME`. `OpenClose` is informational and "can be inaccurate" (the documentation's words).
- VERIFY: the literal `ActivityType` of order rows (`Orders` in the fixture), the last columns (`IsAutomated`).

## Not in the fixtures yet

| Platform | Why not | What is known |
|---|---|---|
| **ATAS** | Exports an Excel workbook (Statistics, Export); reading XLSX needs a new library (the owner's yes first) | [ATAS help](https://help.atas.net/en/support/solutions/articles/72000660488-trading-journal-trading-statistics-); Deltalytix has an ATAS importer |
| **TradingView** | No confirmed header. Traders on TradingView through Tradovate can export from Tradovate instead | One journal reads `Symbol, Side, Type, Qty, Fill Price, Status, Commission, Closing Time` |
| **Volumetrica (Deepcharts), MotiveWave, Jigsaw, Bookmap, Interactive Brokers, TradeStation** | The long tail | The mapping screen of plan Phase 2 covers them |

## The test session

Every fixture describes one made-up Wednesday, 14 October 2026, in `backend/tests/fixtures/platforms/session.json`:
7 trades in NQ, MNQ, ES, CL, MES and GC, total **-696.50** gross. It contains what breaks importers: a scale-out, a
scale-in, one order filled in two parts in the same second, two cancelled orders, a reversal (one fill that closes a long
and opens a short), contracts with different ticks and point values, and a trade after 18:00 New York time that counts
toward the next trading day. The fixtures' times are written in six different ways (four zones, UTC offsets, AM/PM,
day-first). `backend/tests/fixtures/platforms/README.md` lists each file.

## Sources (read 2026-10-21)

Platform documentation and guides:
- Sierra Chart, Trade Activity Log: https://www.sierrachart.com/index.php?page=doc/TradeActivityLog.php
- TradeZella import guides: [Quantower](https://www.tradezella.com/integrations/quantower),
  [NinjaTrader](https://www.tradezella.com/integrations/ninjatrader),
  [R|Trader Pro](https://www.tradezella.com/integrations/rithmic-r-trader),
  [TopstepX](https://www.tradezella.com/integrations/topstepx)
- TradesViz, Tradovate (Orders, not Performance; Cash History for fees): https://www.tradesviz.com/brokers/Tradovate
- NinjaTrader forum, executions export and the Windows date format: https://forum.ninjatrader.com/forum/ninjatrader-8/platform-technical-support-aa/100325-executions-tab-export-date-format-not-as-expected
- MMplatinum, TopstepX headers: https://mmplatinum.com/en/broker/topstepx/

Open-source journals (read for column names and formats only):
- TradeNote, `src/utils/brokers.js` (Tradovate Orders, NinjaTrader, Rithmic, TopstepX Orders): https://github.com/Eleven-Trading/TradeNote
- LuxAlgo trade-journal, `docs/importers.md` and its importer tests: https://github.com/LuxAlgo/trade-journal
- Deltalytix, `app/[locale]/dashboard/components/import/` (TopstepX, NinjaTrader trades, Rithmic, Quantower, ATAS): https://github.com/hugodemenez/deltalytix
- Trading_Journal by 0xsickre, `src/lib/journal/topstepx-export.ts` and `topstepx-orders.ts` (quote real exports): https://github.com/0xsickre/Trading_Journal
- The Trader Log, Tradovate and TopstepX parsers: https://github.com/nicolasgomeztoua/thetraderlog
- TopSignal, `backend/app/services/trade_imports.py`: https://github.com/drewstake/TopSignal
- mff-json-to-tradovate-csv, Tradovate Orders columns: https://github.com/dcastillo3/mff-json-to-tradovate-csv
- sierra-chart-tear-sheet, `tearsheet/dataio/loader.py`: https://github.com/masilver99/sierra-chart-tear-sheet
- analyzer_framework, NinjaTrader trades parser: https://github.com/Nogor-design/analyzer_framework
