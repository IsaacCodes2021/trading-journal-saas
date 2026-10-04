# Running the app on your own machine against the local database

**Status (2026-10-04):** the API serves the real read routes from your local Postgres, signed in as one user, and the front end can read them: **the dashboard, the trade log, a trade's page, the filters and the tag choices show your own journal** when the front end is started with `TJ_DEV_API=real` (below). **Notes, tags, accounts, rules, fees, the ledger, the account progress and the business totals work too, and they write to the database**, so run against a throwaway user if you do not want your own journal changed (`python -m tradejournal.api.devserver --user-id <id>`). **Importing a Performance CSV works too** (preview, confirm, history, undo). Only screenshots are not connected yet and say so on screen. Without `TJ_DEV_API=real`, `pnpm dev` shows the built-in sample data (a build never does: it always reads the API, and the bundle check fails if the sample data is in it).

Nothing here touches the original single-user app or its `journal.db`. Nothing is sent anywhere: both servers listen on this machine only.

## 1. The database

1. Start the local Postgres server (it is stopped between sessions): ask the assistant, or run `postgres.exe -D C:\Users\Isaac\Tools\postgresql\data` from `C:\Users\Isaac\Tools\postgresql\extract\pgsql\bin` in a terminal you leave open.
2. The development database `tj_dev` should hold a user with a journal. Today it holds the **rehearsal copy** of your real journal (made when the migration was rehearsed, see `migrate-local-data.md`). Check with the assistant if unsure; a fresh machine needs `python -m tradejournal.pg.provision` and the migration first.

## 2. The API, signed in as that user

```bash
cd backend
.venv\Scripts\python.exe -m tradejournal.api.devserver
```

