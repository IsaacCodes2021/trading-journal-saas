# First deploy: from nothing to a website you can sign in to

The shortest path to a working Roundturn on the internet, with who does each step. It uses Render for the database and the web service and the Kinde application you already have. **Screenshots (the Cloudflare bucket), the custom address, the operator tools and the deletion sweep come after**, and the site works without them (screenshots answer "not set up" until the bucket exists).

[deploy.md](deploy.md) is the reference behind this (every setting, the order of a normal release). This file is only the first pass. **It has been run once, for real (2026-10-09)**, from an empty Render account to a signed-in journal whose data was still there after signing out and in again; the notes below record what the screens actually looked like and where the written steps were wrong or unclear.

Run the commands in PowerShell, in the `backend` folder of the repository. Never paste a password, the `.env.*` files, or an address with a password in it into a chat or a commit. `.env.*` files are ignored by git.

## What it costs

About **$27 a month**: the web service's smallest plan (about $7) plus the database's Basic-1gb plan (about $19) and its storage ($0.30 per GB a month; 5 GB is $1.50). Render shows the exact price on screen before you confirm, *check* it there. The database is the part that cannot be free: Render's free database expires, so it cannot hold a journal. The 3-day point-in-time restore window comes with the Hobby workspace.

## 1. You: a Render account and a database

1. Make an account at render.com (your own, with your own payment method) and sign in.
2. **New, then Postgres.** Fill in: Name `roundturn-db`; Database `roundturn`; Region **Oregon (US West)** (the web service uses the same one); PostgreSQL version **18**; the **Basic-1gb** plan; 5 GB of storage. Leave the user as it is. Create it and wait until it says **Available**.
3. On its **Info** page, copy two things into a scratch note (not a chat):
   - the **External Database URL**, and add `?sslmode=require` to its end;
   - the **Hostname** (the internal one, like `dpg-abc123-a`, with no dots).

## 2. You: make the roles and build the tables (one command)

```powershell
cd C:\Users\Isaac\Desktop\Development\trading-journal-saas\backend
$env:TJ_SETUP_URL = Read-Host "Paste the External Database URL (with ?sslmode=require at the end)"
.venv\Scripts\python.exe -m tradejournal.pg.provision_hosted --out .env.hosted --app-host dpg-abc123-a --migrate
Remove-Item env:TJ_SETUP_URL
```

(Use your own internal hostname for `--app-host`.) It creates the six database roles with long random passwords, makes `tj_owner` own the database, builds every table, and writes `backend\.env.hosted`: the settings the web service needs, plus the other credentials commented out. It prints no passwords. It is safe to run again (it keeps the passwords already in the file).

If it says the account cannot create roles, that is the question for Render's support (deploy.md section 1). Keep `.env.hosted` somewhere safe (a password manager's file store): it is the only copy of those passwords.

## 3. You: allow the new address in Kinde

The service will live at `https://roundturn.onrender.com`, unless Render has to add letters to the name because it is taken; you will see the real address in step 4, and can come back here. In the Kinde dashboard, in the same application you used for the spike (Settings, Applications, your application, Details), **add** (do not remove the localhost ones):

| Setting | Value |
|---|---|
| Allowed callback URLs | `https://roundturn.onrender.com/auth/callback` |
| Allowed logout redirect URLs | `https://roundturn.onrender.com/invite` |

## 4. You: make the web service, then paste the settings in one go

1. Render: **New, then Blueprint**. Connect your GitHub account to Render when it asks, choose the repository `IsaacCodes2021/trading-journal-saas`, branch `main`. Render reads `render.yaml` and shows one web service, `roundturn`. Confirm it. *Check: the plan and the price it shows.*
2. Its first deploy will build the image (a few minutes) and then **fail to start** ("refusing to start unconfigured"). That is expected: it has no settings yet.
3. Build the file to paste, from the same `backend` folder (it joins the two files and adds the address):

```powershell
$live = Get-Content .env.hosted | Where-Object { $_ -match '^TJ_' }
$kinde = Get-Content .env.kinde | Where-Object { $_ -match '^KINDE_(DOMAIN|CLIENT_ID|CLIENT_SECRET)=' } | ForEach-Object { "TJ_$_" }
($live + $kinde + "TJ_APP_ORIGIN=https://roundturn.onrender.com") | Set-Content .env.render-paste
notepad .env.render-paste
```

   (`$live` takes only the lines not commented out; `.env.kinde` has the names without the `TJ_` the server wants, so the second line adds it, and leaves out the machine-to-machine ones, which are not for the web service.)
