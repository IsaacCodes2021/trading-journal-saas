-- 0024: usernames.
--
-- A person may pick a username: 3 to 20 letters, digits or underscores. No two accounts can hold the same one, whatever the
-- capitals ("Trader_Joe" blocks "trader_joe"); the name keeps the capitals it was typed with. Accounts made before this
-- have none, and so does a new account until its owner picks one. The app asks once after sign-in; a person who says
-- "not now" is not asked again (`username_prompt_skipped_at`) and can still pick or change one in Settings at any time.
--
-- The sign-in role, which already reads and updates a person's own row, gets these two columns. No other role does: the
-- operator's tools, the sweep and the webhooks have no use for a username. Deleting an account frees its username.
--
-- Additive: both columns start empty, so the running app works on the new schema before and after its deploy.

ALTER TABLE users ADD COLUMN username text
    CONSTRAINT users_username_shape CHECK (username ~ '^[A-Za-z0-9_]{3,20}$');
ALTER TABLE users ADD COLUMN username_prompt_skipped_at timestamptz;

CREATE UNIQUE INDEX users_username_lower ON users (lower(username));

GRANT SELECT (username, username_prompt_skipped_at) ON users TO app_login;
GRANT UPDATE (username, username_prompt_skipped_at) ON users TO app_login;
