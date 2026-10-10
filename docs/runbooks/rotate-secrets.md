# Rotating secrets

Every secret the product uses, where it lives, what happens when it changes, and the steps to replace it. Use it for a scheduled rotation, and for an incident ([incident.md](incident.md)): **rotate first, investigate second.** A secret that was pasted somewhere public, committed, or shown in a log is leaked even if nobody is known to have used it.

Never put a secret in this repository, a chat, a ticket or a log. The continuous-integration secret scan (gitleaks) runs on every change, but it is a safety net, not a plan. Secrets live in the host's environment settings (and in the owner's password manager); nothing else.

## Status

- **Not yet rehearsed on a real host.** There is no production host yet (S4, Render), so the "where it lives" column for production says "host environment". Rehearse each row once on the staging host before the beta opens, and write the date in the last column.
- The steps below were written from the code (`api/config.py`, `auth/`, `pg/provision.py`), not from a live rotation.
- Nothing needs to change in the code to rotate any of these: they are all read from the environment at start-up, so a rotation is *change the value, restart*.

## The inventory

| Secret | What it protects | Where it lives | If it leaks | Rotated and rehearsed |
|---|---|---|---|---|
| `TJ_SESSION_SECRET` | Signs the sign-in state and the CSRF tokens; keys the address hash in the audit log | Host environment | An attacker could forge a sign-in state or a CSRF token. Critical only if combined with another flaw; rotate anyway | |
| `TJ_KINDE_CLIENT_SECRET` (with `TJ_KINDE_CLIENT_ID`, `TJ_KINDE_DOMAIN`) | The product's own sign-in application at Kinde | Host environment; Kinde dashboard | Someone could complete a sign-in flow as the product. High | |
| `TJ_KINDE_M2M_CLIENT_ID` and `TJ_KINDE_M2M_CLIENT_SECRET` | The machine-to-machine application the deletion sweep uses to remove people from Kinde (read and delete users) | The sweep's environment only; Kinde dashboard | Someone could list and delete every user at Kinde. **Critical** | |
| `TJ_EMAIL_API_KEY` | Sending email as the product (the beta code, the "access ended" notice) | Host environment; the email provider's dashboard | Someone could send mail as `roundturn.app`, and phish people with the product's address. High | |
| `TJ_DATABASE_URL` (role `app_rw`) | Row-level-security-bound reads and writes of tenant data | Host environment; database | Read and write of tenant data *as a tenant the attacker names*: the database stops them seeing other people's rows only if the policy holds. Critical | |
| `TJ_LOGIN_DATABASE_URL` (role `app_login`) | Sign-in: users, sessions, invites, the audit log | Host environment | Create sessions for anyone. Critical | |
| `TJ_ADMIN_DATABASE_URL` (role `app_admin`) | The operator tools | Host environment (only if the operator tools are on) | Operator-level changes (suspend, plan, codes) without the operator guard. High | |
| `TJ_SWEEP_DATABASE_URL` (role `app_sweep`) | Deleting accounts that are past their grace | The sweep's environment | Delete accounts that are due. High | |
| `TJ_OWNER_URL` (role `tj_owner`) | Migrations; the operator command line; the only role that can delete audit rows | **Never on the host.** The owner's machine only | Everything, including rewriting the audit log. Critical | |
| The database provider's own admin login | Everything at the provider | Owner's password manager | Total | |
| Host, Cloudflare, GitHub, registrar, Kinde and email logins and their recovery codes | The accounts that control the product | Owner's password manager; recovery codes offline | Total | |
| Cloudflare API tokens, GitHub tokens, deploy keys | Deploying and the site | The tool that uses them | Deploy code or change DNS. Critical | |
| Payment provider keys, webhook signing secret | (When billing exists, M9) | Host environment | Fake or hidden payments | n/a yet |
| `TJ_S3_ACCESS_KEY_ID` and `TJ_S3_SECRET_ACCESS_KEY` (the R2 API token) | Reading, writing and deleting every person's screenshot files | The web service's environment **and** the sweep's | Read, overwrite or delete anyone's pictures. **Critical** (the files are the only content a person cannot re-import) | |

**Not secrets, so there is nothing to rotate:** invite codes and plan codes (only their hashes are stored; to take one away, revoke it in the operator tools, Codes & audit); the Kinde client *id* and *domain*; the public site's D1 database id.

## Before any rotation

1. Pick a quiet moment, and have both the old and the new value to hand until you have checked that it works.
2. Generate values with a real random source, never by hand (below).
3. Change it in the host environment and in the owner's password manager at the same time, so the two never disagree.
4. After the restart, check `/readyz` (`GET /readyz` answers 200 when the database is reachable and its schema is current), then sign in as yourself.
5. Write the date here, in the last column of the table.

