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
| **Sweep job** | `python -m tradejournal.pg.sweep deletions --apply`, daily | Finishes account deletions (rows, picture files, the identity at Kinde). Needs the bucket, so it works as a separate scheduled process |
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
4. **There is no versioning to turn on.** R2 does not offer it (Cloudflare's S3 compatibility page lists `PutBucketVersioning` and `GetBucketVersioning` as not implemented (checked 2026-10-09)), so there are no old versions to restore from, and the earlier plan that relied on them is withdrawn. What protects a picture today: the server writes each one once, under a name it makes itself, and never overwrites it, so the only way to lose one is a deletion (a person deleting a picture, or an account being deleted). What does **not** exist yet is a way to put one back after a bad deletion, a bug or a leaked token: that needs a second copy of the bucket (a scheduled job that copies objects the second bucket does not have yet, ideally to a different provider, which is cheap because objects never change). **Not built**: see the launch checklist.

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
| `TJ_SIGNUP_MODE` | `invite` for the private beta (the default) | no |
| `TJ_S3_ENDPOINT`, `TJ_S3_BUCKET`, `TJ_S3_ACCESS_KEY_ID`, `TJ_S3_SECRET_ACCESS_KEY` | from section 2 | the last two |
| `TJ_CLIENT_IP_HEADER` | `CF-Connecting-IP` **only** when every request really comes through Cloudflare; otherwise leave it out | no |
| `TJ_APP_VERSION` | the commit the image was built from, so a stale browser tab can tell it is stale | no |
| `TJ_MAX_CONCURRENT_IMPORTS` | leave out (the default is 2 per process) | no |
| `TJ_RATE_LIMITS` | **leave out.** `off` is an emergency switch | no |
| `WEB_CONCURRENCY` | `2` on a plan with two processors (the load test in doc 05 section 8 recommends it) | no |
| health check path | `/readyz` (the database is reachable and has every migration this image ships with), so a release that forgot its migration never takes traffic and the old instance keeps serving. `/healthz` is plain liveness | n/a |

Never put any of these in the repository, in a chat or in a log. If one leaks, follow [rotate-secrets.md](rotate-secrets.md).

The server stores nothing on its own disk. It can run as several instances: sessions and the audit log are in Postgres, and the bucket is shared. (Rate limits are counted per process, on purpose: two instances give a person twice the allowance.)

## 5. In front of it: Cloudflare

- A DNS record for the app's host, proxied (orange cloud), pointing at the host.
- **A rate-limit rule** on the sign-in routes and on `POST /api/v1/*` (the free plan allows one; the marketing site's form already uses it, so this needs a plan decision or a combined expression). The server has its own limits underneath; this one stops floods before they reach it.
- **Cache** only `/assets/*` (the file names carry a hash, so they never change). Nothing under `/api/`, `/auth/` or `/files/` is cacheable, and the server says so in its headers; do not override them.
- *Check: TLS mode "Full (strict)", and "Always use HTTPS".*

## 6. The deletion sweep

A scheduled job, once a day, running the same image with the command below. It needs the sweep role's connection string, the Kinde management settings and the bucket.

```bash
python -m tradejournal.pg.sweep deletions --apply
```

| Setting | Value |
|---|---|
| `TJ_SWEEP_DATABASE_URL` | the `app_sweep` connection string |
| `TJ_KINDE_DOMAIN`, `TJ_KINDE_M2M_CLIENT_ID`, `TJ_KINDE_M2M_CLIENT_SECRET` | the management application |
| `TJ_S3_*` | the same four as the web service |

It prints only counts. Run it once **without** `--apply` first (a dry run) and check that it says what you expect. A person is deleted seven days after they ask; until the job runs, nothing is removed, so a missing schedule is a privacy failure, not just a chore. **Never run it with `--apply` against a database you care about while testing.**

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
