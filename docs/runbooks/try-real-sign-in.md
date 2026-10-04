# Try the real sign-in on your own machine

Roundturn's sign-in (OIDC, "OpenID Connect", through Kinde) is built and tested against a fake provider. This runbook is the first run against **your real Kinde tenant**, entirely on your computer: nothing is deployed, the database is the local development one, and the servers listen on this machine only.

It takes about ten minutes. You do one thing in the Kinde dashboard, start two servers, and sign up with an invite code like a beta user would.

## What you need

- The local Postgres server running (see `run-locally.md`, section 1).
- `backend/.env.kinde` (already there from the S1b spike): the Kinde domain, client id and client secret. **Never paste these into a chat or a commit.** The launcher reads the file itself and only ever prints the names of what is missing.
- A browser that accepts "Secure" cookies on `http://localhost`: **Edge or Chrome**. (Firefox may refuse them.)

## 1. In Kinde: allow this machine's addresses

Open your Kinde dashboard, then the application used for the spike (Settings, Applications, your application, Details; the labels may differ slightly). Add:

| Setting | Value |
|---|---|
| Allowed callback URLs | `http://localhost:5173/auth/callback` |
| Allowed logout redirect URLs | `http://localhost:5173/invite` |

Leave the rest alone. The old spike address (`http://localhost:8000/api/v1/auth/callback`) can stay or go. If a value is wrong, Kinde stops at its own error page ("redirect URI mismatch") before our code runs.

## 2. Start the API with the real sign-in

```powershell
cd backend
.venv\Scripts\python.exe -m tradejournal.api.devserver --real-sign-in --migrate
```

It prints the two addresses from step 1 so you can compare them. It uses the real session store, so you may be signed in on several browsers at once and signing out ends only that one.

## 3. Make yourself an invite

While the beta is by invitation, a new person needs a code. In a second terminal:

```powershell
cd backend
.venv\Scripts\python.exe -m tradejournal.api.devserver --make-invite
```

It prints one code like `TJ-7KQ2-M9XA`: single use, valid for seven days. (Add `--invite-email you@example.com` to make it work for that address only.) The code is shown once; only a hash is kept.

## 4. Start the front end in sign-in mode

```powershell
$env:Path = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:Path"
cd frontend
$env:TJ_DEV_API = "signin"
pnpm dev
```

`signin` is different from `real` in `run-locally.md`: it does not add the fixed development cookie, so the browser keeps the real session Kinde's sign-in gives it.

## 5. Sign up like a beta user

Open `http://localhost:5173/invite?code=TJ-7KQ2-M9XA` (your code), press **Continue to sign up**, and sign in at Kinde with the email and factor you used in the spike. You should come back to Roundturn signed in, with an empty journal.

Then check:

- [ ] The account menu (top right) shows your email.
- [ ] **Settings > Security** lists this browser ("Edge on Windows" or similar) marked **This device**, with "Two-step verification" if Kinde asked for a code.
- [ ] **Settings > Data & privacy > Download my data** gives you a ZIP (almost empty, since the journal is empty).
- [ ] Open a second browser (or a private window), go to `http://localhost:5173`, and sign in: **Security** now lists both. Use **Sign out everywhere else** in the first one; the second is refused on its next click.
- [ ] **Sign out** in the account menu: you are taken through Kinde's logout and land on Roundturn's invite page (the public page). Opening `http://localhost:5173` again sends you to Kinde to sign in, and a returning person needs no invite.

To see something in the journal, import `backend/tests/fixtures/01-05-2024-performance.csv` from the Import screen (or one of your own Tradovate exports).

## If something looks wrong

| You see | Likely cause |
|---|---|
| `{"detail":"Sign-in is not set up on this server yet."}` | The API was not started with `--real-sign-in` |
| Kinde's own "redirect URI mismatch" page | A value in step 1 is missing or has a typo (including a trailing slash) |
| The invite page with "That invite code did not work" | The code is wrong, used, expired, or tied to another email |
| The invite page with "We could not sign you in" | Something failed after Kinde (token, nonce, key). Tell me the time; the exact reason is recorded in the audit log (`login_failed`, `reason`) and I can read it |
| "Your email address is not verified yet" | Kinde reports the address as unverified; confirm it in Kinde's email |
| Signed in, then asked to sign in again at once | The browser refused the `Secure` cookie on `http://localhost`: use Edge or Chrome |

## What this leaves behind

- A user row in your **local development database** (and its sessions and audit rows). Nothing in Kinde changes, and nothing leaves your machine.
- Stop both servers with Ctrl+C. The invite code and the user can stay; they are only in the local database.

## What this does not prove

It proves the flow with your real tenant, your real factor and a real browser. It does not prove production behaviour (HTTPS cookies on the real domain, the host's headers), custom-domain email, rate limits, or sign-in with Google or a password (try those in Kinde's own sign-in page if they are enabled). The step-up for operators (M10) is not built yet.
