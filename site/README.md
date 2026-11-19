# The Roundturn site

The public marketing site for roundturn.app: static pages in `public/`, one small script (`public/popup.js`, the launch offer pop-up), and one small Worker in `src/` that answers 404 for `/api/*` except the pop-up's event counter. No framework and no dependencies. The design is in `docs/design/02-architecture.md` (marketing site at the apex domain on Cloudflare; the app lives on `app.roundturn.app`).

**The site collects nothing about people.** Sign-up (`https://app.roundturn.app/signup`) and sign-in (`/signin`) are pages of the app, and this site only links to them. The one thing it keeps is a tally of what visitors do with the launch offer pop-up (see below): the name of the event, never who did it.

It deploys as a **Cloudflare Worker with static assets**. (The design docs said "Cloudflare Pages". Cloudflare's dashboard now creates new projects as Workers, with the same CDN and the same domains, so the site follows that. Nothing in the design depended on the difference.)

```
public/            what is served: index, about, privacy, terms, refunds, docs/, 404, style.css, fonts/, favicon, _headers
public/docs/       the docs: how to export from each platform and import into Roundturn, with marked-up screenshots
public/fonts/      IBM Plex Sans and Mono (latin, five weights), the same files the app uses; the CSP allows only this folder
public/popup.js   the launch offer pop-up, the only script on the site (home page only)
src/worker.js      runs only for /api/*: answers 404, except POST /api/e/<event>, which writes one tally; everything else is served straight from public/
wrangler.jsonc     what Cloudflare reads at deploy: the Worker's name and the assets folder (the tally dataset comes back with the pop-up)
scripts/           popup-stats.mjs, which reads the tallies back from Cloudflare; shots.mjs, which makes the docs' screenshots
shots.json         the docs' screenshots: where each comes from, its crop, what is painted over, and its marks
test/              node:test checks, no dependencies
```

```bash
cd site
node --test "test/*.test.mjs"
```

CI runs the same command (job "Marketing site"). The tests check that nothing loads from another origin, nothing is inline (the CSP forbids it), every link resolves, the home page has no form of its own and links to the app's three entry points over https, the disclaimers are present, nothing promises what the product does not do, the privacy page matches what the code does (one script of the site's own, no cookies, no personal data stored here), the pop-up's behaviour (what is counted and how often), the stylesheet loads only fonts from `/fonts/`, the Worker's routing, and that `wrangler.jsonc` points at things that exist and binds no storage (and, while the pop-up is paused, no tally dataset).

## What the page says matters

Payment providers will read this site, and so will users. Every claim has to be true of the product. If a feature changes, change the page in the same pull request; the tests fail if a required statement disappears or a promise such as "guaranteed" appears. The privacy page, the terms and the billing and refund policy were written by us, not by a lawyer: have a professional read them. The home, terms and refunds pages must agree on the price and the trial (a test checks it), and a test fails if any page goes back to saying the beta is invite-only or that paid plans are not open yet.

## The docs pages and their screenshots

`public/docs/` holds the user docs: `/docs/`, `/docs/getting-started/`, `/docs/import/` with one guide per platform at `/docs/import/<platform>/` (lower case, no spaces), and the "Using Roundturn" pages (`/docs/accounts/`, `/docs/numbers/`, `/docs/journal/`, `/docs/your-data/`, `/docs/troubleshooting/`). Every docs page has the same side menu (a test checks it lists every docs page), and every page of the site links `/docs/` from its top menu and footer. The "Using Roundturn" pages are written from the app's Help page (`frontend/src/features/help/HelpPage.tsx`) and its screens: what they say must stay true of the app. Screenshots of the app come from its sample journal (`frontend-sample`, made-up data), never a real account. The app links to these addresses (`DOCS_IMPORT` and each platform's `guide` in `frontend/src/lib/platforms.ts`), and a test fails if one of them has no page. A new platform gets a page here in the same pull request that adds it to the app, and a card on `/docs/` and `/docs/import/`, and a line in every docs page's menu.

**Screenshots are made, not dropped in.** Raw screenshots stay outside the repository (they can show account numbers and balances), in a folder of their own, for example `Development/guide-screenshots/<platform>/`. `shots.json` says, for each image: the raw file, the part to keep, the rectangles to paint over (account numbers, names, balances), and the marks to draw. Then:

```bash
cd site
node scripts/shots.mjs --from ../../guide-screenshots
```

It crops and paints over in the pixels themselves, writes the PNG under `public/docs/`, and rewrites the image and its marks in the page between `<!-- shot:<id> -->` and `<!-- /shot:<id> -->` (the figure and its caption around them are written by hand). Coordinates are CSS pixels of the raw screenshot; `scale` is 2 for a capture at twice the size. A test fails if an image under `public/docs/` is not one of the script's outputs, or if a page's marks differ from `shots.json`, so nothing reaches the site without going through the crop and the paint.

