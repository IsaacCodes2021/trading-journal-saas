# The beta: personal codes, 30 days, and what ending means

How access works while the product is in beta. The rules are in the database (migration 0014) and in `entitlements.py`;
this page says what they are and how to work with them.

## The rules

1. **Nobody gets an account without a code.** A code is an invitation (`TJ-XXXX-XXXX`): single use, tied to one email address,
   expiring. The server checks it **before** the browser is sent to Kinde, and again after, against the address Kinde
   verified. Kinde itself still lets anyone register an identity on its own page; that identity cannot get into Roundturn,
   and it can be deleted in Kinde's dashboard.
2. **Joining the beta makes a personal code** for that address (`beta_join`, called as the sign-in role by the form's
   endpoint, which then emails it; the code is shown on no page). The code carries **30 days of full access (Pro)**, counted from the day the account is created.
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

## What is built and what is not

| Piece | State |
|---|---|
| Table, personal code, the rules above, the 30-day cap, ending a grant early (`grant_redemptions.revoked_at`), the access rule (`plans.access`) | Built and tested (migration 0014) |
| The code is checked before Kinde; a grant that cannot be given refuses the sign-up | Built and tested |
| The public form's endpoint (`POST /api/v1/beta/join`), the email sender (Resend behind `api/mailer.py`) and the email with the code | Built and tested; needs a Resend account, a verified `roundturn.app` and the two settings before it answers anything but 503 |
| The operator's list of beta sign-ups (`GET /api/admin/v1/beta`, with states, filters, search and counts) and **revoke** (`POST /api/admin/v1/beta/{id}/revoke`: ends the access at once and sends the "access ended" email; for someone with no account yet it withdraws their code) | Built and tested (migration 0015); the screen is **Operator tools > Beta testers** (`/admin/beta`) |
| The "access ended" email, sent once, after a revoke (and recorded as sent) | Built and tested |
| The daily job that sends the same email when the 30 days run out | Not built |
| The read-only rule on every write route (`require_writable`), `access` in the session, and a generated test that every write is covered | Built and tested |
| Our own sign-in and sign-up pages (`/signin`, `/signup`; `/invite` still works) and a signed-out visit going to `/signin` instead of straight to Kinde | Built and tested (front end only); Kinde's own password screen is still the default look until its branding is applied |
| The marketing site's landing page and its "Sign up for the beta" form | Designed (the canvas), not built |

## Releasing the migration

Render deploys every commit to `main`, and a new image stays out of traffic until `/readyz` says the database has its
migration. So: merge, then run the migration (`python -m tradejournal.pg.migrate` with `TJ_OWNER_URL`, or
`provision_hosted --migrate`), then the new instance takes over. Nothing in this migration changes how an existing account
behaves.
