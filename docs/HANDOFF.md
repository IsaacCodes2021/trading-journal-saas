# Roundturn handoff: where the project stands

**As of 2026-10-12.** `origin/main` is at `3db1ab8` (merge of PR 139); its CI is green. Written for a new Claude Code session
(or a person) picking this up cold. Facts come from the code at that commit and from what the owner said in chat through
2026-10-11. Anything marked *owner-reported* was not checked independently.

Read this file first, then `CLAUDE.md` (the rules, short), then the runbook or design doc for the task at hand (index at the end).

## 1. What this is

**Roundturn** (roundturn.app) is a hosted, multi-user trading journal for futures prop-firm traders: import Tradovate
Performance CSV exports, see trades, a dashboard, reports and a calendar, add notes, tags and screenshots, track prop accounts,
fees, payouts and costs. It is a private-beta product, invite-only, free for now.

It was built from the owner's local journal (`C:\Users\Isaac\Desktop\Development\trading-journal`, SQLite and NiceGUI). That
original folder is **never touched**. This repo (`trading-journal-saas`, private GitHub `IsaacCodes2021/trading-journal-saas`)
is the hosted product. The old NiceGUI screens were removed in PR 138 (frozen at git tag `v0.1.0-local`).

Operator: **Northfield Labs, LLC, a California LLC.** Public contact is `hello@roundturn.app` only. The owner does not want a
home or postal address published anywhere.

## 2. What is live

| Piece | Where | Notes |
|---|---|---|
| App (API and React front end, one container) | https://app.roundturn.app, Render web service `roundturn` | Oregon, plan `0.5c-512mb`, deploys every commit to `main`, health check `/readyz` |
| Database | Render Postgres 18 `roundturn-db`, Oregon | forced row-level security, six roles, migrations 0001 to 0018 applied |
| Sign-in | Kinde, custom domain `auth.roundturn.app` | OIDC with PKCE; custom screens come from `kinde/` in this repo |
| Screenshots | Cloudflare R2 bucket `roundturn-screenshots` (private) | read back through the server; no versioning |
| Screenshot copy | R2 bucket `roundturn-screenshots-backup` | daily job, keeps a deleted picture 30 days |
| Email | Resend, sender `Roundturn <hello@roundturn.app>` | beta code and "access ended" only |
| Marketing site | https://roundturn.app, Cloudflare Worker with static assets, built from `site/` | collects nothing; links to the app |
| Hourly sweep | Render cron job `roundturn-sweep` | `python -m tradejournal.pg.sweep all --apply` |
| Daily backup | Render cron job `roundturn-backup`, 03:30 UTC | `python -m tradejournal.filebackup --apply` |

