-- 0015: the operator's side of the beta: see who joined, end someone's access, and record that the "ended" email left.
--
-- The operator role (`app_admin`) reads the sign-ups (an address, a first name and a favourite prop firm: it already sees every
-- account's address), and may stamp two columns on a redeemed grant: `revoked_at` (end this person's beta access now) and
-- `ended_notice_sent_at` (the email that says so left). It still cannot change when a grant ends, who it belongs to, or what it
-- gives, and it cannot read a code's hash.

GRANT SELECT ON beta_signups TO app_admin;
GRANT SELECT (id, revoked_at, ended_notice_sent_at) ON grant_redemptions TO app_admin;
GRANT UPDATE (revoked_at, ended_notice_sent_at) ON grant_redemptions TO app_admin;
