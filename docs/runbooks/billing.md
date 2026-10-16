# Billing: the switch, the rules, and what to do when

How Roundturn's billing works and how to operate it. The design is in doc 05 section 16 (slices 2 and 3), the tables in doc
03, the provider plan in `docs/design/paddle-integration.md` (not committed). **Billing is off in production**, and nothing
here can charge anyone until a real provider is connected and switched on, each step with the owner's yes.

## What is built, and what is not

| Built (2026-10-15 and 16) | Not done yet |
|---|---|
| The trial, the access rules, the subscription tables (migration 0021) | Anything in Paddle's sandbox or live account (each step needs the owner's yes) |
| The connector, a fake provider for the tests, and **the Paddle connector** | A test payment end to end in the sandbox (section "Paddle sandbox" below) |
| `POST /webhooks/billing`, the person's billing routes, the screens | A nightly comparison of discount codes with Paddle's copies |
| **The payment page `/billing/pay`**, the only page that loads Paddle's script | |
| Deletion stopping a subscription, the nightly check, the emails | |
| `start-trials`: the trial for the accounts that exist on switch-on day | |
| **Discount codes** (migration 0022): made by the operator, given to Paddle, saved by people, applied at checkout | |

## The rules (what a person gets)

| Situation | Access | Plan tag in the header |
|---|---|---|
| Billing off (today) | Exactly as before billing existed | As before |
| New account, billing on | **7-day trial, no card**, full access (unless the invite brings its own free days) | `Trial · 5 days left` |
| Free days from a code or an invite, or the team's override | Full access until they end | `Pro · 26 days left`, or `Pro` |
| Subscribed, renewing | Full access to the end of the paid month **plus one day** (a late renewal message locks nobody out) | `Pro` |
| Cancelled | Full access to the **end of the paid month**, then read-only | `Pro · ends Nov 14` |
| A payment failed | Full access for **7 days from the first failure** while the provider retries, then read-only | `Payment failed` |
| Nothing live | **Read-only**: everything can be seen and downloaded; nothing can be imported or changed | `Read-only` |

There is **no free plan** once billing is on. Read-only never deletes anything. A refund made at the provider is recorded and
changes no access by itself. Subscribing during free days delays the first charge until they end, rounded up to whole days.

## The settings, by Render service

| Setting | Web Service (`roundturn`, shows `app.roundturn.app`) | Cron Job (`roundturn-sweep`, has Trigger Run) |
|---|---|---|
| `TJ_BILLING_ENABLED` | `true` turns billing on (the trial, Subscribe, read-only for nothing live) | `true` sends the billing emails and words the "access ended" email for a trial |
| `TJ_BILLING_PROVIDER` | `paddle` (`fake` is refused in production) | `paddle`, so the job can send cancels and run the nightly check |
| `TJ_WEBHOOK_DATABASE_URL` | The `app_webhook` role's **private** address. Required with billing on | Not needed |
| `TJ_PADDLE_ENV` | `sandbox` or `production` | The same |
| `TJ_PADDLE_API_KEY` (secret) | The server key (sandbox keys contain `_sdbx`) | The same key: the job cancels and checks subscriptions |
| `TJ_PADDLE_WEBHOOK_SECRET` (secret) | The notification destination's secret key (`pdl_ntfset_...`) | Not needed |
| `TJ_PADDLE_CLIENT_TOKEN` | The client-side token for the payment page (`test_...` or `live_...`; public by design) | Not needed |
| `TJ_PADDLE_PRODUCT_ID`, `TJ_PADDLE_PRICE_ID` | Roundturn Pro and its USD 15 monthly price (`pro_...`, `pri_...`; sandbox and live differ) | Not needed |

The web service refuses to start with billing on and no provider, with `fake` in production, or without
`TJ_WEBHOOK_DATABASE_URL`. With `TJ_BILLING_PROVIDER=paddle` it needs all six `TJ_PADDLE_*` settings, and it refuses a sandbox
key with `TJ_PADDLE_ENV=production`, a live key with `sandbox`, a client token of the other environment, or a mistyped id. Leaving `TJ_BILLING_ENABLED` unset (or `false`) is the off switch; nothing else needs removing.

## Releasing the code (before any switch-on)