Pictures of all this, checked against the code: `.archify/roundturn-moving-parts.html` (local only, excluded from git; each
folder's `candidate.json` regenerates its diagram with the Archify skill).

**The owner started journaling in the hosted app on 2026-10-12.** Their real local journal (9 accounts, 45 trades, 13 tags,
7 ledger entries, 35 screenshots) was migrated on 2026-10-10 with `pg/migrate_local.py`, all 29 golden checks passing, and the
owner confirmed tags, photos and notes appear.

## 3. Rules for any session (the owner's standing instructions)

1. **Never add or change files in `C:\Users\Isaac\Desktop\Development\trading-journal`.** Work in this repo.
2. **Never print or paste a secret** (keys, tokens, connection strings, session secrets). `.env.*` files are git-ignored; a
   temporary file that holds a secret is deleted as soon as it has done its job.
3. **Merge a pull request only when the owner says so** ("merge it", "merge 140 when green"). Use a merge commit
   (`gh pr merge N --merge`). Merging to `main` **deploys to production** (Render and the Cloudflare site).
4. **No production change without an explicit yes:** migrations, Render or Kinde or Cloudflare settings, running a job with
   `--apply`, any write to the production database. Read-only checks (`/healthz`, `/readyz`, `gh` reads) are fine.
5. **Do not poll CI.** Read it once per message (`gh pr checks N`). The six real checks are *Lint and test*, *Dependency audit*,
   *Secret scan*, *Container image builds*, *Front end* and *Marketing site*. The red "Workers Builds: roundturn" is a known
   Cloudflare false alarm: ignore it.
6. **Ask first** before downloads or installs, messages to outside parties, creating accounts, or spending money.
7. **Write replies for a non-specialist.** Keep abbreviations but put the meaning in parentheses on every use (for example
   "RLS (row-level security)").
8. **When giving Render steps, say which service by type:** the *Cron Job* (has a Trigger Run button) or the *Web Service*
   (shows `app.roundturn.app`). The owner once entered cron settings on the web service by mistake.
9. **Never run the sweep with `--apply` against the local development database**; it holds a copy of a real journal.
10. **Run the full backend suite before pushing backend changes.** Two over-broad test assertions broke CI once because only the
    new files had been run.

## 4. How to work here

**Machine:** Windows 10, Git Bash and PowerShell 5.1. Python 3.12 is pinned (`backend/.python-version`); the venv is
`backend/.venv`. Node 24.21 and pnpm 12.8.1. Many files use CRLF line endings: edit with a script that preserves them, or the
diff becomes the whole file. Write commit messages to a file and use `git commit -F`; write PR bodies to a file and use
`gh pr create --body-file`. Commits use the repo's GitHub no-reply identity and end with the Co-Authored-By trailer.

**Backend** (from `backend/`):

```
.venv\Scripts\python.exe -m pytest -q               # the full suite; tests/pg need the local Postgres, else they skip
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

**Local Postgres** (needed for `tests/pg`): portable Postgres 18 in `C:\Users\Isaac\Tools\postgresql`, port **54318**. Start it
as a background task: `C:/Users/Isaac/Tools/postgresql/extract/pgsql/bin/postgres.exe -D C:/Users/Isaac/Tools/postgresql/data`.
If an earlier one died, kill leftover `postgres.exe` processes first; crash recovery takes 15 to 55 seconds. It is **stopped**
right now. The tests read the git-ignored `backend/.env.test`; the superuser password sits in
`C:\Users\Isaac\Tools\postgresql\dev-superuser.pw` (outside the repo; load it into `PGPASSWORD`, never print it).

**Front end** (from `frontend/`, use PowerShell and put `%APPDATA%\npm` and Node on PATH): `pnpm lint`, `pnpm format:check`,
`pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm check:bundle`. After an API change: `python -m tradejournal.api.openapi`
then `pnpm gen:api` (CI fails on drift). **Site** (from `site/`): `node --test "test/*.test.mjs"`; the tests pin wording on the
privacy page, so a wording change needs the test changed with it.

**Run the app locally:** `docs/runbooks/run-locally.md`. **Do not commit drafts into `site/`**: the site deploys whatever is merged.

**Secrets scanning:** gitleaks scans the whole branch history. Never commit anything that looks like an AWS key or a
high-entropy test value; build such values from short pieces at run time.

**Habits that paid off:** a change that touches rules or money gets a two-tenant test and a mutation check (break the code on
purpose, confirm a test fails); migrations are additive and run by hand (section 5); mutation runs must not be interrupted by
reading source (you would be reading the mutant).

## 5. Operating the production system

**Deploy path:** a merge to `main` deploys the web service on its own. The new image stays **out of traffic until the database
has its migrations** (the health check is `/readyz`, which compares the schema version). Migrations are never run by the app:
the owner or a session runs `python -m tradejournal.pg.migrate` as the schema owner (`TJ_OWNER_URL`, from the git-ignored
`backend/.env.hosted`, using the *external* database host from a PC). The next migration number is **0019**.

**Which settings live where** (names only; values are in Render's Environment pages and the owner's `.env.hosted`):

- *Web service:* `TJ_APP_ORIGIN`, `TJ_SESSION_SECRET`, `TJ_DATABASE_URL`, `TJ_LOGIN_DATABASE_URL`, `TJ_ADMIN_DATABASE_URL`,
  `TJ_KINDE_DOMAIN` = `https://auth.roundturn.app`, `TJ_KINDE_CLIENT_ID`/`_SECRET`, the four `TJ_S3_*`,
  `TJ_EMAIL_API_KEY`/`TJ_EMAIL_FROM`, `TJ_CLIENT_IP_HEADER` = `CF-Connecting-IP`, `TJ_SIGNUP_MODE` = `invite` (from
  `render.yaml`).
- *Cron job `roundturn-sweep`:* `TJ_SWEEP_DATABASE_URL`, `TJ_KINDE_DOMAIN` = the Kinde **tenant** address
  (`https://<tenant>.kinde.com`, **not** the custom domain: Kinde refuses its management token there),
  `TJ_KINDE_M2M_CLIENT_ID`/`_SECRET`, the four `TJ_S3_*`, `TJ_EMAIL_API_KEY`/`TJ_EMAIL_FROM`.
- *Cron job `roundturn-backup`:* the four `TJ_S3_*` for the source (a token that can only **read**) and the four
  `TJ_BACKUP_S3_*` for the copy.
- The cron jobs are Blueprint-managed (`render.yaml` holds their schedule and command), but their settings are entered by hand
  on each job's Environment page; they are not in the file.

**Cloudflare R2 tokens:** the web service's (read and write, primary bucket); `roundturn-backup-write` (backup bucket only);
`roundturn-backup-read-source` (Object Read only on the primary; **a restore needs it set to read and write for the
duration, then back**). `roundturn-migrate-temp` was made for the migration and must be deleted (open item below). An old
broad "roundturn build token" from 2026-10-03 has an unknown purpose; the owner may revoke it.

**Identity and retention facts** (pinned to the privacy page by `backend/tests/test_privacy_page_matches_code.py`):
sessions end after 14 idle days or 90 in all; a deleted account is removed 7 days after the request (signing in again
cancels); a beta code works 24 hours and a sign-up with no account is deleted after 24 hours; beta access is 30 days, then
read-only with export; a deleted screenshot stays in the copy bucket for 30 days; Render keeps database recovery copies up to
7 days; the audit log outlives a deleted account with its user id emptied.

**Runbooks** (`docs/runbooks/`): `first-deploy.md` (also DNS and the sweep settings), `deploy.md`, `restore.md`,
`rotate-secrets.md`, `incident.md`, `kinde-branding.md`, `beta.md`, `support.md`, `migrate-local-data.md`,
`try-real-sign-in.md`, `run-locally.md`, `launch-checklist.md`.

**Owner tools (run by hand, never scheduled):** `python -m tradejournal.pg.migrate`, `pg.provision_hosted`, `pg.migrate_local`
(dry run first), `pg.clear_journal` (empties one person's journal; refuses while screenshots exist), `pg.users make-operator`,
`pg.plans`, `pg.invites`, `filebackup restore (--prefix u/<id>/ | --all) [--apply]`.

## 6. Open items

**Waiting on the owner**

| Item | Detail |
|---|---|
| Delete the Cloudflare token `roundturn-migrate-temp` | It can still write to the screenshot bucket. Delete it, do not roll it. |
| Close the old local app | The original app in the `trading-journal` folder was found running on 2026-10-10 and touched `journal.db` at start-up. Close it and journal only in the hosted app. |
| Check the data export | Settings, Data and privacy, download; the manifest should list no missing screenshot files. |
| Switch off Kinde's "No account? Create one" | Details, Authentication experience; leave "own sign-up/sign-in screens" off. A valid code was already verified to open the registration screen. |
| Real account-deletion proof | A throwaway plus-address was scheduled for deletion on **2026-10-18**. Afterwards: the address must be gone from Kinde's Users list and unable to sign in. Board card "Kinde: machine-to-machine app" stays In progress until then. |
| Professional review, and a Terms page | A lawyer's read of the privacy page and the Terms. The Terms are drafts in `docs/legal/`, not published, yet the sign-in and sign-up frame (`frontend/src/features/auth/AuthFrame.tsx`) says "By continuing you agree to the Terms" and links to `roundturn.app/terms/`, which returns 404 today. Either publish Terms or change that sentence. |
| Decide Free versus Pro | `LIMITS` in `backend/tradejournal/entitlements.py` is empty on purpose. |

**Built or planned, not proven**

| Item | Detail |
|---|---|
| **Screenshot restore drill** (URGENT on the board) | `filebackup restore --apply` has **never run on the real buckets**. The one attempt was a dry run, then the test picture was deleted by hand. Redo with a clearly visible picture and capture the `--apply` line; the read token must be temporarily read and write. Until it works, the backup is unproven. |
| Database restore drill | `docs/runbooks/restore.md`; not rehearsed. |
| First scheduled sweep and backup runs | The manual sweep run succeeded (owner-reported) and the backup copied files on 2026-10-10. Look at Render's run history for the hourly and daily runs after that; nobody has checked them independently. |
| Revoke "access ended" email | Owner-reported as tested (2026-10-10). |

**Product work not started**

- **Accounts tab with prop-firm rules, compliance and payout readiness.** A ready brief exists (section 7).
- **Tradovate sync** (the stated main goal). Research (`docs/research/tradovate-api-access.md`) recommends building nothing now:
  OAuth needs an NinjaTrader Ecosystem application and the personal-key route takes the trader's password, which breaks
  principle P1 (the app never holds a broker password). Send the six questions in that note; make CSV import faster.
- **Delete a prop account inside the app** (today only archive; `clear_journal` is the owner's workaround). A product decision.
- Terms acceptance (F-A8), orphan screenshot report, Sentry (needs an account and a dependency), staging environment, billing
  (M9; payment-provider pre-clearance drafts are in `docs/design/payments-preclearance-drafts.md`, unsent).

**Housekeeping:** the Notion board has stale cards (S3, S4). The checkout on this PC sits on the merged branch
`feat/clear-journal`: `git switch main && git pull` before starting new work, and branch from `origin/main`.

## 7. Found in the working tree, not committed (not from the session that wrote this file)

- **`docs/prop-rules/`** (files dated 2026-10-12): `accounts-tab-prompt.md` is a complete feature brief for the Accounts tab
  (rule templates and per-account rules tables, a rule picker, a pure compliance and payout engine, a plan file at
  `docs/prop-rules/PLAN.md` before any code); `Futures prop firm account rules.md` is the research and the definition of every
  rule field; `prop_firm_rules_v2.csv` is the seed data (371 rows, 48 columns, with `flagged_fields`/`flag_reasons`);
  `prop_firm_rules.csv` is the earlier version. No branch or code exists for it yet. Treat the CSV as data, never fill blanks.
- **`marketing/motion-ads/`**: eight short clips and a 20-second reel (mp4, about 10 MB together), dated 2026-10-11 and 12.
  Decide whether they belong in git before committing (size, and whether the repo should hold marketing assets).

## 8. Where things are

`docs/design/`: `README.md`, `01-requirements`, `02-architecture`, `03-data-and-accounts` (tenancy, roles, sessions),
`04-security` (threats and launch gates), `05-features-and-roadmap` (**the dated slice log, newest at the end**),
`06-decisions` (ADRs), `07-frontend-and-api`, `08-spike-prep-research`. `docs/legal/` holds the unpublished drafts. The privacy
page that is live is `site/public/privacy/index.html`.

**Code:** `backend/tradejournal/api/` (FastAPI app, routers, guards), `pg/` (Postgres code, migrations, sweep, owner tools),
`auth/` (sign-in flow, Kinde adapters), root modules `parser.py`, `grouping.py`, `finance.py`, `timeutil.py`
(pure domain code, shared with the old local app), `bucketstore.py`, `filebackup.py`; `frontend/src/` (React, TanStack Router,
generated API types in `lib/api/schema.d.ts`); `site/` (marketing Worker); `kinde/` (Kinde's sign-in page code).
`db.py`, `cli.py` and the SQLite halves of `importer.py`, `store.py`, `settings.py` are the old local journal; the hosted app
reuses only their report shapes and defaults.

**Board:** Notion, "Roundturn" progress board (main page `3ee7f834-7fa5-81dd-a3c0-c23e80edb05f`, Tasks data source
`collection://85c70d1d-5d62-4e3b-b031-63c740de4d40`). Tasks carry Status, Needs you, Milestone and Link; new tasks need a
Milestone to count in the progress bar. Keep it current: when work finishes, mark the card Done.

**Decisions that are easy to forget:** product name Roundturn, domain roundturn.app; Kinde for sign-in (ADR-003); Resend for
email; Render hosting; R2 for files; money is an exact decimal and crosses the API as a string; invite-only beta with a personal
24-hour code by email; ended access means read-only with export; the operator's tools never show a trader's notes, trades or
pictures (a contract test enforces it).
