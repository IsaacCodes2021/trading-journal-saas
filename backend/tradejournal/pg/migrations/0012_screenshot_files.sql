-- 0012: what the file routes need to know about a screenshot (doc 03 section 6).
--
-- `stored_name` has been a bare file name since the local app; for a file made by the hosted server it is the file's key in
-- the store (`u/<user id>/<random>.png`, see filestore.py). The new columns are what serving and counting need without
-- reading the file: its content type, its size (summed into `user_usage.storage_bytes`, so a quota is one number), and the
-- width and height its header gave when it was accepted.
--
-- A row from before this migration has no type, size or dimensions: it is served by the extension of its name and counts as
-- zero bytes until its file is next seen. They are not filled in here because the owner role cannot see tenant rows (forced
-- row-level security), so an UPDATE in a migration would change nothing.

ALTER TABLE screenshots
    ADD COLUMN content_type text CHECK (content_type IS NULL OR content_type IN ('image/png', 'image/jpeg', 'image/gif', 'image/webp')),
    ADD COLUMN size_bytes   bigint NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    ADD COLUMN width        integer CHECK (width IS NULL OR width > 0),
    ADD COLUMN height       integer CHECK (height IS NULL OR height > 0);
