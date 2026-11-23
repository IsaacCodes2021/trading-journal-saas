-- 0026: copy-trading groups (docs/new-feature-copy-trading/decisions.csv).
--
-- A copy trader places each trade on one leader account and the copier repeats it on follower accounts. Roundturn
-- stores the trade once, on the leader, and never stores a follower's copy: a follower's trades and P&L are worked out
-- when they are read, from the leader's trades and the dates the follower was in the group (`tradejournal/pg/queries.py`).
-- So 20 followers add no trade rows, only the group and its membership events.
--
-- Membership is a log of dated events (`added`, `removed`), never edited: a follower counts a leader trade when the
-- trade's trading day is on or after the day it was added and before the day it was removed. A follower chosen when the
-- group is made may have no day (`effective_date` NULL): it counts every leader trade. Later changes are dated today or
-- later (checked in the app), so old trades keep the followers they had.
--
-- One group per leader (an account leads at most one group); an account may follow several groups.
--
-- Additive: two new tables, so the running app works on the new schema before its deploy.

CREATE TABLE copy_groups (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                uuid NOT NULL DEFAULT app_user_id(),
    name                   text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    leader_account_id      bigint NOT NULL,
    fills_acknowledged_at  timestamptz NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    UNIQUE (user_id, name),
    UNIQUE (user_id, leader_account_id),
    FOREIGN KEY (user_id, leader_account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);

CREATE TABLE copy_group_membership_events (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         uuid NOT NULL DEFAULT app_user_id(),
    group_id        bigint NOT NULL,
    account_id      bigint NOT NULL,
    event_type      text NOT NULL CHECK (event_type IN ('added', 'removed')),
    effective_date  date CHECK (effective_date IS NOT NULL OR event_type = 'added'),
    note            text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, group_id) REFERENCES copy_groups (user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);
-- The read joins a leader's trades with each follower's windows, worked out in this order.
CREATE INDEX copy_group_membership_events_window
    ON copy_group_membership_events (user_id, group_id, account_id, effective_date, id);
CREATE INDEX copy_group_membership_events_account ON copy_group_membership_events (user_id, account_id);

ALTER TABLE copy_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE copy_groups FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON copy_groups USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON copy_groups TO app_rw;

ALTER TABLE copy_group_membership_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE copy_group_membership_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON copy_group_membership_events
    USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON copy_group_membership_events TO app_rw;
