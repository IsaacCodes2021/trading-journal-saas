# Trading Journal

A local trading journal and analytics app for futures traders, in the spirit of
Tradezella. It imports Tradovate **Performance** CSV exports, rebuilds round-trip
trades from the fill pairs, and stores everything in a SQLite file on your machine.
No cloud services, accounts, or telemetry.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | CSV parser, trade grouping, SQLite schema, tests, CLI | **done** |
| 2 | Import screen, trade log, dashboard, settings | **done** |
| 3 | Calendar, day view, reports | next |
| 4 | Insights, prop account tracking, journaling | |

## Requirements

- Python 3.12 (the version the project is tested and locked on). On Windows, tick
  "Add python.exe to PATH" when installing from python.org. If you have several
  Pythons installed, `run.bat` / `run.sh` pick 3.12.

That's it. The interface is written in Python with [NiceGUI](https://nicegui.io),
so there's no Node.js or separate frontend build.

## Start the app

**Windows:** double-click `run.bat`, or run it from a terminal.

**macOS / Linux:** run `./run.sh` (the first time, `chmod +x run.sh`).

Your browser opens at <http://127.0.0.1:8765>. The first start creates a Python
environment and installs packages, which needs an internet connection. After that
the app runs fully offline. Close the terminal window (or press Ctrl+C) to stop it.

Options, passed after `run.bat` / `./run.sh`:

| Option | Effect |
|---|---|
| `--port 9000` | use another port |
| `--no-browser` | don't open a browser tab |
| `--data-dir D:\journal` | keep the database in another folder |

### Privacy

- The server listens on `127.0.0.1` only, so other devices on your network can't reach it.
- Every script, style, font and chart library ships inside the Python packages. The
  page makes no requests to the internet.
- There are no accounts and no telemetry. Your data is the `data/` folder.

## Screens

- **Evaluation vs funded.** Every account is marked as an evaluation or a funded
  account, chosen when you create it and changeable in Settings. The switch at the
  start of the filter row (`All accounts | Funded | Evaluation`) shows only that kind
  of account's trades, and the accounts dropdown then lists only those accounts, so
  you can narrow further. Trades take their type from their account, so
  re-marking an account moves all its history at once.
