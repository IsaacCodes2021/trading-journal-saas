# Tradovate API access for automatic sync: a decision note

Written 2026-10-10 from public documentation and community posts. **Nothing here was confirmed with Tradovate or NinjaTrader**; the questions to put to them are at the end. No code was written and no account was created.

## The question

The main goal of the product is that a trader's fills arrive without them exporting a CSV file. What does Tradovate require for that, what does it cost, and is it a realistic next step for people who trade prop firm accounts (the people the beta is for)?

## Short answer

1. **There is a way that fits our rules, and it needs an application, not just a payment.** Tradovate's third-party login ("OAuth": the trader approves our app on Tradovate's own page, and we receive a token, never their password) is only for approved members of the NinjaTrader Ecosystem. Credentials for it are issued after a review. The application route is public in outline, its terms and fee (if any) are not.
2. **The route that needs no approval does not fit our rules.** A trader can buy an API key for themselves (a $25 a month add-on, a live account holding at least $1,000), but using it means giving the app their Tradovate user name and password, which the design forbids (principle P1 in doc 02 and decision 5 in the design README: never hold broker credentials).
3. **Prop firm accounts are the weak point.** The accounts most of our beta testers trade sit under the firm's own arrangement with Tradovate. Reports say the firm, not the trader, controls whether the API can reach them, and that evaluation accounts often do not show up through a trader's own key at all. If that is true, automatic sync would work for few of the people the beta is for, whatever we pay or build.
4. **So: do not build anything yet.** Spend a few emails first (list below). CSV import stays the way in, and the cheapest improvement for the main goal is to make that import faster, not to wait for an API.

## What it takes, by route

