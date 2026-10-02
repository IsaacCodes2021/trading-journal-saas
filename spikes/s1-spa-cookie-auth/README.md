# Spike S1: SPA and cookie auth against FastAPI

**Throwaway code.** It answers the S1 questions in `docs/design/05-features-and-roadmap.md` and is not merged into `main`; the conclusions are in `docs/design/06-decisions.md`. Run on 2026-10-02 on Windows 10, Node 24.21, Python 3.12.1, Microsoft Edge 154.

A FastAPI app serves a built Vite + React + TypeScript SPA and a cookie-authenticated JSON API on one origin, behind a fake OIDC provider, with the strict CSP from doc 04. Data is in memory; the "database" is three users and seven trades each.

## Run it

```powershell
# API tests (26)
backend\.venv\Scripts\python.exe -m pytest spikes\s1-spa-cookie-auth\api\tests

# Front end (from spikes\s1-spa-cookie-auth\web)
pnpm install
pnpm gen:api && pnpm typecheck && pnpm lint && pnpm test && pnpm build   # 16 unit tests

# End to end (8): start the API first, then drive the installed Edge
#   cd ..\api; ..\..\..\backend\.venv\Scripts\python.exe -m uvicorn main:app --port 8000
pnpm exec playwright test
```

## What was found

| Question | Answer | Evidence |
|---|---|---|
| Does the toolchain set work together? | **Mostly. TypeScript must be 6.0.x, not 7** | `typescript@7.0.2` ships only a native launcher, with no JavaScript compiler API: `openapi-typescript` crashes (`ts.factory` undefined) and `typescript-eslint` declares support only below 6.1. On 6.0.3 type check, lint, unit tests, build and codegen all pass |
| Are the peer warnings real? | Cosmetic for the rules used | `eslint-plugin-react` and `jsx-a11y` do not declare ESLint 10, yet planted violations (`react/no-danger`, `jsx-a11y/alt-text`, `no-explicit-any`) all fire |
| Do the supply-chain settings work? | Yes | `minimumReleaseAge: 1440` held TanStack Query at 5.104.0 instead of the 24-hour-old 5.104.1; `strictDepBuilds: true` with an empty allow-list: no dependency tried to run a build script |
| Does money stay a string end to end? | Yes, with `Money` | Plain `Decimal` generates `anyOf [number, string]` for requests (`Naive-Input` in the OpenAPI document); `Money` makes every money field `string` in both directions, rejects JSON numbers, and the generated TypeScript types agree |
| Generated types: request and response optionality | Two settings must be paired | `openapi-typescript` defaults make fields with defaults *required*, which forced callers to send `note`. Fix: `--default-non-nullable false` plus Pydantic `json_schema_serialization_defaults_required=True` (responses stay required, requests optional) |
| Does the `__Host-` cookie flow work? | Yes, on `http://localhost` in Edge | Sign in through the redirect dance, `next` preserved (including search params), rotation on every login, `HttpOnly` cookie invisible to `document.cookie`, nothing in `localStorage`, logout revokes server-side |
| Open redirect, state, replay, unverified email | All refused | pytest: `//evil`, `https://evil`, `/\evil`, `javascript:` all become `/`; wrong state 400; replayed code 400; unverified email 403 |
| CSRF (4 layers) incl. multipart | Holds | pytest matrix plus Edge: no token, wrong token, another session's token, foreign `Origin`, cross-site fetch-metadata, form-encoded body: all refused; multipart upload with the token works; oversize upload cut off with 413 |
| Isolation | Holds | Another user's id answers 404, never 403 (pytest and Edge) |
| Does a strict CSP survive? | **Yes, with two fixes** | No `unsafe-inline` or `unsafe-eval` anywhere. Vite output, Tailwind 4, React's `style` prop (CSSOM), ECharts and Tiptap all run clean after: (1) a plain-text ECharts tooltip formatter, because the default tooltip uses inline `style` attributes; (2) Tiptap `injectCSS: false` plus its few base rules in our stylesheet, because it injects a `<style>` element |
| ECharts tooltip safety | It is an XSS sink | `formatter` returns a string inserted as HTML; anything user-derived must go through `echarts.format.encodeHTML` |
| Hostile notes | Sanitized | `<script>`, `onerror`, `onload`, `javascript:` links, `style` removed by DOMPurify; the page title stayed clean. Side effect: DOMPurify *parsing* a `style` attribute logs a CSP violation even though nothing is applied, so notes should also be sanitized on write (the planned design) |
| Do CSP violation reports arrive? | **Not in this harness** | `report-uri`/`report-to` delivered nothing from the embedded browser even for a deliberate violation. The console and Playwright were the reliable signal; report delivery in real browsers is **unverified** |
| History fallback and caching | Works | Deep links return `index.html` with `no-cache`; hashed assets `public, max-age=31536000, immutable`; API paths never fall back; API responses `no-store` |
| Bundle size | Needs code splitting | Unsplit: 1,283 KB, **422 KB gzipped**. Split: entry 403 KB / **133 KB gz**; chart chunk 165 KB gz; editor chunk 124 KB gz. The dashboard needs entry plus chart, about **298 KB gz**, above the 250 KB target in doc 01 |
| Router | TanStack Router works | Typed search params; the compiler caught an optional-param bug; code-based routes, no friction. React Router not needed |
| `Intl.NumberFormat` and money strings | Exact in Node 24 | `'123456789012345678.99'` formats exactly (unit test); not separately tested in Edge beyond ordinary values |
| Playwright without a browser download | Works | `channel: 'msedge'` drives the installed Edge; 8 tests, including a control proving the CSP watcher catches a violation |

## Not tested

- Cloudflare in front, Render, real HTTPS, memory and latency (spike S4).
- A real identity provider: JWT and JWKS validation, the MFA claim, step-up (spike S1b). The fake provider issues claims directly.
- `img-src` and the presigned R2 redirect (spike S3).
- Firefox and Safari (cookie behavior of `__Host-` on localhost may differ).
- TanStack Table v8 versus v9 (decide when the trade log is built in M4).
- `report-uri` delivery in a real browser.
