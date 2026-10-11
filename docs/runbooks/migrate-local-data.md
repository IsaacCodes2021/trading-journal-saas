# Migrating your own local journal into Postgres

**Status:** the script exists (`backend/tradejournal/pg/migrate_local.py`) and is tested on a synthetic journal. **It was rehearsed on the real journal on 2026-10-04** (a copy; local database; a throwaway user): 8 accounts, 23 trades, 28 fill pairs, 9 tags, 14 screenshot records, 7 ledger entries, and every golden check passed, both as a dry run and for real. That rehearsal copied the screenshot *rows* only; since then the script also takes the **image files** into the file store (below), which has been tested on synthetic journals and **not yet rehearsed on your real screenshots folder**. The hosted run waits for your yes and for sign-in (M5). This runbook is how to run it, and how to read the result.

## What it does

It copies the single-user app's journal (`data/journal.db`) into **one user's** journal in Postgres: accounts, fees, imports, trades, fill pairs, tags, notes, day notes, screenshot records, the ledger and the settings. Then it runs a **golden check**: it asks both databases the same questions and compares the answers.

- **The original is never opened.** `journal.db` (and a `-wal` file beside it, if the app was running) is copied to a temporary folder first, and the copy is what is read. An older journal is brought up to the current schema on the copy only.
- **Everything is one transaction.** A failure leaves nothing behind. `--dry-run` does the whole run, prints the report and rolls back.
- **It refuses a user who already has data.** It only fills an empty journal; it will not guess how to merge.
- **Trades are copied as stored**, not regrouped. If the copy is wrong, a check fails. Two extra lines show what today's code would change (below).
- **Screenshot files go into the file store, when you give it one.** Pass `--files-dir <folder>` (or set `TJ_FILES_DIR`; it must be **the folder the web server uses**, so on a host the persistent disk), **or set the four `TJ_S3_*` bucket settings** to copy them into the bucket (not both). A bucket is checked the same way: every file is read back from it and compared with its checksum. Each file is checked the way an upload is (a real PNG, JPEG, GIF or WebP, 10 MB, 50 million pixels, 20 per trade, 500 MB in all) and copied under a new name in your own folder (`u/<your id>/...`); the copy's row records the new name, type, size and dimensions. The golden check then compares the rows with the old names mapped to the new ones, **reads every copied file back from the store and compares its SHA-256 with the original**. A file that is missing, not an acceptable image or over a limit is *not* copied: its row still is (under its old name), and the report lists which and why (an export lists such a row as missing). A dry run checks every file and writes none; a failure takes the files already copied out again. Without `--files-dir` only the rows are copied, and the report says so. Your `data/screenshots` folder is only read, never changed.

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
3. Dry run: `cd backend`, set `TJ_DATABASE_URL` to the local `app_rw` URL (it is in the gitignored `backend/.env.test` as `TJ_DEV_APP_URL`), and a throwaway folder for the files, then:

   ```bash
   python -m tradejournal.pg.migrate_local --source ../../trading-journal/data/journal.db --user-id <the id> --files-dir <a throwaway folder> --dry-run
   ```

   The source path only has to be readable; nothing in the old folder is changed. The screenshots folder is taken to be `screenshots` beside `journal.db` (use `--screenshots` for another). The report ends with how many files would be taken in and lists any that would not, with the reason.
4. Read the report as above. When it is clean, run again without `--dry-run`, then check the numbers in the real copy.

## The real run (after sign-in works, M5)

1. Sign in once, so the identity provider has created your account. Find your user id (the operator tools or the owner role can look it up by email).
2. Point `TJ_DATABASE_URL` at the hosted database's `app_rw` URL and `--files-dir` at the folder the hosted server uses (the same disk), and run with `--dry-run` first, then for real.
3. Keep `journal.db` and the screenshots folder until you have checked the hosted app against the old one: open a few trades, look at their screenshots, and download your data (the manifest should list no missing screenshot files).

## If something goes wrong

- `refused: this user already has journal data`: the target is not empty (the app can archive a prop account but not delete one). Use a fresh user, or empty this one with `python -m tradejournal.pg.clear_journal --user-id <id>` (a dry run that counts), then again with `--apply --expect-accounts N --expect-trades N` using the numbers the dry run showed. It refuses while any screenshot exists (delete those in the app first), keeps the sign-in and settings, and prints counts only. Then run the migration again.
- `no journal at ...`: the path is wrong.
- A traceback: nothing was written (one transaction). Keep the message; it does not contain your data.
