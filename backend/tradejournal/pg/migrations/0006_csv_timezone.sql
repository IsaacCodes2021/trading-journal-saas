-- 0006: the timezone a person's Tradovate CSV files are written in, when it is not their own.
--
-- Until now one `timezone` meant both "show me times in this zone" and "read my CSV files in this zone". They are not
-- always the same: an export can carry exchange time (America/Chicago) while the person trades from Los Angeles, and
-- reading it in the wrong zone silently lands trades hours off and on the wrong trading day (doc 03 section 7).
--
-- NULL means "the same as my timezone", which is what every existing user already has, so nothing changes for them.
-- Each import records the zone it was read in (`imports.source_timezone`), so a later correction can re-run it.

ALTER TABLE user_settings ADD COLUMN csv_timezone text;