4. In Render, the service's **Environment** page, **Add from .env**: paste the whole file and save. It redeploys by itself. Then delete the paste file: `Remove-Item .env.render-paste`.
5. If the service's real address (top of its page) is not `https://roundturn.onrender.com`, change `TJ_APP_ORIGIN` to the real one (scheme and host only, no slash) and fix the two Kinde addresses from step 3 to match. The address must be exactly right: every write is compared with it.

## 5. Check it is up

Both of these should answer 200 (tell me the address and I can run them for you):

```powershell
curl.exe -s -o NUL -w "%{http_code}\n" https://roundturn.onrender.com/healthz
curl.exe -s https://roundturn.onrender.com/readyz
```

`/readyz` lists its checks; "database: ok" means the web role reached the database and every migration is there. If it says `unreachable`, the address in `TJ_DATABASE_URL` is wrong (it must be the internal hostname, from `--app-host`); if `schema_behind`, run step 2 again with `--migrate`.

## 6. You: invite yourself and sign in

```powershell
Get-Content .env.hosted | ForEach-Object { if ($_ -match '^#?\s*(TJ_[A-Z_]+)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
.venv\Scripts\python.exe -m tradejournal.pg.invites create --label me --email you@example.com
```

(The first command loads the settings into this window, including the owner's address; use your own email.) It prints a code like `TJ-7KQ2-M9XA`, valid for one use. Open `https://roundturn.onrender.com/invite?code=TJ-7KQ2-M9XA`, press **Continue to sign up**, sign in at Kinde, and you land in an empty journal. Then work through the checklist in [try-real-sign-in.md](try-real-sign-in.md), step 5, on the real address: import `backend/tests/fixtures/01-05-2024-performance.csv`, add a note and a tag, download your data, open a second browser, sign out.

Close the window afterwards, or clear the variables, so the owner's address is not left in your environment.

## What the first real run showed (2026-10-09)

- **It worked end to end**: database, the setup command (roles, owner, 13 migrations), the Blueprint, the pasted settings, `/healthz` 200, `/readyz` 200 with the database "ok", an invitation, sign-up at Kinde, an empty journal, data kept across a sign-out and sign-in. The sign-in route sent the browser to Kinde with the callback `https://roundturn.onrender.com/auth/callback`, and the response carried the strict security headers (HSTS, a content policy with no inline script, no framing, `nosniff`).
- **The service's address was the one in the guide** (`https://roundturn.onrender.com`).
- **Render's screens.** The Blueprint form asks for a *Blueprint Name* (any label; leave the path empty) and accepted the plan name `0.5c-512mb` as written. The environment variables are edited through the **Edit** button on the Environment page; The first deploy ended **Failed** after about a minute, as predicted (no settings), and the deploy with the settings went **Live**.
- **Kinde's screens.** The right application is the one of type "Back-end web" (not the machine-to-machine one). The environment is labelled "Production, Not live"; that label does not stop sign-in.
- **A plain request to `/` is a 404 by design.** The built app is served only to requests that ask for a web page (`Accept: text/html`), as a browser does and as CI's image check does. A bare `curl` and Render's own port probe (`HEAD /`, which shows in the log as a 404) therefore see a 404. Use `curl -H "Accept: text/html"` to check the page, and `/healthz` and `/readyz` for health.
- **Not checked in that run:** the cost on Render's screen (the guide's figure is still the estimate), memory under a real import, the second-browser and "sign out everywhere else" checks (not reported), and every step that needs the bucket.

## What is deliberately not here yet

- **Screenshots.** Needs the Cloudflare R2 bucket: [deploy.md](deploy.md) section 2. Add the four `TJ_S3_*` settings and redeploy.
- **The address `app.roundturn.app`.** A custom domain on Render plus a DNS record on Cloudflare; then change `TJ_APP_ORIGIN` and the two Kinde addresses to match, and set `TJ_CLIENT_IP_HEADER=CF-Connecting-IP` once every request really passes through Cloudflare. Until then every visitor looks like Render's proxy to the rate limits, which is harmless for one person and wrong for many.
- **Operator tools** (deploy.md section 7, step 2) and **the deletion sweep** (section 6).
- **A separate Kinde environment for production.** This reuses the spike's application, which is fine for you alone; make a clean one before real users.
- **Releases.** A merge to `main` deploys by itself. A release that adds a migration must be migrated first (`provision_hosted --migrate` is safe to run again, or `python -m tradejournal.pg.migrate` with `TJ_OWNER_URL`); until it is, the new instance stays out of traffic and the old one keeps serving, because `/readyz` stays red.
- **The restore drill** ([restore.md](restore.md)). Do it before anyone else's data is in the database.
