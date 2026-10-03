# The Roundturn site

The public marketing site and waitlist for roundturn.app: static pages in `public/`, and one small Worker in `src/` for the waitlist API. No framework and no dependencies. The design is in `docs/design/02-architecture.md` (marketing site at the apex domain on Cloudflare; the app lives on `app.roundturn.app` later).

It deploys as a **Cloudflare Worker with static assets**. (The design docs said "Cloudflare Pages". Cloudflare's dashboard now creates new projects as Workers, with the same CDN and the same domains, so the site follows that. Nothing in the design depended on the difference.)

```
public/            what is served: index, about, privacy, thanks, 404, style.css, waitlist.js, favicon, _headers
src/worker.js      runs only for /api/*; everything else is served straight from public/
src/waitlist.js    POST /api/waitlist: stores an email address and the date, in a D1 database bound as DB
wrangler.jsonc     what Cloudflare reads at deploy: the Worker's name, the assets folder, the D1 binding
test/              node:test checks, no dependencies
```

```bash
cd site
node --test "test/*.test.mjs"
```

CI runs the same command (job "Marketing site"). The tests check that nothing loads from another origin, nothing is inline (the CSP forbids it), every link resolves, the disclaimers are present, nothing promises what the product does not do, the privacy page matches the code (no cookies, no stored IP address, no logging), the handler's behavior (validation, duplicates, honeypot, cross-site refusal, size limits, failures that leak nothing), the Worker's routing, and that `wrangler.jsonc` points at things that exist and holds a real database id.
## What the page says matters

Payment providers will read this site, and so will users. Every claim has to be true of the product. If a feature changes, change the page in the same pull request; the tests fail if a required statement disappears or a promise such as "guaranteed" appears. The privacy page is a draft covering the waitlist only. A full policy, reviewed by a professional, replaces it before the app opens to users.

## Deploying on Cloudflare (the owner's steps)

1. **The database.** Cloudflare dashboard, Storage and databases, D1, Create database. Name it `roundturn-waitlist`. Copy its **Database ID** (a UUID; it is an identifier, not a secret) and put it in `wrangler.jsonc` in place of `REPLACE_WITH_THE_D1_DATABASE_ID`, in a pull request. The code creates the table itself on first use.
2. **The project.** Workers & Pages, Create application, connect the GitHub repository `trading-journal-saas` (authorize Cloudflare's GitHub app for **only this repository**). Fill in the form like this:
   - Project name: `roundturn` (it must match `name` in `wrangler.jsonc`)
   - Build command: leave empty
   - Deploy command: `npx wrangler deploy`
   - Advanced settings, Path (the root directory): `site`
   - Enable Preview builds: off. A preview build would write to the same waitlist database.
   - Protect with Cloudflare Access: off
   - API token: Create new token (Cloudflare's own flow; do not paste it anywhere)
   Press Deploy only after the pull request with the database id is merged, because the build reads `main`.
3. **The domain.** The Worker's Settings, Domains & Routes, Add, Custom domain: `roundturn.app`, then `www.roundturn.app`. The domain is registered at Cloudflare, so the DNS records are created for you.
4. **The contact address.** Email Routing for `hello@roundturn.app` (and `payments@`) forwarding to your inbox, verified. Done 2026-10-03.
5. **Abuse limit.** Security, WAF, Rate limiting rules: a rule for POST requests to `/api/waitlist`, a handful per minute per IP, action Block. The handler already refuses cross-site posts and honeypot fills; this caps volume.
6. **Check it.** Open the `roundturn.workers.dev` address, submit your own address, and confirm a row appears (D1, the database's Console tab: `SELECT * FROM waitlist`). Submit it again and confirm there is still one row.

## Things you take on by publishing it

- **Removal requests.** The privacy page promises that an address is deleted when its owner asks by email. In the D1 console: `DELETE FROM waitlist WHERE email = 'address@example.com'`.
- **The invitation email.** The page promises one email when an invite is ready. After you send invitations, delete those addresses from the list.
- **The list is personal data.** Do not export it to other tools or share it.