| | Personal API key | OAuth through the Ecosystem |
|---|---|---|
| Who holds credentials | The trader generates a key in Tradovate's settings; our app would need their login name and password to get a token | Tradovate issues our app an ID and a secret once; each trader approves us on Tradovate's page |
| Fits "no broker credentials held" | **No** | **Yes** |
| Cost to the trader | $25 a month add-on (some 2025 sources say $29.99), live account with a balance over $1,000, self-attestations | Reported: the same $25 a month (a forum reply gives that price for third-party OAuth access); unconfirmed whether the trader pays it or whether an approved vendor covers it |
| Cost to us | None | Unknown: no published fee for the Ecosystem application; one forum poster says partnership "involves fees"; another founder wrote to the Ecosystem address and had no answer |
| Approval | None | A formal application and review, quoted by a developer as coming from Tradovate's own email: credentials are "issued only to approved members of the NinjaTrader Ecosystem following a review process" |
| Works for prop firm accounts | Reported not to (the key is tied to the trader's own username; firm accounts sit under the firm) | Unknown; depends on whether the firm's accounts allow third-party apps |

Two more costs that are easy to miss:

- **Live market data is a separate agreement with CME** (reported at about $290 a month or more, payable to CME, as a "sub-vendor"). A journal does not need it: fills, orders and positions are account data, not market data. Real-time open profit and loss *would* need it, so that feature is out of scope.
- **Social login** (sign in with Google and the like) is not allowed for live accounts, so it is no shortcut.

## What the API can give a journal

From the public reference: the list of fills (`/fill/list`), orders (`order` entities), positions and their dependents, the account list, and live updates over a WebSocket. Cash balance history was not covered by the page that was read. Fills are what a journal needs; they would have to be paired into round turns the way the CSV import does it today (that code exists and is tested). Order placement is not needed and **must never be requested**: a read-only use keeps us out of the "automated order" rules entirely.

## Limits to design for

- Access tokens last about 90 minutes and are renewed by a call made about 15 minutes before expiry; a trader has at most two live sessions, and a third closes the oldest (so our sync must not use up a trader's session limit while they have a platform open).
- Limits are per user (5,000 requests an hour) and, on some endpoints, per IP address on a rolling hour. Examples: 5 failed token requests an hour, 15 renewals an hour, 300 sync requests an hour. A hosted app shares one address, so **all our users would share the endpoint limits**; Tradovate says higher limits are for established partners.
- A refusal can come back as a normal 200 answer carrying a `p-ticket` and a wait time (and sometimes `p-captcha`, which a third-party app cannot answer). Every response has to be checked for it.

## What this means for the design

- **It would amend a recorded decision.** The design (doc 02 P1, README decision 5) says "CSV upload only" and gives the same reason this note found: API access needs a live funded account and is not available for prop firm evaluation accounts (the README cites a NinjaTrader vendor-support article; that page did not load when this note was written, so it was not re-read). Adopting sync through OAuth would keep the spirit of P1, because we would hold a revocable token and never a password, but the rule and the decision are worded absolutely, so it needs a dated amendment and the owner's yes first.

- A sync job per person, rate-limited as one pool, with a visible "last synced" and a plain failure state. It would be a new background job and a new kind of stored secret (a refresh of the token per person), so it needs its own threat-model pass before any code.
- Tokens are credentials: encrypted at rest, deletable by the person, deleted with the account (the deletion sweep would need a step for it), never shown to operators.
- It is also a new data processor to name in the privacy policy, and the terms of use have to say what we do and do not do with the data.

## Options

| Option | Cost | Fit for prop traders | Verdict |
|---|---|---|---|
| A. Ecosystem application, then OAuth read-only sync | Unknown fee, weeks of review, a build | Unknown: depends on firms | Worth one email to find out; do not build before the answer |
| B. Personal API key with the trader's login | Low | Poor, and breaks our rule on credentials | **No** |
| C. Make CSV import faster (remember the last file's settings, a drop zone on the dashboard, a reminder after a trading day, import by email attachment) | Small, in our hands | Works for every account at every firm | **Recommended now** |
| D. Per-firm routes: some firms run on other platforms with their own APIs (TopstepX reportedly sells an add-on) | Per firm | Narrow | Revisit after the beta shows which firms people use (the sign-up form already asks) |
| E. A vendor that already holds an integration deal and forwards fills | A dependency and a fee | Unknown | Not worth it for a journal |

## What to ask, and of whom

To the Ecosystem (the address a developer reported using is info@ninjatraderecosystem.com; Tradovate's partner team is partners@tradovate.com for limits). Written from the product, not from us as individuals:

1. Is there an application for a read-only journaling app, what does it ask for, how long does review take, and is there a fee (one-time or monthly)?
2. If our app is approved, does each trader still need their own $25 add-on and a $1,000 live account?
3. Do approved apps reach accounts held under a prop firm's arrangement (for example Apex or the firms running evaluations on Tradovate)? If the firm must enable it, is there a list of firms that do?
4. Which scopes does the token carry, and can it be limited to reading fills and balances?
5. Do you provide per-app, not just per-IP, rate limits, and what happens to our other users when one trader exceeds theirs?
6. What must a privacy policy and terms of use say for an approved read-only app, and is a CME agreement needed for account data only (no quotes)?

## Where this came from, and how far to trust it

| Claim | Source | Confidence |
|---|---|---|
| Token lifetime, renewal, two sessions, social login not for live accounts, OAuth for other users | NinjaTrader's hosted API documentation (authentication page) | High (official) |
| Authorization and token addresses, no scopes or approval process described | The same documentation (OAuth page) | High that they are as described; the page is silent on approval |
| Rate limits and p-ticket behaviour | The Tradovate partner documentation (rate limits page) | High (official) |
| Credentials only for approved Ecosystem members after review | A developer relaying an email from Tradovate in a community thread (2026-07-02); another relaying support (2026-01-13) | Medium (secondhand, consistent) |
| $25 a month, $1,000 live account | Tradovate community staff-adjacent replies and several blogs | Medium (consistent, some conflict on the exact price; check the Add-Ons tab) |
| About $290 a month to CME for market data | A community thread quoting support | Medium (not needed for a journal) |
| Prop firm accounts are not reachable with a personal key | Community threads and a vendor blog that sells a competing bridge | Low to medium (anecdotal; vendor has an interest) |

The vendor blogs in the results sell competing products, so they were used only where an official page or a second source agreed.