1. **Apply migrations 0020, 0021 and 0022 before merging** the branches that ship them, as the schema owner (`TJ_OWNER_URL`,
   `python -m tradejournal.pg.migrate`; `deploy.md` section 1, step 4). Both are additive, so the running image works on
   the new schema. The order matters for the **Cron Job**: it runs the new image at the next hour without waiting for the
   web service's `/readyz`, and its billing step needs the 0021 tables. (The Web Service is safe either way: it stays out of
   traffic until the migrations are there.)
2. Merge. With billing off, people see no change; the operator's overview gains a "Paying · on trial" tile (0 and 0).
3. Read the next Cron Job run's log: the billing step prints `cancels sent 0, cancels still to send 0, subscriptions checked
   0` and `billing is switched off (TJ_BILLING_ENABLED): no billing emails`.

## Switch-on day (each step with the owner's yes)

**Before switching on,** every account that exists needs something that gives full access, or it becomes read-only the
moment billing is on (no free plan):

1. The owner's own account: an operator override, Pro, no end date (Operator tools, the account, Plan override).
2. Everyone else with nothing (accounts from a plain invite, an override that ended): a 7-day trial from that day, as the
   schema owner, counting first:

   ```
   python -m tradejournal.pg.plans start-trials            # counts only: "accounts with nothing that gives Pro: N"
   python -m tradejournal.pg.plans start-trials --apply    # gives each a 7-day trial (once per account; audit: trial_started)
   ```

   It skips anyone who ever had free days from a code or an invite (theirs already ran), has an override in force or a
   subscription, is suspended, or is an operator (it counts operators with nothing, who need step 1). Run it on the day,
   just before step 5: every trial starts the moment it runs.
3. People on beta codes keep their 30 days, then subscribe; they get no extra trial.

Then:

4. The Cron Job's settings first (`TJ_BILLING_ENABLED`, `TJ_BILLING_PROVIDER`, `TJ_PADDLE_*`), then Trigger Run once and
   read the log.
5. The Web Service's settings (`TJ_WEBHOOK_DATABASE_URL`, `TJ_BILLING_PROVIDER`, `TJ_PADDLE_*`, and last
   `TJ_BILLING_ENABLED=true`). Render redeploys.
6. Check: Settings > Plan & billing shows the price and Subscribe; the provider's webhook test reaches
   `/webhooks/billing` with a 200; a sandbox subscription goes through (the Paddle guide's section 7.2).

**To switch off again:** set `TJ_BILLING_ENABLED` to `false` on both services. Subscriptions keep running at the provider and
their rows stay; the app simply stops showing billing, and every rule returns to what it was before.

## The payment page (`/billing/pay`)

Subscribe sends the browser to `/billing/pay?_ptxn=txn_...`, a transaction the server made for the person signed in. The page
loads Paddle.js from Paddle's address (Paddle requires it), and Paddle opens its checkout for that transaction: card only for
now, dark, in English, with Paddle's own discount box hidden (a code is typed in the app and the checkout already carries
it). Paid: back to Plan & billing, which waits for Paddle's notification. Closed: back to Plan & billing.

It is the only page that may load a script from another site: it has a Content-Security-Policy of its own (Paddle's script,
frames and API; no inline code) and every other page keeps the strict one. Paddle's emails link here too, to update a card.
It answers 404 unless billing is on with Paddle. **To check in the sandbox:** the browser console must show no CSP refusal
while the checkout opens and takes a test card, including the 3-D Secure card; add only what is reported. Found on
2026-10-16 and allowed: Paddle's stylesheet (`sandbox-cdn.paddle.com`, `cdn.paddle.com` live) and the inline styles
Paddle.js sets on its frame. Without them the checkout is a small unstyled box that cannot be completed.

**Paddle's checkout settings** (sandbox, then live): the default payment link is `https://app.roundturn.app/billing/pay`;
payment recovery cancels the subscription when retries run out (Roundturn has no pause). The sandbox takes the same link:
its form refuses any `localhost` address (2026-10-16), and without a saved link Paddle creates no checkout at all
(`transaction_default_checkout_url_not_set`, shown as "The payment provider would not open a checkout"). A test on a PC
still works: Roundturn sends people to its own page (`/billing/pay?_ptxn=...` on the site in use) and never to Paddle's
link, which only appears in Paddle's emails (a sandbox email's "update card" link opens the real site, where billing is off).

## Paddle sandbox: from nothing to a test payment (each step needs the owner's yes)

