# The Roundturn site

The public marketing site for roundturn.app: static pages in `public/`, one small script (`public/popup.js`, the launch offer pop-up), and one small Worker in `src/` that answers 404 for `/api/*` except the pop-up's event counter. No framework and no dependencies. The design is in `docs/design/02-architecture.md` (marketing site at the apex domain on Cloudflare; the app lives on `app.roundturn.app`).

**The site collects nothing about people.** Sign-up (`https://app.roundturn.app/signup`) and sign-in (`/signin`) are pages of the app, and this site only links to them. The one thing it keeps is a tally of what visitors do with the launch offer pop-up (see below): the name of the event, never who did it.

It deploys as a **Cloudflare Worker with static assets**. (The design docs said "Cloudflare Pages". Cloudflare's dashboard now creates new projects as Workers, with the same CDN and the same domains, so the site follows that. Nothing in the design depended on the difference.)

```
public/            what is served: index, about, privacy, terms, refunds, 404, style.css, fonts/, favicon, _headers
public/fonts/      IBM Plex Sans and Mono (latin, five weights), the same files the app uses; the CSP allows only this folder
public/popup.js   the launch offer pop-up, the only script on the site (home page only)
src/worker.js      runs only for /api/*: answers 404, except POST /api/e/<event>, which writes one tally; everything else is served straight from public/
wrangler.jsonc     what Cloudflare reads at deploy: the Worker's name, the assets folder and one Analytics Engine dataset for the tallies
scripts/           popup-stats.mjs, which reads the tallies back from Cloudflare
test/              node:test checks, no dependencies
```

```bash
cd site
node --test "test/*.test.mjs"
```

CI runs the same command (job "Marketing site"). The tests check that nothing loads from another origin, nothing is inline (the CSP forbids it), every link resolves, the home page has no form of its own and links to the app's three entry points over https, the disclaimers are present, nothing promises what the product does not do, the privacy page matches what the code does (one script of the site's own, no cookies, no personal data stored here), the pop-up's behaviour (what is counted and how often), the stylesheet loads only fonts from `/fonts/`, the Worker's routing, and that `wrangler.jsonc` points at things that exist and binds only the tally dataset.

## What the page says matters

Payment providers will read this site, and so will users. Every claim has to be true of the product. If a feature changes, change the page in the same pull request; the tests fail if a required statement disappears or a promise such as "guaranteed" appears. The privacy page, the terms and the billing and refund policy were written by us, not by a lawyer: have a professional read them. The home, terms and refunds pages must agree on the price and the trial (a test checks it), and a test fails if any page goes back to saying the beta is invite-only or that paid plans are not open yet.

## The launch offer pop-up and its counts

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
