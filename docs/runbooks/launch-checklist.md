# Launch checklist

One page that says what stands between today and (1) letting the first invited people in (**private beta**) and (2) opening to everyone (**public launch**). It gathers the launch gates from design doc 04 section 4, the tasks on the Notion progress board and the runbooks, and says **who can do each one**. It is a snapshot of 2026-10-05: re-read the sources before relying on a tick, and tick items here only when the thing has been seen to work.

Legend: `[x]` built and tested in code · `[ ]` open. Tags say what blocks it: **(me)** the assistant can do it with no input · **(you)** needs the owner's decision, account or approval · **(host)** needs the deployed host (spike S4) · **(real)** built, but not yet proven against the real provider.

Related: [deploy.md](deploy.md), [restore.md](restore.md), [incident.md](incident.md), [rotate-secrets.md](rotate-secrets.md), [support.md](support.md), design doc 04 (the full gates) and doc 05 (the roadmap).

## A. Before the first invited person signs in (private beta)

### The product works for a person

- [x] Sign-in flow with every check (state, nonce, PKCE, signature, issuer, audience, expiry, verified email), invite-only sign-up, sessions you can list and end, sign out **(proven 2026-10-09)**: tested against a fake provider, then run for real on the deployed site (an invitation, sign-up at Kinde, sign-in, sign-out and back in; the owner reported that everything worked and the data was still there)
- [x] Import (preview, confirm, history, undo), dashboard, trade log, trade page with notes, tags and screenshots, calendar and day notes, reports, prop accounts with fees, rules and ledger
- [x] Export of everything and deletion of the account with a 7-day grace, including the picture files; the sweep that finishes deletions
- [x] First-run setup (`/welcome`), plans and plan codes, the operator's tools (`/admin`)
- [ ] The Help page, so the commonest questions have an answer in the app **(me)**
- [ ] A rehearsal of one real person's path from the invite email to a first imported file, on staging **(host)**

### Security gates (design doc 04)

- [x] Tenant isolation: forced row-level security, the generated two-tenant suite on every route that names a row, schema lint, role checks, the import-boundary test
- [x] CSRF on every write, strict CSP and headers, notes sanitized on write and read, upload limits (size, pixels, row cap, concurrency), per-person and per-address rate limits, no secrets in the repository or the bundle, locked and audited dependencies, a container that fails closed
- [x] Audit log events, structured logs with a scrubber, request ids shown on server errors
- [ ] Kinde configured as the design says: email verification required, breached-password check, MFA available, brute-force protection, exact callback and logout URLs **(you)**
- [ ] Separate credentials per environment; the schema owner's password used only by the migration step **(host)**
- [x] HTTPS only with HSTS **(host)**: seen on `https://app.roundturn.app` (2026-10-09), certificate by Google Trust Services via Render; the design's own Cloudflare proxy and "Full (strict)" are not used (DNS only, [first-deploy.md](first-deploy.md))
- [ ] Private bucket proven with a real upload, a view and a deletion **(you)**: upload and view reported working on the deployed site (2026-10-09), deletion not yet reported ([deploy.md](deploy.md) section 2)
- [ ] A second copy of the screenshot bucket, because R2 has no versioning: the tool is built and tested (`python -m tradejournal.filebackup`); **(you)** a second bucket, its token and a daily job, then one restore, [deploy.md](deploy.md) section 2, step 4
- [ ] Two-factor authentication on GitHub, the host, Cloudflare, the registrar, Kinde and the payment provider **(you)**
- [x] The operator account made, with two-step verification on Kinde first (2026-10-09; `/admin` seen working with "Two-step verification current")
- [ ] A short security review of the deployed system, including `/admin` **(me, after the host exists)**; a penetration test if the budget allows **(you)**

### Data safety

- [ ] **Backups on, and one restore drill done and written down** (a gate) **(host)**, procedure in [restore.md](restore.md)
- [ ] The deletion sweep scheduled and seen to run once **(host)**
- [ ] Your own journal moved into the hosted database: a rehearsal on a copy has passed every check; the real run needs your yes **(you)**

### Operations

- [x] One image deployed to a host (Render, from `main`, 2026-10-09), `/readyz` ready, sign-in working, steps in [first-deploy.md](first-deploy.md)
- [ ] A staging and a production environment from the same image (there is one environment today) **(you, host)**, steps in [deploy.md](deploy.md)
- [ ] Alerts that have been seen to fire in a test: a 5xx spike, a failed readiness check, a backup older than 26 hours **(host)**
- [ ] An error tracker with scrubbing checked against a real error **(you)** (needs an account and a dependency)
- [ ] The incident runbook rehearsed once, as a tabletop **(me, with you)**; the contact list and notice templates filled in, which needs the company's exact legal name **(you)**
- [ ] The HTTP load test run against the staging host **(host)**

### Words that must be right