1. In the sandbox dashboard: an API key with the permissions in the Paddle guide's section 2.3 (discounts read and write
   included; the whole key, starting `pdl_sdbx_apikey_`: without that part Paddle answers `authentication_malformed`), a
   client-side token, and the product "Roundturn Pro" with a USD 15 monthly price (no trial on the price: the
   trial is Roundturn's). Or Claude makes the product and price through the `paddle-sandbox` tools, after a yes. The
   default payment link `https://app.roundturn.app/billing/pay`, saved (above).
2. A notification destination at a public HTTPS address: a tunnel to a PC (an install) or a paid staging Web Service (a
   cost). Subscribe it to `subscription.*`, `transaction.completed`, `transaction.payment_failed` and `adjustment.*`.
   On the owner's PC: cloudflared 2026.9.3 at `C:\Users\Isaac\Tools\cloudflared\cloudflared.exe` (Cloudflare's GitHub
   release, checksum checked, 2026-10-16). `cloudflared.exe tunnel --url http://localhost:8000` prints a
   `https://<words>.trycloudflare.com` address, a new one at each start; the destination is that address followed by
   `/webhooks/billing`, and its secret key (`pdl_ntfset_...`) goes in `TJ_PADDLE_WEBHOOK_SECRET`.
3. The six `TJ_PADDLE_*` settings (sandbox values), `TJ_BILLING_PROVIDER=paddle`, `TJ_BILLING_ENABLED=true` and
   `TJ_WEBHOOK_DATABASE_URL` on the test server, with migrations 0020 to 0022 applied to its database. On a PC: the six go
   in the gitignored `backend/.env.paddle`, and the dev server sets the rest:
   `.venv\Scripts\python.exe -m tradejournal.api.devserver --real-sign-in --billing --migrate` in `backend/` (sandbox
   only; `--migrate` applies the migrations to the local `tj_dev`), then in `frontend/` `pnpm build` once (the payment
   page's script) and the front end's dev server with `TJ_DEV_API=signin` (docs/runbooks/try-real-sign-in.md). It forwards
   `/billing` and `/assets` to the API, so the app and its payment page share `http://localhost:5173`.
4. Sign up, open Plan & billing, press Subscribe, pay with `4242 4242 4242 4242` (any future date, security code `100`).
   Paddle's notification log must show `200`, and the page must say "Payment confirmed".
5. Then the rest of the lifecycle in the Paddle guide's section 7.2 (renewal and failed payment through Paddle's
   simulations, cancel, resume, the portal, a refund, deleting the account).

## Discount codes

**Making one (Operator tools > Codes > Promo codes).** Type a code (LAUNCH20), choose Type "Discount", the percent off and
for how long (the first payment, the first months, or every payment), and optionally how many people and the last day.
The code is saved first, then made at Paddle as a percentage off the Roundturn Pro product, usable at checkout with the same
code. The table says where Paddle's copy stands:

| It says | Meaning | Do |
|---|---|---|
| At the payment provider | Paddle has it | Nothing |
| … not live while billing is off | Paddle has it, but nobody can use a discount until billing is on | Nothing |
| Not at the payment provider yet | No provider on this server when it was made | It is pushed on the next Retry, once a provider is set |
| Sync failed (`reason`) | Paddle did not answer, or refused (`..._discount_code_conflict`: Paddle already has that code) | Retry. A copy Paddle made but never confirmed is found by its code and adopted, never made twice |

**Revoking one** stops it here at once, then archives Paddle's copy (a failed archive shows "Sync failed" and Retry). People
already subscribed with it keep their discount, as Paddle keeps it on their subscription.

**People** type a discount code in the same box as a free-days code (Settings > Plan & billing, or at sign-up). It is saved
on their account ("Saved: 20% off your first 3 months. It is applied when you subscribe."), shown beside Subscribe, put on
their next checkout, and forgotten once a subscription comes of that checkout. A newer code replaces an older one. With
billing off, a discount code is the one refusal ("That code did not work"). **Used** counts people who subscribed with it.
A discount over free days starts after them (Paddle applies a recurring discount after a trial).

## The webhook (`https://app.roundturn.app/webhooks/billing`)

The provider's only way in. No cookie and no CSRF (cross-site request forgery) token: the signature over the raw body is the
lock. Answers: **404** billing off; **415** not JSON; **413** over 64 KB; **401** signature missing, wrong or more than 5
seconds old (nothing is stored); **503** the database is away (the provider retries); **200** everything else, so the
provider stops retrying an event that was received, even one that was not applied.