- It reads the two database URLs from the gitignored `backend/.env.test` (`TJ_DEV_APP_URL`, `TJ_DEV_LOGIN_URL`), so no password is typed or shown.
- It signs in as **the one user in the database**; with several, add `--user-id <id>`.
- It listens on `127.0.0.1:8000` and expects the browser at `http://localhost:5173` (the front end's dev server). Writes are checked against that origin, exactly as in production; use `--origin` if you serve the front end somewhere else.

The sign-in is a development stand-in: the API's configuration refuses `TJ_DEV_USER_ID` when `TJ_ENV=production`, and a cookie with any other value gets a 401.

### Real sign-in (Kinde)

The real flow is in the API (`/auth/login`, `/auth/callback`, `POST /auth/logout`) and is switched on by three settings, all or none:

| Setting | What it is |
|---|---|
| `TJ_KINDE_DOMAIN` | `https://<your-tenant>.kinde.com`, or the custom domain; https, no path |
| `TJ_KINDE_CLIENT_ID` | the Kinde application's client id |
| `TJ_KINDE_CLIENT_SECRET` | its client secret (a secret: host secret store only, never the repository, never a chat) |
| `TJ_SIGNUP_MODE` | `invite` (the default: a new person needs a code) or `open` |
| `TJ_CLIENT_IP_HEADER` | optional: the header that carries the real client address when every request comes through a proxy that sets it (`CF-Connecting-IP` behind Cloudflare). Leave it out otherwise: a header nobody controls would let a caller choose their own rate-limit bucket |
| `TJ_RATE_LIMITS` | `off` switches the rate limits off (development or an emergency); anything else leaves them on |

(To try this flow on your own machine with your existing Kinde tenant, follow `try-real-sign-in.md`: it has the launcher options and a checklist.)

In the Kinde application, set the **allowed callback URL** to exactly `<TJ_APP_ORIGIN>/auth/callback` and the **allowed logout redirect URL** to `<TJ_APP_ORIGIN>/invite`. With the settings unset the two sign-in routes answer 503 and nobody can sign in. Make an invitation with the schema owner's address in `TJ_OWNER_URL`:

```powershell
cd backend
.venv\Scripts\python.exe -m tradejournal.pg.invites create --label "Sam" --email sam@example.com
```

It prints the code once; only a hash is stored. Tests never contact Kinde: they use a fake provider (`backend/tests/fake_idp.py`).

### Handing out plan codes

A plan code gives a person a plan for some days when they type it on Settings > Plan & billing, or when they sign up with an invite that carries one. Making codes needs the schema owner's connection (`TJ_OWNER_URL`):

```powershell
cd backend
.venv\Scripts\python.exe -m tradejournal.pg.plans create-grant --label "Beta testers" --days 90 --max-redemptions 25 --expires-days 60
.venv\Scripts\python.exe -m tradejournal.pg.plans list-grants
.venv\Scripts\python.exe -m tradejournal.pg.plans revoke-grant --id 1
.venv\Scripts\python.exe -m tradejournal.pg.invites create --label "Sam" --grant-id 1
```

The code (`GR-XXXX-XXXX`) is printed once and only its hash is kept, so write it down then; `list-grants` shows each code's label, days, how many people used it and whether it still works, never the code. Revoking stops a code being used; people who already used it keep what they were given. A person can use one code once, and can use another only when the plan from the first has ended. What Free and Pro include is not decided yet, so a plan code changes the label and the "complimentary until" date and unlocks nothing yet.

### Deleting accounts (the sweep)

A person who asks to delete their account (Settings > Data & privacy) is marked for deletion a week away and signed out everywhere; signing in again before then cancels it. **Nothing is deleted until the sweep runs**, so it must be a scheduled job on the host (hourly is plenty):

```powershell
cd backend
.venv\Scripts\python.exe -m tradejournal.pg.sweep deletions            # a dry run: counts only, nothing is touched
.venv\Scripts\python.exe -m tradejournal.pg.sweep deletions --apply    # delete what is due
```

| Setting | What it is |
|---|---|
| `TJ_SWEEP_DATABASE_URL` | the database address for the `sweep` role (it can see an account's identity id and deletion date, delete what is due, and write the audit log; nothing else about a person). In production it is the `app_sweep` role created with the others (doc 03 section 1); on this machine its address is the `TJ_DEV_SWEEP_URL` line of `backend/.env.test` (copy it into this setting) |
| `TJ_KINDE_DOMAIN`, `TJ_KINDE_M2M_CLIENT_ID`, `TJ_KINDE_M2M_CLIENT_SECRET` | the machine-to-machine client the sweep uses to remove the person from Kinde too (the secret: host secret store only, never the repository, never a chat). Without them `--apply` refuses, because an account deleted here and left at Kinde would still be someone's personal data; `--without-identity-provider` overrides that on purpose, for local development only |

**In Kinde (once, by you):** create a *machine-to-machine* application for the sweep (not the sign-in application), authorize it for the **Kinde Management API**, and allow it to read and delete users. Put its client id and secret in the host's secret store as the two `TJ_KINDE_M2M_*` settings above.

What the sweep prints is counts only: due, deleted, skipped (signed in a moment ago, so cancelled), identities removed, identities still pending. An identity that Kinde did not confirm stays in the table `identity_removals` (with the number of attempts and a short reason) and the next run tries it again; check that the "still pending" count goes back to 0.

**Not covered yet:** the screenshot files (M6) and the subscription (M9) are not part of the sweep until those features exist.

**To try the whole thing on this machine without touching anyone real:** make a throwaway user, sign in as it, ask to delete, then move its date into the past as the database owner and run the sweep with `--apply --without-identity-provider`. **Never run `--apply` against the local database while it holds the copy of your real journal** unless that user is not due: the sweep deletes whoever is due.

## 3. The front end

```powershell
$env:Path = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:Path"
cd frontend
$env:TJ_DEV_API = "real"
pnpm dev
```

Open `http://localhost:5173`. Trades in an **archived** account (passed, failed or closed) are left out until you tick **Show archived** in the filter bar, as in the old app. With `TJ_DEV_API=real` the dev server stops answering the session itself and forwards `/api` to the API above, sending the development session cookie on the browser's behalf. Without it, `pnpm dev` runs entirely on built-in sample data with no backend (the default).

## Stopping

Close the two terminals. Stop Postgres last (`pg_ctl.exe stop -D C:\Users\Isaac\Tools\postgresql\data`).

## Trying the API directly

With the API running, these need the cookie and answer JSON:

```bash
curl -H "Cookie: __Host-tj_session=dev-session" "http://127.0.0.1:8000/api/v1/dashboard?preset=all"
curl -H "Cookie: __Host-tj_session=dev-session" "http://127.0.0.1:8000/api/v1/trades?limit=5&sort=-net_pnl"
```

Money is a string (`"-475.00"`); times end in `Z` (UTC). Without the cookie every route answers 401.
