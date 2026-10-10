# The Roundturn site

The public marketing site for roundturn.app: static pages in `public/`, and one small Worker in `src/` that answers 404 for `/api/*`. No framework and no dependencies. The design is in `docs/design/02-architecture.md` (marketing site at the apex domain on Cloudflare; the app lives on `app.roundturn.app`).

**The site collects nothing.** The beta form is a page of the app (`https://app.roundturn.app/join`), because the server accepts it only from its own pages. This site links to it, to sign-in (`/signin`) and to sign-up with a code (`/signup`), and stores no data of its own.

It deploys as a **Cloudflare Worker with static assets**. (The design docs said "Cloudflare Pages". Cloudflare's dashboard now creates new projects as Workers, with the same CDN and the same domains, so the site follows that. Nothing in the design depended on the difference.)

```
public/            what is served: index, about, privacy, 404, style.css, fonts/, favicon, _headers
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

Payment providers will read this site, and so will users. Every claim has to be true of the product. If a feature changes, change the page in the same pull request; the tests fail if a required statement disappears or a promise such as "guaranteed" appears. The privacy page is a draft covering the beta form only. A full policy, reviewed by a professional, replaces it before the app opens to users.

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

Until 2026-10 the site collected addresses in a Cloudflare D1 database (`roundturn-waitlist`, a form on the home page, `POST /api/waitlist`). That form and its code are gone: the beta form replaced it, and the privacy page now describes the beta form. **The database itself is untouched** and still holds whoever joined; decide what to do with it:

- To invite those people, read the addresses in the dashboard (D1, `roundturn-waitlist`, Console: `SELECT email, created_at FROM waitlist`) and send them the link to `https://app.roundturn.app/join`, or make them invites by hand. Nothing has been imported.
- To remove them, delete the database in the dashboard.

The old privacy page promised those people one email when their invite was ready. Keep that in mind when deciding.
