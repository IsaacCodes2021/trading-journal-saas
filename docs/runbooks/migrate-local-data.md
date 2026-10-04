# Migrating your own local journal into Postgres

**Status:** the script exists (`backend/tradejournal/pg/migrate_local.py`) and is tested on a synthetic journal. **It was rehearsed on the real journal on 2026-10-04** (a copy; local database; a throwaway user): 8 accounts, 23 trades, 28 fill pairs, 9 tags, 14 screenshot records, 7 ledger entries, and every golden check passed, both as a dry run and for real. The hosted run waits for sign-in (M5) and the screenshot files for M6. This runbook is how to run it, and how to read the result.

## What it does

It copies the single-user app's journal (`data/journal.db`) into **one user's** journal in Postgres: accounts, fees, imports, trades, fill pairs, tags, notes, day notes, screenshot records, the ledger and the settings. Then it runs a **golden check**: it asks both databases the same questions and compares the answers.

- **The original is never opened.** `journal.db` (and a `-wal` file beside it, if the app was running) is copied to a temporary folder first, and the copy is what is read. An older journal is brought up to the current schema on the copy only.
- **Everything is one transaction.** A failure leaves nothing behind. `--dry-run` does the whole run, prints the report and rolls back.
- **It refuses a user who already has data.** It only fills an empty journal; it will not guess how to merge.
- **Trades are copied as stored**, not regrouped. If the copy is wrong, a check fails. Two extra lines show what today's code would change (below).
- **Screenshot files are not moved yet.** The records are copied with their file names; the files go to object storage with the R2 pipeline (M6). The report lists any file the journal names that is missing from `data/screenshots`.

## Reading the report

```
Golden check (source on the left, copy on the right):
  [ok  ] rows in trades
  [ok  ] per account: trade count, gross, fees and net
  [FAIL] every trade, every column
           source: 10,000 rows, fingerprint 3fa9...
           copy:   10,000 rows, fingerprint 77c1...
  [note] trades whose stored result today's fee and trading-day rules would change
```

- **`[ok  ]`**: both databases gave the same answer. Counts, per-account totals, per-day results and the ledger are exact decimals. Accounts, trades, fill pairs, tags, notes, screenshots, imports and fees are compared row by row through a fingerprint that ignores ids (the copy has new ids).
- **`[FAIL]`**: a difference. The run ends with `A CHECK FAILED. Do not use this copy.` and exits non-zero. Send the report text (it holds counts and totals, never notes or passwords) and do not use the copy.
- **`[note]`**: informational, and does not fail the run. It says what today's grouping and fee code would change about trades stored by an older version of the app. A non-zero number is not a copy error; it means a rule has moved since those trades were written, and is worth a look.

## Rehearsal (before the hosted database exists)

Run it against your **local** Postgres, as a throwaway user, until the report is clean. This is the step that needs your eyes: compare a few totals in the report to what you know from the old app (an account's net P&L, the number of trades, the ledger total).

1. Start the local Postgres server (it is stopped between sessions).
2. Make a throwaway user in the local database and note its id. Ask the assistant, or run as the owner role: `INSERT INTO users (idp_subject, email) VALUES ('rehearsal', 'rehearsal@example.test') RETURNING id`.
3. Dry run: `cd backend`, set `TJ_DATABASE_URL` to the local `app_rw` URL (it is in the gitignored `backend/.env.test` as `TJ_DEV_APP_URL`), then:

   ```bash
   python -m tradejournal.pg.migrate_local --source ../../trading-journal/data/journal.db --user-id <the id> --dry-run
   ```

   The source path only has to be readable; nothing in the old folder is changed.
4. Read the report as above. When it is clean, run again without `--dry-run`, then check the numbers in the real copy.

## The real run (after sign-in works, M5)

1. Sign in once, so the identity provider has created your account. Find your user id (the operator tools or the owner role can look it up by email).
2. Point `TJ_DATABASE_URL` at the hosted database's `app_rw` URL and run with `--dry-run` first, then for real.
3. Keep `journal.db` and the screenshots folder until the screenshot files have been uploaded (M6) and you have checked the hosted app against the old one.

## If something goes wrong

- `refused: this user already has journal data`: the target is not empty. Use a fresh user, or delete the earlier copy (as the owner) and run again.
- `no journal at ...`: the path is wrong.
- A traceback: nothing was written (one transaction). Keep the message; it does not contain your data.