Each event is stored once by the provider's id in `billing_events` (no name, email or card), with an `outcome`:

| Outcome | Meaning |
|---|---|
| `applied` | The subscription row now says what the event said |
| `stale` | Older than the last event applied for that subscription: kept, not applied |
| `no_account` | The subscription names an account that has been deleted: nothing is granted, and a live one is queued to be cancelled |
| `alert` | Stored, nothing granted, logged for a look: `unplaced` (no checkout of ours opened it), `mismatch` (the checkout and the provider's custom data name different people) or `second_subscription` (a second live subscription for one person: they are being charged twice) |
| `refund_recorded` | A refund or a chargeback: recorded, access unchanged (logged for a look) |
| `unknown_type` | A kind of event the app does not use |
| `reconciled` | Written by the nightly check when it corrected a row |

An event that needs a person to look at it is logged on the Web Service as `billing event needs a look: <what>`.

## The nightly check and the cancels (the Cron Job's `billing` step)

Part of `sweep all --apply` (hourly), and on its own as `python -m tradejournal.pg.sweep billing [--apply]`. It needs the
provider's settings on the Cron Job. It does two things:

- **Ends the subscriptions of deleted accounts.** Asking to delete stops the next renewal at once (signing in again within the
  7 days resumes it). When the account is actually deleted, the final cancel is queued in `billing_cancellations` and sent here
  until the provider confirms. With no provider configured the queue waits, and the log says so.
- **Compares every subscription that has not ended with the provider's own answer**, and corrects a difference (a missed
  webhook). The log line counts what was checked, corrected and failed; a failure makes the run end with an error, where Render
  shows it.

## The emails (billing on only)

- "Your Roundturn trial ends in 2 days" (the `billing-notices` step), once per trial, not to someone who will still have Pro
  when it ends (a subscription already started, a code, an override).
- "Your Roundturn trial has ended" (the `ended-notices` step: the "access ended" email, worded for a trial, with Subscribe).
- "Your Roundturn subscription has ended" (the `billing-notices` step), once, when a subscription stops giving Pro and
  nothing else does.

Each needs `TJ_EMAIL_API_KEY` and `TJ_EMAIL_FROM` on the Cron Job (already there for the "access ended" email).

## Support

| Someone says | Look | Do |
|---|---|---|
| "I paid and I'm still read-only" | Operator tools > the account > **Subscription**: is there one, and what do its latest events say? | No subscription and no event: the webhook did not arrive. The nightly check will not find it either (it only checks known subscriptions), so look the person up in the provider's dashboard and resend the event from there. An event marked `alert` (`unplaced` or `mismatch`): nothing was granted; compare the checkout in the provider's dashboard with the person before giving access by hand (an override) |
| "Cancel my subscription" (by email, as the Terms allow) | The same card | Cancel it in the provider's dashboard (at period end). The webhook updates the row, and the page shows "Ends …" |
| "I want a refund" | The billing and refund policy (`roundturn.app/refunds/`): payments are not refunded, except billing mistakes and legal rights | A refund is made in the provider's dashboard; Roundturn records it and changes no access. To end access too, cancel the subscription now in the dashboard |
| A chargeback | The log line `billing event needs a look: refund` | Recorded only. Decide with the provider; an operator override can end access if needed |
| (nobody says it) The log line `billing event needs a look: second_subscription` | The person has two live subscriptions and is charged twice; the first one is kept | Cancel and refund the second in the provider's dashboard |
| "Delete my account" with a subscription | Nothing to do | Deletion stops the renewal, and the final cancel follows the deletion |

## Trying it without money

- **The screens only:** `pnpm dev` in `frontend/` and open `/__dev/billing/<state>` (`trial`, `covered`, `active`,
  `cancelling`, `past_due`, `read_only`, `off`). The dev server answers billing itself; nothing reaches an API.
- **The whole lifecycle** runs in the tests with the fake provider: `tests/pg/test_pg_billing_app.py` (trial, subscribe, renew,
  a failed payment, grace, read-only, resubscribe, cancel, delete). The local Postgres must be running (`run-locally.md`).
- **The API locally** with `TJ_BILLING_ENABLED=true TJ_BILLING_PROVIDER=fake` shows the real answers, but the fake provider
  cannot be paid from a browser: there is no payment page until the Paddle adapter. Never run the sweep with `--apply`
  against the local development database (it holds a copy of a real journal).
