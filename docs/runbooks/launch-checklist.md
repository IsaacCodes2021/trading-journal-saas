# Launch checklist

One page that says what stands between today and (1) letting the first invited people in (**private beta**) and (2) opening to everyone (**public launch**). It gathers the launch gates from design doc 04 section 4, the tasks on the Notion progress board and the runbooks, and says **who can do each one**. It is a snapshot of 2026-10-05: re-read the sources before relying on a tick, and tick items here only when the thing has been seen to work.

Legend: `[x]` built and tested in code · `[ ]` open. Tags say what blocks it: **(me)** the assistant can do it with no input · **(you)** needs the owner's decision, account or approval · **(host)** needs the deployed host (spike S4) · **(real)** built, but not yet proven against the real provider.

Related: [deploy.md](deploy.md), [restore.md](restore.md), [incident.md](incident.md), [rotate-secrets.md](rotate-secrets.md), [support.md](support.md), design doc 04 (the full gates) and doc 05 (the roadmap).

## A. Before the first invited person signs in (private beta)

### The product works for a person

- [x] Sign-in flow with every check (state, nonce, PKCE, signature, issuer, audience, expiry, verified email), invite-only sign-up, sessions you can list and end, sign out **(real)**: the flow is tested against a fake provider; one run on the real Kinde tenant is still to do **(you)**, steps in [try-real-sign-in.md](try-real-sign-in.md)
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
- [ ] HTTPS only with HSTS, Cloudflare TLS "Full (strict)" **(host)**
- [ ] Private bucket proven with a real upload, a view and a deletion **(you)** (the code is built and tested against a fake: [deploy.md](deploy.md) section 2)
- [ ] Two-factor authentication on GitHub, the host, Cloudflare, the registrar, Kinde and the payment provider **(you)**
- [ ] The operator account made, with two-step verification on Kinde first **(you)**
- [ ] A short security review of the deployed system, including `/admin` **(me, after the host exists)**; a penetration test if the budget allows **(you)**

### Data safety

- [ ] **Backups on, and one restore drill done and written down** (a gate) **(host)**, procedure in [restore.md](restore.md)
- [ ] The deletion sweep scheduled and seen to run once **(host)**
- [ ] Your own journal moved into the hosted database: a rehearsal on a copy has passed every check; the real run needs your yes **(you)**

### Operations

- [ ] One image deployed to a staging host and then production, from the same image **(you, host)**, steps in [deploy.md](deploy.md)
- [ ] Alerts that have been seen to fire in a test: a 5xx spike, a failed readiness check, a backup older than 26 hours **(host)**
- [ ] An error tracker with scrubbing checked against a real error **(you)** (needs an account and a dependency)
- [ ] The incident runbook rehearsed once, as a tabletop **(me, with you)**; the contact list and notice templates filled in, which needs the company's exact legal name **(you)**
- [ ] The HTTP load test run against the staging host **(host)**

### Words that must be right

- [ ] The company's exact legal wording (pinned): for the Terms "operated by" line, the Privacy Policy controller line and the payment emails **(you)**
- [ ] Privacy Policy, Terms and the financial disclaimer reviewed by a professional, then published **(you)**: the drafts are in `docs/legal/` and are **not** on the site
- [ ] The invite email says: invite-only beta, best-effort support, what is and is not promised **(you)**

## B. Before it opens to everyone (public launch)

- [ ] **What Free includes and what Pro adds** decided (the machinery exists; nothing is limited yet) **(you)**
- [ ] A payment provider that accepts a trading journal: the two pre-clearance emails sent and answered **(you)**, then checkout, webhooks and failed-payment handling, a billing screen and discount codes **(me, after approval)**
- [ ] Cookie and consent wording confirmed for the regions served **(you, a professional)**
- [ ] A subprocessor list published (host, database, Cloudflare, Kinde, email, payment provider, error tracker) **(you)**
- [ ] An end-to-end browser test suite and a scan with ZAP against staging **(you for the Playwright install; then me)**
- [ ] A status page, or a decision to use the site's front page for notices **(you)**; see [support.md](support.md) section 9
- [ ] SPF, DKIM and DMARC for the domain's mail **(you)**
- [ ] Billing's reverse path: a cancelled subscription and an account deletion agree **(me, with billing)**
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