Generate a session secret (64 hexadecimal characters; production refuses a shorter one):

```powershell
cd backend
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

## Session secret (`TJ_SESSION_SECRET`)

What changes when it does, from the code:

- **Nobody is signed out.** Sessions are random tokens stored hashed in the database; the secret is not part of them.
- **Someone in the middle of signing in** (the few minutes between "Sign in" and the redirect back) gets an error and tries again.
- **A page that was open before the change** holds a CSRF token made with the old secret, so its next write is refused (403) until the page is reloaded. Say so in the support note if you rotate during the day.
- **The audit log's address hashes change**: two events from the same address on either side of the rotation no longer look alike. Old rows are not rewritten. This is the only lasting cost, and it is a reason to rotate deliberately, not to avoid it.
- There is **one** secret and no overlap period: every instance must restart together, so set the new value on all of them before restarting any.

Steps: generate, set `TJ_SESSION_SECRET` on the host, restart, check.

## Sign-in application secret (`TJ_KINDE_CLIENT_SECRET`)

1. In Kinde, open the product's application and generate a new client secret. (If Kinde keeps the old one working for a while, note how long; if not, sign-in is broken from this step until step 2 is done, so do both within a minute.)
2. Set `TJ_KINDE_CLIENT_SECRET` on the host and restart.
3. Check: sign out, sign in. Then, in Kinde, make sure the old secret is revoked.

People already signed in are unaffected (their session does not touch Kinde again until they sign in).

## Machine-to-machine secret (deletion sweep)

1. In Kinde, rotate the secret of the machine-to-machine application (the one with only the read-users and delete-users permissions).
2. Update `TJ_KINDE_M2M_CLIENT_SECRET` in the sweep's environment (and nowhere else: the API server does not need it).
3. Check with a count that deletes nothing: `python -m tradejournal.pg.sweep deletions` (no `--apply`; the default only counts). **Never run it with `--apply` against a database that holds data you want to keep.**

## Object-storage token (`TJ_S3_*`)

The token is limited to the one bucket, so a leak is limited to the pictures (never the database). Rotate with no gap:

1. In Cloudflare, create a **second** API token for the same bucket (object read and write). Do not revoke the first yet.
2. Put the new key id and secret in `TJ_S3_ACCESS_KEY_ID` and `TJ_S3_SECRET_ACCESS_KEY` for **both** the web service and the sweep job, then restart the web service.
3. Check with your own account that a picture still shows and a new one uploads; the logs say "the file store was unavailable" (type only, never the address or the answer) if the new token is wrong.
4. Revoke the first token in Cloudflare.
5. If the old token leaked, also look in the bucket for objects you did not expect, and note that **R2 keeps no versions** (there is no way to put back a changed or deleted picture until a second copy of the bucket exists; see [deploy.md](deploy.md) section 2, step 4), so write down what you find for the affected people. Everything under `u/<person's id>/` belongs to one person.

## Database role passwords

Each role is a plain login role (not a superuser, no BYPASSRLS, no CREATEDB or CREATEROLE). For each: `app_rw`, `app_login`, `app_admin`, `app_sweep`, `app_webhook` and `tj_owner`.

1. Generate a password with letters and digits only (so it can sit in a connection string as is): the generator in `pg/provision.py` does this; or `python -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"`.
2. As the provider's admin login: `ALTER ROLE app_rw PASSWORD '<new>';` (one statement per role; the statement is logged by some providers, so use their tool if it has one).
3. Update the matching `TJ_*_DATABASE_URL` (or `TJ_OWNER_URL`) and restart. Connections already open keep working until the pool recycles them; new ones fail until the URL is updated, so do steps 2 and 3 together.
4. Check `/readyz` and a sign-in.

Rotate `tj_owner` last, and only ever on the owner's machine; it is not set on the host at all.

## When the leaked secret is the owner role or the provider's admin login

This is Critical. In order: stop the service (the host's stop control), rotate the provider login and the owner role, rotate every other role, rotate `TJ_SESSION_SECRET`, then **end every session** ([incident.md](incident.md) section 3) before starting the service again, and read the audit log for the window ([incident.md](incident.md) section 4). An owner-level attacker could have deleted audit rows, so a clean log is not proof; compare it with the database provider's own connection log if it keeps one.

## Scheduled rotation

Until there is a better reason, once a year and at once on any of: someone with access leaves, a laptop is lost or sold, a secret appears anywhere it should not. Put a recurring reminder in the calendar; this file has no way to remind you.
