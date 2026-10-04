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