**The marks** are an `<svg>` over the image, not part of it: boxes in percent, so they follow the image at any width, and numbered pins of a fixed size, so the numbers stay readable on a phone. The page needs no inline style (the CSP forbids it). The numbers match the caption under the image. A crossed-out red mark (`"tone": "no"`) says "not this one".

**What to look for before publishing a screenshot:** account numbers and names, balances, the trader's name or email, and anything else from a real account. Paint it over or crop it out. Roundturn's own screens are captured from a local test account with the test fixtures (`backend/tests/fixtures/`), whose account numbers are made up.

## The launch offer pop-up and its counts

**Switched on (2026-11-08), counts not kept yet.** The code `LAUNCH10` exists (operator tools, Codes; in Stripe it is coupon `rt-launch10`: 10% off Roundturn Pro for **2 months**, redeemable until 2027-01-01), and the pop-up's wording says 2 months to match; if the code changes, change the pop-up and `test/popup.test.mjs` in the same pull request. To switch it off again, put `data-paused` back on the `<dialog id="offer">` and, in the same pull request, say on the privacy page that it is switched off for now (a test fails while the two disagree). The tally dataset is still out of `wrangler.jsonc`: it was taken out on 2026-11-06 because the first deploy with it failed in Workers Builds. To keep the counts, read that build's log in the Cloudflare dashboard, set up Workers Analytics Engine there if the log asks for it, then put `"analytics_engine_datasets": [{ "binding": "POPUP_EVENTS", "dataset": "roundturn_popup" }]` back and change its check in `test/worker.test.mjs`. Until then the Worker answers `POST /api/e/<name>` and records nothing.

`public/popup.js` shows the offer (`<dialog id="offer">` on the home page) once per visit, 8 seconds after the page opens. It sends the name of what the visitor did to `POST /api/e/<name>`, where the Worker writes one tally to the Analytics Engine dataset `roundturn_popup`: `shown`, then at most one of `x` (the X), `away` (a click outside it) or `esc` (the Escape key); `copy` (Copy link) and `signup` (Sign up now) can come on top. Nothing else is sent or read: no cookie, IP address, browser details or referrer. The only thing kept in the browser is a note in session storage that the pop-up was shown, so it does not appear twice in one visit. The privacy page says all of this, and a test pins it.

**Reading the numbers** needs two values that never go in the chat or in git: your Cloudflare account id and an API token with the single permission *Account Analytics: Read* (dashboard, My Profile, API Tokens). Put them in a git-ignored file, e.g. `site/.env.cloudflare` (`CLOUDFLARE_ACCOUNT_ID=...`, `CLOUDFLARE_API_TOKEN=...`), then:

```bash
cd site
npm run popup-stats -- --env-file .env.cloudflare --days 30
```

It prints how many were shown, how many left (X, click away, Escape) and how many went on (Copy link, Sign up now), as shares of those shown. Counts can be a little off: anyone can send a tally, and Cloudflare may sample at very high volumes. The offer's code (`LAUNCH10`, 10% off for the first 3 months) must exist in the app's operator tools before this goes live: until it does, Sign up now ends on an error about the promo code.

## Deploying on Cloudflare (the owner's steps)

Done once, 2026-10-03; kept here for the next time.

1. **The project.** Workers & Pages, Create application, connect the GitHub repository `trading-journal-saas` (authorize Cloudflare's GitHub app for **only this repository**). Fill in the form like this:
   - Project name: `roundturn` (it must match `name` in `wrangler.jsonc`)
   - Build command: leave empty
   - Deploy command: `npx wrangler deploy`
   - Advanced settings, Path (the root directory): `site`
   - Enable Preview builds: off
   - Protect with Cloudflare Access: off
   - API token: Create new token (Cloudflare's own flow; do not paste it anywhere)
2. **The domain.** The Worker's Settings, Domains & Routes, Add, Custom domain: `roundturn.app` (leave the subdomain empty; Production). The domain is registered at Cloudflare, so the DNS records are created for you. `www` is not served for now: it would need its own record and a redirect to the apex, so there is one copy of the site. `wrangler.jsonc` turns the `workers.dev` and preview addresses off; do not turn them on again in the dashboard, because the next deploy follows the file.
   Also: SSL/TLS, Edge Certificates, **Always Use HTTPS** on, so plain `http://` requests are redirected.
3. **The contact address.** Email Routing for `hello@roundturn.app` (and `payments@`) forwarding to your inbox, verified. Done 2026-10-03.

## The old waitlist

Until 2026-10 the site collected addresses in a Cloudflare D1 database (`roundturn-waitlist`, a form on the home page, `POST /api/waitlist`). That form and its code are gone: the beta form replaced it, and the privacy page describes the beta form. **The database was deleted on 2026-10-10.** It held one entry, made by a person the owner had asked to try the form; nobody was invited from it and nothing was imported anywhere, so the site holds no waitlist data. Nothing in this folder binds a database (a test checks that); the only binding is the pop-up's tally dataset.
