# Running the app on your own machine against the local database

**Status (2026-10-04):** the API serves the real read routes from your local Postgres, signed in as one user, and the front end can read them: **the dashboard, the trade log, a trade's page, the filters and the tag choices show your own journal** when the front end is started with `TJ_DEV_API=real` (below). Everything that writes (notes, tags, importing, accounts, fees, the ledger) and the account progress numbers are not connected yet and say so on screen. Without `TJ_DEV_API=real`, `pnpm dev` and the shipped app still show the built-in sample data.

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

The sign-in is a development stand-in: the API's configuration refuses `TJ_DEV_USER_ID` when `TJ_ENV=production`, and a cookie with any other value gets a 401. Real sign-in (Kinde) arrives in M5.

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
