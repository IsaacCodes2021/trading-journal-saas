-- 0013: the files of a deleted account (requirement F-A6, doc 03 section 6).
--
-- Deleting an account deletes its rows in one transaction, but its screenshot files are in the file store, not in the
-- database, and removing them can fail (a disk that is not there, a bucket that does not answer). So that a failure cannot
-- leave files nobody remembers, the sweep writes down which folder to remove in the same transaction that deletes the
-- account, and removes the entry once the store confirms: the same shape as `identity_removals`.
--
-- A system table with no `user_id` column: it outlives the account on purpose, and holds only the folder's name
-- (`u/<the former user's id>/`), which names no one now that the account is gone.

CREATE TABLE file_removals (
    prefix      text PRIMARY KEY
                CHECK (prefix ~ '^u/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/$'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    attempts    integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error  text                                           -- the kind of error, never a message from the store
);

GRANT SELECT, INSERT, DELETE ON file_removals TO app_sweep;
GRANT UPDATE (attempts, last_error) ON file_removals TO app_sweep;
