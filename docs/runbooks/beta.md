# The beta: personal codes, 30 days, and what ending means

How access works while the product is in beta. The rules are in the database (migration 0014) and in `entitlements.py`;
this page says what they are and how to work with them.

## The rules

1. **Nobody gets an account without a code.** A code is an invitation (`TJ-XXXX-XXXX`): single use, tied to one email address,
   expiring. The server checks it **before** the browser is sent to Kinde, and again after, against the address Kinde
   verified. Kinde itself still lets anyone register an identity on its own page; that identity cannot get into Roundturn,
   and it can be deleted in Kinde's dashboard.
2. **Joining the beta makes a personal code** for that address (`beta_join`, called as the sign-in role by the form's
   endpoint, which then emails it; the code is shown on no page). **The code works for 24 hours** (migration 0018; it was 14 days
   before). It carries **30 days of full access (Pro)**, counted from the day the account is created.
   Rules in the database: one code an hour per address, five in all, none for an address that already has an account or has
   used its code, none while the beta grant is revoked. A new code withdraws the previous one. The same answer is given to
   everyone, so the form cannot be used to find out who has joined.
3. **Any code that gives full access gives at most 30 days**: the plan codes made in the operator tools or with
   `python -m tradejournal.pg.plans create-grant` (`--days` 1 to 30), and the beta grant. The database enforces it for every
   code made from now on (older ones are not looked at).
4. **A sign-up that cannot be given its access is refused.** If an invite carries a grant and the grant cannot be given (it
   ran out or was withdrawn meanwhile), the whole sign-up is undone and the code is not used up. Before this rule, such a
   person got an account with no end date.
5. **When the access ends (it ran out, or an operator ended it) the account is read-only.** The person keeps their data and
   can look at everything and download it; they cannot create, change or delete. Every write to the journal answers 403 "Your
   beta access has ended, so your journal is read-only"; what stays open is deleting the account, signing out and ending
   other sessions, redeeming a code, and every read and the export. A test generated from the API description fails for any
   new write route that is neither refused nor on that short list. This applies only to someone whose full
   access came from a code or an invite and has ended. An account that never had one (yours, accounts made with a plain
   invite) is never affected, and neither is anyone with an operator override or, later, a subscription. A new code gives
   access back.
6. **What the beta keeps about a person, and for how long** (migration 0017). A sign-up holds an address, a first name and a
   favourite prop firm; each code made for it is an invite tied to the address. **When an account is deleted, its sign-up and
   every invite tied to its address are removed in the same statement** (a trigger on `users`, so it holds for any way a user
   row goes, not only the sweep). Before 0017 they were left behind. **People who asked to join and never made an account**
   are removed **24 hours after their code was made** (the owner's decision, 2026-10-10: sign-up, address, first name, firm
   and every code; the code stops working at the same moment). That is the third step of the sweep job
   (`python -m tradejournal.pg.sweep all --apply`, hourly) and also runs alone as
   `python -m tradejournal.pg.sweep beta-signups [--keep-hours N] [--apply]`; N defaults to 24 and cannot be lower, because a
   shorter time would remove someone whose code still works. A dry run prints the count only. Someone who has an account
   is never touched by it. What this does **not** reach: the host's database backups (they keep deleted rows until they age
   out), the email provider's own log of messages it delivered, and a person's identity at Kinde if they started
   registering there and never finished (Kinde keeps that until it is deleted in Kinde). A removal request by email is
   honoured by the operator's `POST /api/admin/v1/beta/{id}/remove` (a reason is required and kept in the audit log with the
   sign-up's number, never the address): it removes a sign-up that has **no account** and every code tied to its address.
   For someone with an account it answers 409: delete the account, which does both by itself. In **Operator tools > Beta
   testers** it is the "Remove sign-up" button in a waiting person's panel.

## What is built and what is not

| Piece | State |
|---|---|
| Table, personal code, the rules above, the 30-day cap, ending a grant early (`grant_redemptions.revoked_at`), the access rule (`plans.access`) | Built and tested (migration 0014) |
| The code is checked before Kinde; a grant that cannot be given refuses the sign-up | Built and tested |
| The public form's endpoint (`POST /api/v1/beta/join`), the email sender (Resend behind `api/mailer.py`) and the email with the code | Built and tested; needs a Resend account, a verified `roundturn.app` and the two settings before it answers anything but 503 |
| The operator's list of beta sign-ups (`GET /api/admin/v1/beta`, with states, filters, search and counts) and **revoke** (`POST /api/admin/v1/beta/{id}/revoke`: ends the access at once and sends the "access ended" email; for someone with no account yet it withdraws their code) | Built and tested (migration 0015); the screen is **Operator tools > Beta testers** (`/admin/beta`) |
| The "access ended" email, sent once, after a revoke (and recorded as sent) | Built and tested |
| The daily job that sends the same email when the 30 days run out (`python -m tradejournal.pg.sweep ended-notices`, or `all` with the deletions; once per person; a refused send is tried again the next day for 14 days) | Built and tested (migration 0016); switched on by two settings and a command change on the cron job ([deploy.md](deploy.md) section 6) |
| A deleted account takes its beta sign-up and codes with it (a trigger); the sweep removes sign-ups that never became accounts 24 hours after their code was made (`beta-signups`, the third step of `all`) | Built and tested (migration 0017); the operator's "Remove sign-up" (route and button) for a removal request is built |
| The read-only rule on every write route (`require_writable`), `access` in the session, and a generated test that every write is covered | Built and tested |
| Our own sign-in and sign-up pages (`/signin`, `/signup`; `/invite` still works) and a signed-out visit going to `/signin` instead of straight to Kinde | Built and tested (front end only); Kinde's own password screen is still the default look until its branding is applied |
| Kinde's own password, code and second-factor screens in the app's look (`kinde/` holds the code, `kinde-branding.md` the steps) | Written, **not run**: needs Kinde's custom domain, a GitHub repository and one setting on Render (owner's steps) |
| The beta form page in the app (`/join`: first name, address, favourite firm; three states; the same answer whatever became of the address) | Built and tested (front end only); it works once the email sender is set up |
| The marketing site's landing page (who it is for, what it does, limitations, goals) linking to `/join`, `/signin` and `/signup`; the old waitlist form and function are gone | Built and tested; the old waitlist database is untouched (decide what to do with it: see `site/README.md`) |

## Releasing the migration

Render deploys every commit to `main`, and a new image stays out of traffic until `/readyz` says the database has its
migration. So: merge, then run the migration (`python -m tradejournal.pg.migrate` with `TJ_OWNER_URL`, or
`provision_hosted --migrate`), then the new instance takes over. Nothing in these migrations changes how an existing account
behaves. Migrations 0014 to 0017 ship together in this stack of pull requests and are run in one pass after the first
merge; all four are additive (new tables, columns, functions and a trigger, and one check that keeps new codes to 30 days or fewer,
added `NOT VALID` so it looks at no existing row).
