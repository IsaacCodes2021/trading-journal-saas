# The Roundturn site

The public marketing site and waitlist for roundturn.app: static pages in `public/`, and one Cloudflare Pages Function in `functions/api/waitlist.js`. No framework and no dependencies. The design is in `docs/design/02-architecture.md` (marketing site on Cloudflare Pages at the apex domain; the app lives on `app.roundturn.app` later).

```
public/            what is served: index, about, privacy, thanks, 404, style.css, waitlist.js, favicon, _headers
functions/api/     POST /api/waitlist: stores an email address and the date, in a D1 database bound as DB
test/              node:test checks, no dependencies
```

```bash
cd site
node --test "test/*.test.mjs"
```

CI runs the same command (job "Marketing site"). The tests check that nothing loads from another origin, nothing is inline (the CSP forbids it), every link resolves, the disclaimers are present, nothing promises what the product does not do, the privacy page matches the code (no cookies, no stored IP address, no logging), and the function's behavior (validation, duplicates, honeypot, cross-site refusal, size limits, failures that leak nothing).

## What the page says matters

Payment providers will read this site, and so will users. Every claim has to be true of the product. If a feature changes, change the page in the same pull request; the tests fail if a required statement disappears or a promise such as "guaranteed" appears. The privacy page is a draft covering the waitlist only. A full policy, reviewed by a professional, replaces it before the app opens to users.

## Deploying on Cloudflare Pages (the owner's steps)

1. **Connect the repository.** Cloudflare dashboard, Workers & Pages, Create, Pages, Connect to Git. Authorize Cloudflare's GitHub app for **only this repository** (it is private) and pick `trading-journal-saas`.
2. **Build settings.** Production branch `main`. Framework preset None. Build command: leave empty. Build output directory: `public`. Root directory (under Advanced): `site`.
3. **The database.** After the first deploy: the project's Settings, Bindings, add a **D1 database** binding. Variable name `DB`. Create a database called `roundturn-waitlist`. The function creates its own table on first use. Redeploy once so the binding takes effect.
4. **The domain.** The project's Custom domains: add `roundturn.app` and `www.roundturn.app`. The domain is registered at Cloudflare, so the DNS records are created for you.
5. **The contact address.** The site says `hello@roundturn.app`. Cloudflare dashboard, roundturn.app, Email, Email Routing: enable it and forward `hello@` (and later `payments@`) to your own inbox. Do this before publishing, or the address on the page will bounce.
6. **Abuse limit.** Security, WAF, Rate limiting rules: a rule for requests to `/api/waitlist` (POST), a handful per minute per IP, action Block. The function already refuses cross-site posts and honeypot fills; this adds a cap on volume.
7. **Check it.** Open the `*.pages.dev` address, submit your own address, and confirm a row appears in the D1 console (`SELECT * FROM waitlist`). Then submit it again and confirm there is still one row.

## Things you take on by publishing it

- **Removal requests.** The privacy page promises that an address is deleted when its owner asks by email. In the D1 console: `DELETE FROM waitlist WHERE email = 'address@example.com'`.
- **The invitation email.** The page promises one email when an invite is ready. After you send invitations, delete those addresses from the list.
- **The list is personal data.** Do not export it to other tools or share it.
