# Roundturn (trading-journal-saas)

A hosted, multi-user trading journal for prop-firm futures traders. It imports Tradovate **Performance** CSV exports,
rebuilds round-trip trades from the fill pairs, and shows statistics, notes, tags and screenshots. It is live at
<https://app.roundturn.app> as an invite-only, free beta; the marketing site is <https://roundturn.app>.

It is a FastAPI JSON API over Postgres and a React single-page app, served from one container. Start with the
[design](docs/design/README.md), the [runbooks](docs/runbooks/) and [The hosted product](#the-hosted-product-roundturn)
below. The domain code that parses exports, groups fills into trades and does the money math (`parser.py`,
`grouping.py`, `trades.py`, `finance.py`, `timeutil.py`) is shared by the API and the command line.

> The earlier local-only NiceGUI app was removed from this repository on 2026-10-10. It is frozen at the git tag
> `v0.1.0-local`. The original local project folder is a separate copy that this repository never modifies.

## Status

Live as an invite-only beta. What is built, what is switched on and what comes next are in the running log of the
[roadmap](docs/design/05-features-and-roadmap.md); the operational state (deploys, the cleanup and backup jobs, how to
restore) is in the [runbooks](docs/runbooks/).

## What it does

- **Import** Tradovate Performance exports with a preview, duplicate handling and undo.
- **Trades:** round trips rebuilt from fill pairs, with net P&L after fees, a filterable log and a trade page.
- **Statistics:** a dashboard with charts and a performance score, a calendar and day view, and reports by symbol,
  weekday, hour, hold time, tag and account.
- **Journal:** notes, tags and screenshots on each trade.
- **Prop accounts:** evaluation and funded accounts, fees, costs and payouts, archiving.
- **Your data is yours:** export everything, and delete your account (with a 7-day grace period).

## Metrics

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

For development (tests, linting; Python 3.12 is the tested version)
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
`backend/uv.lock`. The container image and CI install from the lock
with `uv sync --frozen`, so they get exactly the locked versions. There is no committed
`requirements.txt`: it used to be generated from the lock, and Dependabot edited it instead
of `uv.lock`. After changing dependencies, run `uv lock` in `backend/` and commit both files.
CI exports the lock to run `pip-audit` on the pinned versions for known vulnerabilities.

The fixtures in `backend/tests/fixtures/` are copies of real exports. The main one,
`01-05-2024-performance.csv`, has 14 rows that must collapse into 8 trades.

## The hosted product (Roundturn)

One container serves the JSON API and the built React app on a single origin ([design](docs/design/README.md)).

| Folder | What it is |
|---|---|
| `backend/tradejournal/api/` | The FastAPI app: the tenant API (`/api/v1`), the operator API (`/api/admin/v1`), sign-in routes (`/auth`), sessions, CSRF, rate limits, import limits, structured logs |
| `backend/tradejournal/pg/` | Postgres: migrations `0001` to `0011`, the request role with forced row-level security, the narrow system roles (login, webhook, sweep, admin), the ported domain queries, the command-line tools (`invites`, `plans`, `users`, `sweep`, `migrate`) |
| `backend/tradejournal/auth/` | Sign-in through Kinde (an identity-provider port with a fake for tests), signed state, CSRF tokens |
| `frontend/` | The React app (Vite, React 19, TypeScript, Tailwind, TanStack Router and Query). `pnpm dev` runs it with sample data; see the runbook for the real API |
| `site/` | The marketing site at roundturn.app: static pages that link to the app's beta form and sign-in, on a Cloudflare Worker that stores nothing |
| `docs/design/` | The design (requirements to decisions), the roadmap with a running log of what is built, and the security gates |
| `docs/runbooks/` | Running it locally, trying real sign-in, migrating a local journal, **incident response, rotating secrets and restoring** |
| `docs/legal/` | Drafts of the Terms and Privacy Policy (not published) |

**Run it on your own machine:** [docs/runbooks/run-locally.md](docs/runbooks/run-locally.md) (and
[try-real-sign-in.md](docs/runbooks/try-real-sign-in.md) for the real Kinde flow). The operator tools are described there too.

**Checks** (CI runs all of these on every pull request: *Lint and test*, *Dependency audit*, *Secret scan*,
*Container image builds*, *Front end*, *Marketing site*):

```powershell
cd backend
uv run ruff check . ; uv run ruff format --check .
uv run python -m tradejournal.api.openapi --check     # backend/openapi.json is current (regenerate without --check)
uv run pytest                                          # tests/pg need the local Postgres (see the runbook); without it they skip

cd ../frontend
pnpm gen:api                                           # regenerate the TypeScript types from openapi.json
pnpm format:check ; pnpm typecheck ; pnpm lint ; pnpm test ; pnpm build
pnpm check:bundle ; pnpm check:security                # entry-chunk budget, no sample data loaded up front, security scan
```

The container image holds the API and the built front end (a Node stage builds `frontend/`). Build it from the
repository root; the image defaults to `TJ_ENV=production`, so it refuses to start until the settings below exist:

```powershell
docker build -f backend/Dockerfile -t tradejournal .
docker run --rm -p 8000:8000 -e TJ_ENV=development -e TJ_APP_ORIGIN=http://localhost:8000 tradejournal
```

CI builds it and starts it on every pull request. Settings come from environment variables (`ApiConfig` in
`api/config.py`); development needs none. In production the process writes one scrubbed JSON line per request and
per error to standard output (`api/logs.py`); the server's own access log is off because it would print query strings.

| Variable | Meaning |
|---|---|
| `TJ_ENV` | `development` (default) or `production`, which refuses to start without the settings below |
| `TJ_APP_ORIGIN` | Where browsers reach the app, such as `https://app.example.com`: no path, no trailing slash |
| `TJ_SESSION_SECRET` | At least 32 bytes as hex; `python -c "import secrets; print(secrets.token_hex(32))"` |
| `TJ_WEB_DIST` | The built front end (default `frontend/dist`) |
| `TJ_APP_VERSION` | The build id, sent as `X-App-Version` (CI sets the git sha) |
| `TJ_DATABASE_URL` | The request role (`app_rw`); without it the data routes answer 503 and `/readyz` says not ready |
| `TJ_LOGIN_DATABASE_URL` | The sign-in role (`app_login`): sessions, invites, the audit log |
| `TJ_ADMIN_DATABASE_URL` | The operator role (`app_admin`); without it the operator tools answer 503 |
| `TJ_KINDE_DOMAIN`, `TJ_KINDE_CLIENT_ID`, `TJ_KINDE_CLIENT_SECRET` | Sign-in through Kinde, all or none (a secret: the host's secret store only) |
| `TJ_SIGNUP_MODE` | `invite` (default) or `open` |
| `TJ_CLIENT_IP_HEADER` | The header that carries the client address behind a proxy (`CF-Connecting-IP`); leave it out otherwise |
| `TJ_RATE_LIMITS` | `off` switches the rate limits off (development or an emergency) |
| `TJ_MAX_CONCURRENT_IMPORTS` | How many imports run at once in a process, 1 to 16 (default 2) |
| `TJ_FILES_DIR` | The folder that holds screenshot files (for development, or a host with a persistent disk). Without it **and** without the bucket settings below, the screenshot routes answer 503 and a trade's screenshots have no address |
| `TJ_S3_ENDPOINT`, `TJ_S3_BUCKET`, `TJ_S3_ACCESS_KEY_ID`, `TJ_S3_SECRET_ACCESS_KEY` (`TJ_S3_REGION`, default `auto`) | A private bucket for screenshot files instead of a folder (Cloudflare R2, or anything that speaks the S3 API): all four or none, https in production, **not together with `TJ_FILES_DIR`**. The access key and secret are secrets (the host's secret store only) |

The command-line tools use their own settings: `TJ_OWNER_URL` (the schema owner: migrations, invites, plan codes,
operators; **never on the host**), `TJ_SWEEP_DATABASE_URL`, the machine-to-machine `TJ_KINDE_M2M_CLIENT_ID` /
`TJ_KINDE_M2M_CLIENT_SECRET` and `TJ_FILES_DIR` or the four `TJ_S3_*` settings (the deletion sweep, which removes a deleted person's screenshot folder). [deploy.md](docs/runbooks/deploy.md) lists every setting for a host. [rotate-secrets.md](docs/runbooks/rotate-secrets.md) lists every
secret and how to replace it.

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

## Storage (the command-line journal)

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
trading-journal-saas/
  README.md
  render.yaml                the Render Blueprint: the web service, the cleanup job, the backup job
  kinde.json, package.json   Kinde's custom-code entry points (the code itself is in kinde/)
  kinde/                     the look of the sign-in screens, deployed through Kinde
  site/                      the marketing site (a Cloudflare Worker serving static pages)
  frontend/                  the React app (Vite, React, TypeScript)
  docs/                      design, runbooks, legal drafts, research
  backend/
    pyproject.toml, uv.lock  dependencies, locked with hashes
    openapi.json             the API contract the front end's types come from
    tradejournal/
      api/                   the FastAPI app: routes, schemas, sessions, rate limits, logs
      auth/                  sign-in through Kinde, signed state, CSRF
      pg/                    Postgres: migrations, queries, system roles, command-line tools (sweep, migrate, ...)
      finance.py             all money math and statistics (Decimal)
      parser.py              Tradovate Performance CSV -> fill pairs
      grouping.py            fill pairs -> trades
      trades.py, timeutil.py, symbols.py, models.py
      filestore.py, bucketstore.py, filebackup.py    screenshot files, the private bucket, the daily copy
      db.py, store.py, importer.py, queries.py, accounts.py, journal.py, ledger.py, settings.py, config.py
                             the original SQLite journal modules (the command line and the local-data migration)
      cli.py                 command line over the local SQLite journal
    tests/
      fixtures/              real export samples
```