- **Archiving.** When an account passes, fails or closes, open it in Settings and
  click **Archive…**. Pick how it ended (evaluations: Passed, Failed or Closed;
  funded: Failed or Closed), the end date (defaults to its last trading day) and an
  optional note. Archived accounts and their trades drop out of the dashboard, the
  trade log and the import list. Nothing is deleted: turn on **Show archived** in
  the filter row to include them, or **Restore** the account in Settings. Archiving
  a passed evaluation offers to set up the funded account next, pre-filled with the
  same firm, size and fees. Settings shows your evaluation pass rate (passed vs.
  failed; closed ones don't count). In the Archived list, passed accounts are
  marked green with a check, failed ones red with a ✕, and closed ones stay
  neutral.
- **Costs and payouts.** Record what each account cost and what it paid out, kept
  separate from trading P&L. Enter the purchase cost when creating an account (the
  New account dialog on the Import page or in Settings): it's saved as an
  evaluation fee, or an activation fee for a funded account. In Settings, every
  account (active or archived) has a **Costs and payouts** section to add
  evaluation fees, resets, activation fees, monthly fees, other costs and payouts,
  each with a date and note, or delete them. Each account's row shows its cost and
  payouts. The top of Settings → Accounts totals everything (spent on accounts,
  payouts received, prop business net) and lists accounts with no cost recorded.
- **Firms.** Apex, Bulenox, Lucid, My Funded Futures, Take Profit, Tradeify, or
  Other. The Firms filter lists the firms you have accounts with, and narrows the
  accounts dropdown to match. To add a firm, add it to `FIRMS` in
  `backend/tradejournal/accounts.py`.
- **Dashboard.** Filter by account type, firm, accounts, date range (presets or
  custom), symbol and long/short. Shows net P&L, win rate, profit factor, expectancy, trade count, day
  win rate, average win and loss, win/loss ratio, largest win and loss, and max
  drawdown. Below that are the cumulative net P&L curve (hover for each trade), the
  drawdown underneath it, the performance score radar, and a daily net P&L chart
  with a table view.
- **Trade log.** Every trade, sortable, with a filter under each column and a search
  box. The same filter row as the dashboard applies, and your filter choices carry
  over between the two pages. The trade count and net at the top follow every
  filter, including the column filters. The Journal column marks trades that have
  notes and how many screenshots they have.
- **Trade detail.** Click a trade to open it. The header shows the account, date,
  times, hold and net P&L, with ‹ › to step through trades in the log's current
  order. Stats are on the left; on the right are:
  - **Tags** (see below).
  - **Notes**: a rich-text editor (bold, lists, quotes, links, full screen) that
    saves itself a moment after you stop typing. **Insert template** adds Setup /
    Entry / Management and exit / Lesson headings.
  - **Screenshots**: drop image files, click + to browse, or paste with Ctrl+V
    (e.g. right after Win+Shift+S). Click a thumbnail to view it full size, step
    through, open it in a tab, or delete it. PNG, JPEG, GIF and WebP up to 25 MB;
    files are checked by content, not name.
  - **Fill pairs**, folded away at the bottom.
- **Tags.** Tags are shared by all trades and shown as colored pills; each tag
  keeps its color everywhere. In a trade's detail panel:
  - **On this trade**: its tags; × takes one off this trade only.
  - **Add**: every other tag you've made; click one to add it. The
    **All categories** dropdown narrows this to one category (Setup, Mistake,
    Emotion, or your own).
  - **New tag**: type a name, pick its category, press Enter. It's added to this
    trade and offered on every other trade from then on.

  Changes save immediately. The trade log's Tags column shows the pills, and typing
  in that column's filter (e.g. `scalp`) finds tagged trades.

  **Settings → Tags** (or **Manage tags** in the trade panel) lists every tag by
  category, with how many trades carry it. Type in a category's **Add a tag** box to
  create one ahead of time. Click a tag to edit it:
  - **Name** and **Category**: rename it or move it to another category; its trades
    keep it. Renaming it to a tag that already exists in that category offers to
    merge the two: the trades move to the existing tag, which keeps its color.
  - **Color**: nine presets, **Your colors** (custom colors already on your tags,
    ready to reuse), and **+** to pick any color. Text on a pill is darkened or
    lightened automatically so it stays readable in both themes. New tags get the
    preset fewest tags use.
  - **Delete tag** removes it from every trade, after asking if it's in use.

  Click a category's name to rename or delete it (deleting removes its tags), and add
  categories at the bottom.
- **Import.** Pick or create an account, drop one or more CSVs, and check the
  preview: rows parsed, new rows, duplicates, rows that belong to another account,
  errors and warnings, and trades created or extended. Nothing is saved until you
  click Import. Below is the import history, where every import can be undone after
  a preview of what will be removed.
- **Settings.** Your timezone, when a trading day starts, each account's details,
  fee schedule, costs and payouts, and the tag editor. Saving an account's fees
  recomputes its trades.

The sun/moon button in the header switches between the dark and light themes.

### Performance score

Each measure scores 0 to 100, and the overall score is their plain average. All P&L
is net of fees.

| Measure | What's measured | Scoring |
|---|---|---|
| Win rate | wins ÷ (wins + losses) | 60% or higher = 100, scaling down to 0 at 0% |
| Profit factor | total won ÷ total lost | 1.0 or lower = 0, 2.5 or higher = 100. No losing trades = 100 |
| Avg win/loss | average win ÷ average loss | 2.5 or higher = 100, scaling down to 0 |
| Consistency | best day's share of all winning-day profit | 20% or less = 100, 100% (one day made it all) = 0 |
| Max drawdown | deepest drop in cumulative net P&L ÷ total won | 0% = 100, giving back 100% or more = 0 |
| Recovery factor | net P&L ÷ max drawdown | 3 or higher = 100, 0 or lower = 0 |

Breakeven trades (net exactly $0) count toward neither wins nor losses. The
dashboard's "How it's scored" panel shows your value and score for each measure.

## Manual setup (optional)

`run.bat` / `run.sh` set up what the app needs to run. For development (tests, linting)
use [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs exactly
the versions recorded in `backend/uv.lock`:

```powershell
cd backend
uv sync        # creates .venv with the pinned runtime and dev tools, on Python 3.12
```

The commands below use `uv run`, so you never need to activate the venv (which also
avoids PowerShell execution-policy prompts). The CLI examples further down use
`.venv\Scripts\python`; on macOS / Linux that is `.venv/bin/python`.

## Run the tests and checks

```powershell
cd backend
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

CI runs the same three on every pull request.

## Dependencies

Versions are declared in `backend/pyproject.toml` and locked, with hashes, in
`backend/uv.lock`. `run.bat`, `run.sh`, the container image and CI all install from the lock
with `uv sync --frozen`, so they get exactly the locked versions. There is no committed
`requirements.txt`: it used to be generated from the lock, and Dependabot edited it instead
of `uv.lock`. After changing dependencies, run `uv lock` in `backend/` and commit both files.
CI exports the lock to run `pip-audit` on the pinned versions for known vulnerabilities.

The fixtures in `backend/tests/fixtures/` are copies of real exports. The main one,
`01-05-2024-performance.csv`, has 14 rows that must collapse into 8 trades.

## Hosted API (work in progress)

The hosted version replaces the NiceGUI screens with a JSON API and a React app on one origin
([design](docs/design/README.md)). Today `backend/tradejournal/api/` has the skeleton only (security headers,
error format, CSRF check, the `Money` type, serving the built front end); the data endpoints arrive with the
Postgres port in M2.

```powershell
cd backend
uv run uvicorn tradejournal.api.app:create_app --factory --port 8000    # needs `pnpm build` in frontend/ to serve pages
uv run python -m tradejournal.api.openapi                              # rewrites backend/openapi.json
```

Settings come from environment variables (`ApiConfig` in `api/config.py`). Development needs none.

| Variable | Meaning |
|---|---|
| `TJ_ENV` | `development` (default) or `production`, which refuses to start without the settings below |
| `TJ_APP_ORIGIN` | Where browsers reach the app, such as `https://app.example.com`: no path, no trailing slash |
| `TJ_SESSION_SECRET` | At least 32 bytes as hex; `python -c "import secrets; print(secrets.token_hex(32))"` |
| `TJ_WEB_DIST` | The built front end (default `frontend/dist`) |
| `TJ_APP_VERSION` | The build id, sent as `X-App-Version` (CI sets the git sha) |

## Command line (optional)

Everything the Import and Settings screens do is also available from the command
line, which is handy for bulk imports. Run these from `backend/`. They use the same
database as the app, `trading-journal/data/journal.db`. Set `TRADEJOURNAL_DATA_DIR`
to use another folder, or pass `--db path\to\file.db`.

```powershell
# Create an account. --fee is $ per contract round trip: a bare number is the
# default for every symbol, and ROOT=RATE overrides one symbol root.
# --type is evaluation (the default) or funded.
.venv\Scripts\python -m tradejournal accounts add "Apex 07" --firm Apex --type funded --size 50000 --balance 50000 --fee 4.12 --fee MNQ=1.52

# Preview an import. Nothing is saved; you see rows parsed, trades built, duplicates, errors.
.venv\Scripts\python -m tradejournal import "C:\path\to\03-13-2024-performance.csv" --account "Apex 07" --dry-run

# Import for real. Wildcards work, and overlapping files are deduplicated.
.venv\Scripts\python -m tradejournal import "C:\path\to\*.csv" --account "Apex 07"

# Look at trades. --fills shows the raw fill pairs under each trade.
.venv\Scripts\python -m tradejournal trades --account "Apex 07" --date 2024-03-13 --fills

# Import history, and undo an import (--dry-run first to see what it would remove).
.venv\Scripts\python -m tradejournal imports
.venv\Scripts\python -m tradejournal undo 3 --dry-run
.venv\Scripts\python -m tradejournal undo 3

# Change fees. Existing trades are recomputed.
.venv\Scripts\python -m tradejournal fees "Apex 07" NQ=4.50

# Settings: CSV/display timezone and when a trading day starts.
.venv\Scripts\python -m tradejournal settings
.venv\Scripts\python -m tradejournal settings timezone America/Chicago
```

Import each file into the account it came from. Positions from different accounts
that overlap in time would otherwise merge into one trade.

## How trades are built

Each CSV row is a **fill pair**, one buy fill matched to one sell fill, not a trade.
Rows of the same account, symbol and direction are one trade when any of these hold:

1. **Shared exit fill**: the trade scaled in.
2. **Shared entry fill**: the trade scaled out.
3. **The position never returned to flat** between them. Fills are walked in execution
   order per symbol and direction, so this also catches `buy 2, buy 2, sell 2, sell 2`
   where no fill ID is shared.

The fill pairs stay linked to their trade for drill-down.

Details that go beyond the raw rules:

- **Direction.** Long if the buy came first, short if the sell did. When both
  timestamps are in the same second, the lower fill ID came first. Tradovate assigns
  fill IDs in execution order, and this held for all 2,088 rows with distinct
  timestamps in your exports. Without the tiebreak, a same-second row in
  `11-17-2023` would be misread as a long.
- **Reversals.** A single fill that closes a long and opens a short (or the reverse)
  ends one trade and starts the next.
- **Hold time.** First entry to last exit. CSV timestamps are truncated to the second,
  while Tradovate's `duration` column comes from the exact fill times ("4sec" where
  the timestamps say 5). When one fill pair spans the whole trade, its duration is
  used. Otherwise the timestamps are.
- **Trading day.** By default a trading day starts at 18:00 New York time (CME
  Globex), so a 16:30 Pacific trade on the 19th counts toward the 20th. Set
  `trading_day_start` to `00:00` and `trading_day_timezone` to your timezone for plain
  calendar days.
- **Points and ticks.** The quantity-weighted move per contract, positive when it
  went your way. Ticks use the row's `_tickSize`.
- **Fees.** `total contracts × $ per round trip` for the symbol root, falling back to
  the account default. Net P&L = CSV P&L − fees.

## Imports, duplicates and undo

- A fill pair is unique by `(buyFillId, sellFillId)`. Re-importing an overlapping
  export skips rows you already have. Rows that already belong to a different
  account are skipped and reported.
- A later export can extend a trade you already imported, for example a scale-out
  that finished after your mid-day export. The existing trade is updated in place
  and keeps its notes, tags and screenshots. If a new row bridges two existing
  trades, they merge and their journal entries are combined.
- Undoing an import removes the fill pairs it added, except rows another import also
  contained, and regroups what's left. A trade that loses all its fill pairs is
  deleted; the undo preview tells you if any of those had journal entries.
- Files that aren't Tradovate Performance exports, such as Rithmic or R|Trader order
  history, are rejected with an explanation. Bad rows are reported by line number and
  skipped. The rest of the file still imports.

## Storage

- Everything lives in `data/`: `journal.db` and the `screenshots/` folder. Back up
  that folder to back up your journal. Screenshot files get random names; the
  database remembers which trade each belongs to and its original file name.
  Deleting a screenshot deletes its file, and files left behind when an import is
  undone are cleaned up the next time the app starts.
- Money, prices and fee rates are exact decimal strings in SQLite and `Decimal` in
  Python, never floats. All financial math, including every dashboard statistic, is
  in `backend/tradejournal/finance.py`. Numbers become floats only when sent to the
  browser for drawing charts and sorting table columns.
- Timestamps are stored in UTC and converted to your timezone setting for display.

## Project layout

```
trading-journal/
  run.bat, run.sh            start the app
  README.md
  data/                      your database (created on first run)
  backend/
    pyproject.toml, uv.lock  dependencies, locked with hashes
    tradejournal/
      finance.py             all money math and statistics (Decimal)
      parser.py              Tradovate Performance CSV -> fill pairs
      grouping.py            fill pairs -> trades
      trades.py              per-trade metrics
      timeutil.py            timezones, trading day, hold time
      symbols.py             NQH4 -> NQ, 2024-03
      db.py                  schema + migrations
      store.py               saving trades, regrouping after imports/undo
      importer.py            import, preview, undo
      queries.py             trade queries and filters for the screens
      accounts.py, settings.py, config.py
      ledger.py              account costs and payouts
      journal.py             tags, notes, screenshots
      cli.py                 command line
      ui/
        app.py               starts the local server
        layout.py, filters.py, widgets.py, charts.py, theme.py, fmt.py
        pages/               dashboard, trade log, import, settings
    tests/
      fixtures/              real export samples
```
