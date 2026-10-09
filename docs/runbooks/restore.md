# Backups and restoring

How to get data back, and how to prove that you can. The launch gate in design doc 04 reads: *backups configured, and one successful restore drill documented*. This file is the procedure for the drill and for the real thing. **Neither has been done yet**: there is no production database to back up (S4, the host, is open), and the commands below were written from the code and the design, not run. The drill log at the bottom is empty until someone fills it in.

Related: [incident.md](incident.md), [rotate-secrets.md](rotate-secrets.md), design doc 03 section 6 (screenshots) and doc 04 (T10, data loss).

## What is backed up, and from where

| Data | Where it lives | Backup (design) | Decided? |
|---|---|---|---|
| Everything about people, trades, notes, accounts, plans, sessions, the audit log | Postgres | The provider's point-in-time recovery (restore to a chosen moment) **and** a nightly dump to a bucket at a different provider or account | The host and provider are not chosen (S4) |
| Screenshot files | A private R2 bucket (`bucketstore.py`) | **None yet.** R2 has no versioning (Cloudflare's S3 compatibility page lists `PutBucketVersioning` and `GetBucketVersioning` as not implemented (checked 2026-10-09)); a second copy of the bucket is the plan and is not built ([deploy.md](deploy.md) section 2, step 4) | The bucket exists and screenshots work (2026-10-09); a lost or wrongly deleted picture cannot be restored |
| The marketing site's waitlist | Cloudflare D1 (`roundturn-waitlist`) | Export on request; low value | Not scheduled |
| Code and documents | GitHub | The repository itself; clone it somewhere else now and then | Done by being a Git repository |
| Identity (who can sign in) | Kinde | Kinde's own; the product does not hold passwords | n/a |

The alert "backup older than 26 hours" in design doc 04 section 5 does not exist yet; a backup nobody checks is a hope, not a backup.

## The rules that cannot be broken

1. **Never restore over the live database.** Restore into a *new, empty* database, check it, and only then point the product at it. The old one stays until you are sure (and may be the evidence for an incident).
2. **Backups contain everyone's data**, including notes. They are as sensitive as the database itself: stored encrypted, in a place the application's credentials cannot reach (an attacker who has the application's keys must not be able to delete the backups too), and listed in the privacy policy with their retention.
3. **A restore is a rollback for everyone**: it takes people back to a past moment. Say so to the people affected before you do it (see incident.md section 7).

## Restoring the database

The product creates its roles by hand once on the managed database (doc 03 section 1): `tj_owner` (owns the schema), `app_rw`, `app_login`, `app_admin`, `app_sweep`, `app_webhook`. A dump of one database does **not** contain roles or their passwords, so **on a new server create the roles first**, with the same names, then restore. On the same server they already exist.

1. **Get the backup**: the provider's point-in-time restore into a new database (the provider's own steps), or the latest nightly dump.
2. **Create an empty database owned by `tj_owner`** and restore the dump into it as `tj_owner`, so every object belongs to the owner and the grants come back with it:

```powershell
# a dump is taken like this (custom format, one database):
pg_dump --format=custom --no-owner --file backup.dump "<owner url of the live database>"

# and restored like this, into a new empty database made with the same owner:
pg_restore --no-owner --exit-on-error --dbname "<owner url of the NEW database>" backup.dump
```

3. **Set the time zone**: migration `0001` pins the database's time zone to UTC (`ALTER DATABASE ... SET timezone TO 'UTC'`), and a dump made without `--create` does not carry database-level settings. Set it again on the new database, then check in a new connection that `SHOW timezone;` says `UTC`. Money and times are wrong in subtle ways otherwise (timestamps arrive in the session's zone).

```sql
ALTER DATABASE <new database name> SET timezone TO 'UTC';
```

4. **Apply any newer migrations** (a no-op if the dump is current): `TJ_OWNER_URL=<owner url of the new database>` then `python -m tradejournal.pg.migrate`. It prints `nothing to apply` or the names it applied, and refuses if an already-applied migration's file has changed (the checksums).
5. **Check it is whole**:
   - `GET /readyz` on an instance pointed at the new database answers 200 (the database is reachable and its schema is current).
   - Counts. **Row-level security applies to the owner role too**: counting tenant tables as `tj_owner` reads zero rows and proves nothing. Count as the provider's admin login (which bypasses it) or through a tenant session, for `users`, `accounts`, `trades`, `fill_pairs`, `day_notes` and `screenshots`, and compare with the live database's counts at the time of the backup.
   - Sign in as yourself and open a trade you know.
   - The audit log's last event is about when you expect.
6. **Point the product at it**: change `TJ_DATABASE_URL`, `TJ_LOGIN_DATABASE_URL`, `TJ_ADMIN_DATABASE_URL` and `TJ_SWEEP_DATABASE_URL` on the host (the passwords of the roles on the new server may differ) and restart. Keep the old database untouched.
7. **End every session** if the restore was because of an incident ([incident.md](incident.md) section 3).

## After restoring to an earlier moment

A restore forgets everything after the backup. These need doing by hand, because the data that says they happened is gone:

- **People who deleted their account after the backup are back.** Their data is in the database again, and the identity provider no longer knows them (the sweep removed them), so they cannot sign in, but **their data exists, and they asked for it to be deleted**. Compare the database's users with Kinde's list; for each account with no Kinde user, schedule its deletion again and let the sweep run. Keep a list of deletions made (the audit log has `account_deleted` events, but it was restored too, so also keep the sweep's output somewhere outside the database when it runs).
- **Anything people did after the backup is lost**: imports, notes, new accounts. Tell them what the window was.
- **Sign-ups after the backup**: their accounts exist at Kinde but not in the database. When one signs in again, the sign-in treats them as new and (in invite-only mode) needs an invite; give them one.
- **Plan codes and invites made or used after the backup** are gone or unused again; check the operator tools, Codes & audit.

## Restoring one person's data

There is no tool for restoring a single account from a backup, and the operator role cannot read tenant data by design. If it is ever needed: restore into a separate scratch database (steps above), have the person's own export (`Settings`, Data & privacy, Download my data) as the reference, and ask whether re-importing their file is enough; it usually is. Do not write a script that reads one person's rows out of a backup without a decision recorded in the incident note.

## Screenshots

The files are in a versioned bucket, so a deleted or overwritten file can be restored by version in Cloudflare (the object's key is `u/<person's id>/<random>.<ext>`, and the database row names it); the database holds the references. **Not rehearsed**: no bucket exists yet. Restoring the database to an earlier moment and the bucket to a later one leaves files with no row; the orphan job (design doc 03 section 6; **not built**: it needs its own decision, see doc 05 section 14, slice 7) would **report** them first, deletes an object only after it has been unreferenced across two runs at least 7 days apart, and is designed to refuse to run when the database looks older than the bucket (the sign of a restore). **Do not run an orphan clean-up right after a restore.**

## The drill

Do it once on the staging host before the beta opens, then once a year. Pick a real recent backup, follow this file *exactly as written* and fix the file wherever it was wrong.

Record each run:

| Date | Who | Backup used (type, age) | Time to a working copy | Data lost (how far back) | Problems and the fix to this file |
|---|---|---|---|---|---|
| | | | | | |

**A drill counts as successful when** a person who was not part of the incident can follow only this file, and at the end: `/readyz` answers 200, counts match, the owner's own sign-in works, and the time and data-loss figures are written down and are ones the owner can live with. If they are not, the backup plan (how often, how far back) changes, not the figures.
