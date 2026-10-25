# Putting Roundturn on a host

The first deploy, in order, with every setting the server reads and what to check afterwards. **Sections 1, 3 and 4 were followed for real on 2026-10-09** (the first deploy on Render, see [first-deploy.md](first-deploy.md), "What the first real run showed"); sections 2 (the bucket), 5 (Cloudflare), 6 (the sweep), 7 steps 2 to 6 and 8 have not been run. It is written from the code (`api/config.py`, `Dockerfile`, the `pg/` command line tools) and the design (docs 02, 03 and 04), and every step that depends on a provider's screens is marked *check*.

**Doing it for the first time? Follow [first-deploy.md](first-deploy.md)**: the same steps in order, with who does each, and the one command (`python -m tradejournal.pg.provision_hosted`) that makes the roles and builds the tables.

Related: [restore.md](restore.md), [rotate-secrets.md](rotate-secrets.md), [incident.md](incident.md), [run-locally.md](run-locally.md) (the same settings, on your own machine), design doc 02 section 7 (release steps) and doc 03 section 1 (the database roles).

## What runs where

| Piece | What it is | Notes |
|---|---|---|
| **Web service** | One container built from `backend/Dockerfile` (the API and the built front end, one origin) | Listens on `$PORT`; refuses to start unconfigured (`TJ_ENV=production` is the image's default) |
| **Postgres 18** | The only place people's data lives | Roles are made by hand once; migrations are a release step, never run by the app |
| **Object storage** | A private Cloudflare R2 bucket for screenshots | The server reads and writes it; pictures are never linked to it directly |
| **Sweep job** | `python -m tradejournal.pg.sweep deletions --apply`, daily; **`all --apply` every hour once the email settings are on the job** (section 6), which also sends the ended-access email and removes beta sign-ups with no account after 24 hours | Finishes account deletions (rows, picture files, the identity at Kinde) and, with `all`, sends the "your access has ended" email. Needs the bucket, so it works as a separate scheduled process |
| **Kinde** | Sign-in (OIDC) and, for the sweep, its management API | Two applications: the web app, and a machine-to-machine one |
| **Cloudflare** | In front of the host: DNS, TLS, caching of `/assets/*`, a rate-limit rule | `roundturn.app` already points a Worker at the marketing site; the app needs its own host name |

The marketing site (`site/`) is a separate Cloudflare Worker and has nothing to do with this file.

## 1. The database

1. **Create a database** on the managed Postgres 18 and keep its administrator's connection string to hand (it is used for this step only).
2. **Create the six roles**: `python -m tradejournal.pg.provision_hosted` does steps 2 to 4 in one go (it needs the host's administrator address in `TJ_SETUP_URL`, and `--migrate` runs step 4); [first-deploy.md](first-deploy.md) section 2 has the command. By hand, each `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE` with its own long random password: `tj_owner` (owns the schema, runs migrations), `app_rw` (serves requests), and the four system roles `app_login`, `app_webhook`, `app_sweep`, `app_admin`. `backend/tradejournal/pg/provision.py` shows the exact statements; it is for development and CI, but the same statements can be pasted into the provider's SQL console. *Check: whether the provider's administrator may create roles at all. If not, this is the question to put to their support, and it can change the choice of host (doc 02).*
3. **Make `tj_owner` own the database and its `public` schema**, so the migrations can create everything and nothing else can.
4. **Run the migrations** as `tj_owner`, once, before the first start and again before every release that adds one:

```bash
docker run --rm -e TJ_OWNER_URL="postgresql://tj_owner:...@HOST:5432/DBNAME" <image> python -m tradejournal.pg.migrate
```

The runner takes an advisory lock, so two at once take turns, and refuses a shipped migration whose file has changed. The server's `/readyz` stays red until the database has every migration this image ships with, so a deploy that forgot this step never takes traffic.

5. **Write down the five connection strings** (one per role) in the host's secret store. They are the same database with different users.

## 2. The bucket (screenshots)

1. In Cloudflare, create an R2 bucket. **Private**: no public access and no custom domain. Any name of 3 to 63 lower-case letters, digits and dashes will do.
2. Create an API token for **that bucket only**, with object read and write. You get an access key id and a secret; the secret is shown once.
3. The endpoint is `https://<your account id>.r2.cloudflarestorage.com` (the account id is on the R2 page); the region is `auto`.
4. **There is no versioning to turn on.** R2 does not offer it (Cloudflare's S3 compatibility page lists `PutBucketVersioning` and `GetBucketVersioning` as not implemented (checked 2026-10-09)), so there are no old versions to restore from, and the earlier plan that relied on them is withdrawn. What protects a picture today: the server writes each one once, under a name it makes itself, and never overwrites it, so the only way to lose one is a deletion (a person deleting a picture, or an account being deleted). What does **not** exist yet is a way to put one back after a bad deletion, a bug or a leaked token: that needs a second copy of the bucket (a scheduled job that copies objects the second bucket does not have yet, ideally to a different provider, which is cheap because objects never change). **Built, not in use** (`python -m tradejournal.filebackup`, 2026-10-09): copies the keys the second bucket lacks and, after 30 days (`--keep-days`), forgets what has been gone from the source that long, so a bad deletion can be undone within a month and a deleted account's pictures do not stay in the copy forever (the privacy wording has to say backups keep deleted pictures for up to that long). It refuses to forget anything when the source lists nothing while the copy holds files, never forgets more than `--max-purge` (200) in a run, refuses when the source and the copy are the same bucket, and prints only counts. To use it: make a second bucket (a different provider is better than a second one at Cloudflare, which would share the account; a second R2 bucket still covers a bad deletion, a bug or a leaked token), give it its own token limited to that bucket (Object Read & Write), and make a second token for the source bucket that can only read it (Object Read only). The job `roundturn-backup` in `render.yaml` runs daily at 03:30 UTC with `--apply` (switched on 2026-10-10 after a dry run). Add its eight settings in its Environment page: `TJ_S3_ENDPOINT`, `TJ_S3_BUCKET`, `TJ_S3_ACCESS_KEY_ID`, `TJ_S3_SECRET_ACCESS_KEY` (the source, with the read-only token) and `TJ_BACKUP_S3_ENDPOINT`, `TJ_BACKUP_S3_BUCKET`, `TJ_BACKUP_S3_ACCESS_KEY_ID`, `TJ_BACKUP_S3_SECRET_ACCESS_KEY` (the copy; add `TJ_BACKUP_S3_REGION` only if the provider needs a real one). Before switching it on, run it without `--apply` (**Trigger Run**) and check that the counts are what you expect. Then do one restore: copy a picture back and see it ([restore.md](restore.md)). The privacy page and the screenshots row in restore.md say that backups hold deleted pictures for up to 30 days.

The server needs the four settings `TJ_S3_ENDPOINT`, `TJ_S3_BUCKET`, `TJ_S3_ACCESS_KEY_ID`, `TJ_S3_SECRET_ACCESS_KEY` (and optionally `TJ_S3_REGION`, default `auto`), all together or none. The sweep job needs the same four. **Do not also set `TJ_FILES_DIR`**: a server refuses to start with both. A folder (`TJ_FILES_DIR`) is for development; on a host where the job cannot share the web server's disk it would leave deleted people's files behind.

## 3. Kinde

Details are in [try-real-sign-in.md](try-real-sign-in.md) and [run-locally.md](run-locally.md); for production:

- **The web application:** allowed callback URL exactly `https://<the app's host>/auth/callback`, allowed logout redirect exactly `https://<the app's host>/invite`.
- **A machine-to-machine application** that can read and delete users, for the sweep (`TJ_KINDE_M2M_CLIENT_ID`, `TJ_KINDE_M2M_CLIENT_SECRET`).
- **Your own account:** turn on two-step verification before making yourself an operator.

## 4. The web service

Build from the repository root: `docker build -f backend/Dockerfile -t roundturn .` (the host's "Docker" service type with that Dockerfile path and the repository root as context does the same).

| Setting | Value | Secret? |
|---|---|---|
| `TJ_ENV` | `production` (the image's default; leave it) | no |
| `TJ_APP_ORIGIN` | `https://<the app's host>`: scheme and host only, no slash, no path. Writes are compared with it byte for byte | no |
| `TJ_SESSION_SECRET` | 64 hexadecimal characters: `python -c "import secrets; print(secrets.token_hex(32))"`. Fixed across deploys, or every open tab loses its token | **yes** |
| `TJ_DATABASE_URL` | the `app_rw` connection string | **yes** |
| `TJ_LOGIN_DATABASE_URL` | the `app_login` connection string | **yes** |
| `TJ_ADMIN_DATABASE_URL` | the `app_admin` connection string; leave out until you have made yourself an operator | **yes** |
| `TJ_KINDE_DOMAIN`, `TJ_KINDE_CLIENT_ID`, `TJ_KINDE_CLIENT_SECRET` | from the Kinde web application (all three, or sign-in answers 503) | the last one |
| `TJ_SIGNUP_MODE` | `invite` for a private beta (the default); `open` when anyone may make an account, no code needed (the launch checklist, section B, says the order of the steps) | no |
| `TJ_S3_ENDPOINT`, `TJ_S3_BUCKET`, `TJ_S3_ACCESS_KEY_ID`, `TJ_S3_SECRET_ACCESS_KEY` | from section 2 | the last two |
| `TJ_EMAIL_API_KEY`, `TJ_EMAIL_FROM` | the email service's key (Resend) and the address mail comes from, like `Roundturn <beta@roundturn.app>` (the domain must be verified at the provider). Both or neither: with neither, the form that joins the beta answers 503 and no code can be sent | the first |
| `TJ_EMAIL_API_URL` | leave out (the default is `https://api.resend.com`); it exists for tests | no |
| `TJ_CLIENT_IP_HEADER` | `CF-Connecting-IP` **only** when every request really comes through Cloudflare; otherwise leave it out. On Render it is set (2026-10-09): Render's edge is Cloudflare and sets it, and a request with a forged value is refused there | no |
| `TJ_APP_VERSION` | the commit the image was built from, so a stale browser tab can tell it is stale | no |
| `TJ_MAX_CONCURRENT_IMPORTS` | leave out (the default is 2 per process) | no |
| `TJ_RATE_LIMITS` | **leave out.** `off` is an emergency switch | no |
| `WEB_CONCURRENCY` | `2` on a plan with two processors (the load test in doc 05 section 8 recommends it) | no |
| health check path | `/readyz` (the database is reachable and has every migration this image ships with), so a release that forgot its migration never takes traffic and the old instance keeps serving. `/healthz` is plain liveness | n/a |

Never put any of these in the repository, in a chat or in a log. If one leaks, follow [rotate-secrets.md](rotate-secrets.md).

The server stores nothing on its own disk. It can run as several instances: sessions and the audit log are in Postgres, and the bucket is shared. (Rate limits are counted per process, on purpose: two instances give a person twice the allowance.)

## 5. In front of it: Cloudflare

**As deployed (2026-10-09, [first-deploy.md](first-deploy.md)):** a `CNAME` for `app` to the service's `onrender.com` address, **DNS only** (grey cloud), because Render's own edge is Cloudflare and supplies the visitor's real address in `CF-Connecting-IP` (`TJ_CLIENT_IP_HEADER`); a second Cloudflare in front would probably hide it (not tested), and Render's guide asks for the DNS-only record while it verifies the domain and issues its certificate. The list below is the original plan with the orange cloud; none of it is in use, and the rate-limit rule needs a decision (Render's edge plus the server's own limits are what runs).

- A DNS record for the app's host, proxied (orange cloud), pointing at the host.
- **A rate-limit rule** on the sign-in routes and on `POST /api/v1/*` (the free plan allows one; the marketing site's form already uses it, so this needs a plan decision or a combined expression). The server has its own limits underneath; this one stops floods before they reach it.
- **Cache** only `/assets/*` (the file names carry a hash, so they never change). Nothing under `/api/`, `/auth/` or `/files/` is cacheable, and the server says so in its headers; do not override them.
- *Check: TLS mode "Full (strict)", and "Always use HTTPS".*

## 6. The deletion sweep

A scheduled job running the same image with the command below: once a day while it only finishes deletions, every hour once it is the `all` command (section 6, below). It needs the sweep role's connection string, the Kinde management settings and the bucket.

```bash
python -m tradejournal.pg.sweep deletions --apply
```

| Setting | Value |
|---|---|
| `TJ_SWEEP_DATABASE_URL` | the `app_sweep` connection string |
| `TJ_KINDE_DOMAIN` | the Kinde **tenant address**, `https://<tenant>.kinde.com`. **Not** the web service's custom domain: Kinde refuses the management token there (HTTP 400, checked 2026-10-10) |
| `TJ_KINDE_M2M_CLIENT_ID`, `TJ_KINDE_M2M_CLIENT_SECRET` | the management application |
| `TJ_S3_*` | the same four as the web service |

These settings go on the **cron job** only: the Render service of type Cron Job, which has a Trigger Run button. Never put them on the web service (the one that shows `app.roundturn.app`). On 2026-10-10 they were entered on the web service by mistake, which sent its sign-in back to Kinde's default address until it was put right.

It prints only counts. Run it once **without** `--apply` first (a dry run) and check that it says what you expect. A person is deleted seven days after they ask; until the job runs, nothing is removed, so a missing schedule is a privacy failure, not just a chore. **Never run it with `--apply` against a database you care about while testing.**

### The "your access has ended" email (migration 0016)

The same job also sends one email to each person whose time-limited access (a beta code, a plan code) has run out, or was ended by an operator and the email at that moment did not go. It is a second command of the same program, so it needs no new service, only two more settings and a different command line:

| Setting | Value |
|---|---|
| `TJ_EMAIL_API_KEY`, `TJ_EMAIL_FROM` | the same two as the web service (section 4). Both or neither |

1. Run the migration (0016) first; the command fails on an older database.
2. Add the two settings to the cron job's Environment page.
3. **Look before sending**: `python -m tradejournal.pg.sweep ended-notices` (no `--apply`) in the job's shell, or **Trigger Run** after temporarily setting the command to that. It prints how many people are due and nothing else. A first run on a database that has had beta users for a while should say a small number (only grants that ended in the last 14 days count); a large number means something is wrong, so stop and look.
4. Change the job's command to `python -m tradejournal.pg.sweep all --apply` (the Start Command on the cron job's Settings page, and the `dockerCommand` line of `render.yaml` in the same change so the file and the dashboard agree). `all` runs deletions first and then the notices, and exits non-zero if either could not run or had a send refused, so Render shows a failed run (and emails you, if its notifications are on).
5. After a run: the log ends with the notice counts (`due`, `sent`, `failed`) and, for failures, the provider's short reason codes with counts and no address: `mail_network` (could not reach it), `mail_refused_<status>` (it refused the message; the number is its HTTP status, and 401 or 403 usually means the key or the sender's domain is not accepted), `mail_unavailable_<status>` (it was down or limiting you: 429 or a 5xx). A person whose send failed is simply due again tomorrow, for as long as their access ended within the last 14 days; nothing is lost, and a person is never sent the same notice twice.

`TJ_EMAIL_API_KEY` is a secret: it goes into the dashboard, never into the chat or this repository (rotate it with [rotate-secrets.md](rotate-secrets.md)). With the settings missing, `ended-notices --apply` refuses to run (it does not pretend to send), and `all --apply` still finishes the deletions first and then exits with an error.

### Beta sign-ups that never became accounts (migrations 0017 and 0018)

`python -m tradejournal.pg.sweep beta-signups [--keep-hours N] [--apply]` removes the sign-ups (address, first name, favourite firm) and codes of people who asked to join, never made an account, and whose last code is older than 24 hours (the owner's decision of 2026-10-10; a code works for 24 hours since migration 0018). It needs only `TJ_SWEEP_DATABASE_URL`. `--keep-hours` can lengthen that and cannot go below 24. **It is the third step of `all`**, so the one cron job does it; because the promise is 24 hours, the job should run **every hour** (`0 * * * *`), not daily, or a sign-up can be kept for up to 47 hours. To switch it on: on the `roundturn-sweep` job set the schedule to `0 * * * *`, the command to `python -m tradejournal.pg.sweep all --apply`, and add `TJ_EMAIL_API_KEY` and `TJ_EMAIL_FROM` (the same values as the web service; **add them before changing the command**, or every run ends with an error because the email step refuses). Run it once without `--apply` and look at the count. A person who does have an account is never removed by it; their sign-up goes when the account is deleted, which the database does by itself.

## 7. After the first start

1. `GET /healthz` is 200 and `GET /readyz` is 200 with every check "ok".
2. **Make yourself the first operator.** Make an invitation as the schema owner (`TJ_OWNER_URL`), sign in with it, then run `python -m tradejournal.pg.users make-operator --email you@example.com`, add `TJ_ADMIN_DATABASE_URL` to the web service and restart it. See [run-locally.md](run-locally.md), "The operator tools".
3. Smoke test with your own account, **on staging first**: sign in; import a small CSV; add a note and a tag; add a screenshot and see it (the picture must come from `https://<the app's host>/files/...`, never from the bucket's address); download your data and look inside the ZIP; sign out.
4. Check the headers of the home page once (`curl -I`): `Strict-Transport-Security`, a `Content-Security-Policy` with no `unsafe-inline`, `X-Content-Type-Options: nosniff`.
5. Take a database backup and **do the restore drill** in [restore.md](restore.md). Until one restore has worked, the launch gate for backups is not met.
6. Make sure the sweep job is scheduled and has run once (a dry run counts for the first time).

## 8. A new release

1. Merge to `main` (CI must be green first). The design (doc 02 section 7) has `main` deploy to staging and a deliberate fast-forward (`git push origin main:production`) after a staging check deploy to production.
2. If the release adds a migration, run it **before** the new image takes traffic (section 1, step 4). Keep migrations additive (add, do not remove or rename) so the previous image still works against the new schema while the new one starts; a release that cannot be additive should be a planned maintenance window.
3. Watch `/readyz` and the host's logs for five minutes. Every log line has a request id; a person who sees "The server had a problem" can quote its reference.
4. To roll back: redeploy the previous image. Do not roll the database back unless [restore.md](restore.md) says so.

## What is not known yet

- Whether the provider's database administrator can create the six roles (section 1).
- Whether a deploy is zero-downtime on the chosen plan, and what the backup window is (doc 02 has the vendors' claims; they are not verified).
- The exact cost of the host plus the bucket's storage and requests.
- Whether `/files/` should be cached privately at the edge. It is not, today; every picture goes through the server.
- Sentry or another error tracker: not set up (needs an account and a dependency; the logs are the only record).
- The orphan-file report (pictures in the bucket that no row names) is not built; a failed cleanup leaves a file that costs a little storage and is otherwise harmless.
