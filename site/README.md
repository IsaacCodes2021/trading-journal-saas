# The Roundturn site

The public marketing site for roundturn.app: static pages in `public/`, and one small Worker in `src/` that answers 404 for `/api/*`. No framework and no dependencies. The design is in `docs/design/02-architecture.md` (marketing site at the apex domain on Cloudflare; the app lives on `app.roundturn.app`).

**The site collects nothing.** The beta form is a page of the app (`https://app.roundturn.app/join`), because the server accepts it only from its own pages. This site links to it, to sign-in (`/signin`) and to sign-up with a code (`/signup`), and stores no data of its own.

It deploys as a **Cloudflare Worker with static assets**. (The design docs said "Cloudflare Pages". Cloudflare's dashboard now creates new projects as Workers, with the same CDN and the same domains, so the site follows that. Nothing in the design depended on the difference.)

```
public/            what is served: index, about, privacy, terms, refunds, 404, style.css, fonts/, favicon, _headers
public/fonts/      IBM Plex Sans and Mono (latin, five weights), the same files the app uses; the CSP allows only this folder
src/worker.js      runs only for /api/*, to answer 404; everything else is served straight from public/
wrangler.jsonc     what Cloudflare reads at deploy: the Worker's name and the assets folder (no bindings)
test/              node:test checks, no dependencies
```

```bash
cd site
node --test "test/*.test.mjs"
```

CI runs the same command (job "Marketing site"). The tests check that nothing loads from another origin, nothing is inline (the CSP forbids it), every link resolves, the home page has no form of its own and links to the app's three entry points over https, the disclaimers are present, nothing promises what the product does not do, the privacy page matches what the code does (no scripts, no cookies, nothing stored here), the stylesheet loads only fonts from `/fonts/`, the Worker's routing, and that `wrangler.jsonc` points at things that exist and binds nothing.

## What the page says matters

Payment providers will read this site, and so will users. Every claim has to be true of the product. If a feature changes, change the page in the same pull request; the tests fail if a required statement disappears or a promise such as "guaranteed" appears. The privacy page, the terms and the billing and refund policy are drafts (each says so at the top). A professional should review them before the app opens widely; then remove the draft notice. When billing goes live, change "Paid plans are not open yet" on the home, terms and refunds pages together (a test checks they agree on the price, the trial and that wording).

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

Until 2026-10 the site collected addresses in a Cloudflare D1 database (`roundturn-waitlist`, a form on the home page, `POST /api/waitlist`). That form and its code are gone: the beta form replaced it, and the privacy page describes the beta form. **The database was deleted on 2026-10-10.** It held one entry, made by a person the owner had asked to try the form; nobody was invited from it and nothing was imported anywhere, so the site holds no waitlist data. Nothing in this folder binds a database (a test checks that).