- [ ] The company's exact legal wording (pinned): for the Terms "operated by" line, the Privacy Policy controller line and the payment emails **(you)**
- [ ] Privacy Policy, Terms and the financial disclaimer reviewed by a professional, then published **(you)**: the drafts are in `docs/legal/` and are **not** on the site
- [ ] The privacy wording says backups hold a deleted picture for up to 30 days (the second copy's keep time) **(you)**
- [ ] The beta works as described in [beta.md](beta.md): personal codes, 30 days, read-only afterwards (rules built and tested; the form, the emails and the pages are not) **(me)**
- [ ] The invite email says: invite-only beta, best-effort support, what is and is not promised **(you)**

## B. Before it opens to everyone (public launch)

- [x] **What Free includes and what Pro adds** decided: one plan, Pro, USD 15 a month, a 7-day trial and no free plan (2026-10-14)
- [ ] A payment provider that accepts a trading journal: Paddle's written pre-clearance asked for and answered **(you)**; the
  trial, webhooks, failed-payment handling and the billing screens are built with a fake provider (2026-10-15 and 16, switch
  off); the Paddle adapter, the payment page and discount codes next **(me)**. Operating it: [billing.md](billing.md)
- [ ] Migrations 0020, 0021 and 0022 applied **before** the billing branches merge (the Cron Job runs the new image at
  the next hour) **(you, as the schema owner)**
- [ ] A test payment in Paddle's sandbox, and the rest of the lifecycle ([billing.md](billing.md), "Paddle sandbox"): the
  connector, the payment page and discount codes are built (2026-10-16) and tested against a stand-in for Paddle's API, never
  against Paddle itself **(you for the sandbox settings and the webhook address; then me)**
- [ ] Switch-on day: an override for your own account, then `python -m tradejournal.pg.plans start-trials --apply` (built
  2026-10-16), then the Cron Job's and the Web Service's billing settings **(you)**; [billing.md](billing.md)
- [ ] If Stripe instead of Paddle: Stripe's written answer on eligibility (its restricted list names "funded prop trading")
  **(you)**; the Stripe connector is built beside Paddle's (2026-10-23) and tested against a stand-in for Stripe's API and
  calls in Stripe's test mode; migration 0023 applied before its merge **(you, as the schema owner)**; a Stripe test payment
  and the lifecycle ([billing.md](billing.md), "Stripe test mode") **(you for the key and the webhook endpoint; then me)**;
  then `stripe_check` passing against the live settings and `TJ_BILLING_PROVIDER=stripe` ([billing.md](billing.md),
  "Switching from Paddle to Stripe") **(you)**
- [ ] **Open sign-up** (decided 2026-10-24: the beta is over, anyone may make an account): the sign-up page that needs no
  code merged and live **(me, on your word)**; then `TJ_SIGNUP_MODE=open` on the Web Service (Render, the one that shows
  app.roundturn.app; the Cron Job does not read it) **(you)**; then one sign-up with a brand-new email in a private window, all
  the way through Kinde's registration screen and the first sign-in **(you, with me watching the log)**; then the site's launch
  pull request **(me, on your word)**. Before the setting is changed, a person with no code is turned away with "That sign-up
  code did not work": do not publish the site's new sign-up links before it is. Rollback: `TJ_SIGNUP_MODE=invite`. Anyone with
  an email can then start a trial, so watch the number of new accounts per day for the first weeks
  ([support.md](support.md)); the sign-up routes are rate-limited per address (`login`, and 10 code or promo guesses in 15
  minutes).
- [ ] **The launch offer** on the home page: the code `LAUNCH10` made in the operator tools, Codes (10% off, 3 months, only
  Roundturn Pro; billing must be on and the payment provider's settings live, because the code is made at the provider too)
  **(you)**, because until it exists the pop-up's "Sign up now" ends on a promo-code error; the counts of what visitors do
  with the pop-up are read with `npm run popup-stats` in `site/` (an API token that can only read Cloudflare analytics, in a
  git-ignored file; never in the chat) **(you for the token; then me)**
- [ ] Cookie and consent wording confirmed for the regions served **(you, a professional)**
- [ ] Retire the old random `GR-` plan codes once none is outstanding (promo codes replace them; nothing is deleted, the blank-code
  form can still make one)
- [ ] A subprocessor list published (host, database, Cloudflare, Kinde, email, payment provider, error tracker) **(you)**
- [ ] An end-to-end browser test suite and a scan with ZAP against staging **(you for the Playwright install; then me)**
- [ ] A status page, or a decision to use the site's front page for notices **(you)**; see [support.md](support.md) section 9
- [ ] SPF, DKIM and DMARC for the domain's mail **(you)**
- [ ] Billing's reverse path: a cancelled subscription and an account deletion agree (built and tested with the fake provider,
  2026-10-15; to prove once in Paddle's sandbox) **(me)**
- [ ] Support has run for the whole beta and the questions it received are answered in the Help page **(me)**
- [ ] Watch activation (invited, signed in, imported a file) and the import failure rate for the first weeks; a failure rate above [5%] is fixed before more people come in **(me, with real data)**

## C. The last day

- [ ] Re-read this page and doc 04 section 4 against the running system; every `[ ]` in section A is ticked or consciously accepted in writing.
- [ ] Take a backup and note its time. Confirm the restore drill is less than a month old.
- [ ] Rotate nothing that is working; do check that every secret's owner and rotation steps are known ([rotate-secrets.md](rotate-secrets.md)).
- [ ] Know who answers the mailbox that day, and tell them where [incident.md](incident.md) is.
- [ ] Release in small batches of invitations, not all at once, and watch the logs and the import failure rate after each batch.

## What is not on this list on purpose

Features. A launch is not waiting for the prop-rule tracker, the demo mode, thumbnails or a mobile layout beyond what exists; they are tracked on the board and ship after, fed by what the first people say.
