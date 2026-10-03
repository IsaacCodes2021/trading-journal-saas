# Front end

React + TypeScript single-page app, built with Vite and served by the API container on one origin
([ADR-010](../docs/design/06-decisions.md), [doc 07](../docs/design/07-frontend-and-api.md)). This is the
**scaffold**: a working shell and the tooling, with placeholder pages. The real pages are rebuilt in milestone M4.

## Commands

Needs Node 24 (`.nvmrc`) and pnpm 12.8.1 (`packageManager` in `package.json`).

```powershell
pnpm install --frozen-lockfile
pnpm dev            # Vite dev server; /api, /auth, /files are proxied to the API on :8000
pnpm lint           # ESLint 10 + typescript-eslint + react-hooks + jsx-a11y
pnpm typecheck      # tsc --noEmit (TypeScript 6.0.x, strict)
pnpm test           # Vitest + React Testing Library
pnpm build          # typecheck, then vite build
pnpm check:bundle   # fails if the entry chunk is over 150 KB gzipped
pnpm format         # Prettier
```

## Lessons from spike S1 that are built in

- **TypeScript is pinned to 6.0.x.** 7.x has no JavaScript compiler API, so `openapi-typescript` and
  `typescript-eslint` cannot run on it. Revisit when both support 7.
- **Supply chain** (`pnpm-workspace.yaml`): versions published in the last 24 hours are not installed, and any
  dependency build script fails the install until it is reviewed and allow-listed.
- **Money is a string** end to end. `src/lib/money.ts` formats it exactly and is the _only_ place a money string
  becomes a number (for charts). Never do arithmetic on `number`.
- **Raw HTML only through `SafeHtml`** (DOMPurify, same allow-list as the server). ESLint forbids
  `dangerouslySetInnerHTML` everywhere else. A chart library tooltip that returns HTML is the same kind of sink:
  escape user-derived text.
- **Strict CSP** (no `unsafe-inline`, no `unsafe-eval`). When ECharts and Tiptap arrive (M4): use a plain-text
  ECharts tooltip `formatter`, and Tiptap with `injectCSS: false` (put its base rules in `styles.css`).
- **Lazy-load heavy libraries** (chart, editor). Budget: 150 KB gzipped for the entry chunk, 300 KB for the landing
  route (doc 01). `pnpm check:bundle` enforces the first.
- **Filters live in the URL** (typed search params in `routes.tsx`), not in a global store.
- `fetch` is resolved at call time in `src/lib/api/client.ts` so tests can replace it.

## The API types are a placeholder

`src/lib/api/schema.d.ts` is hand-written to the session and settings contract until the API exists (M2). Then:

1. The API writes `backend/openapi.json`.
2. `pnpm gen:api` regenerates `schema.d.ts` with `--default-non-nullable false` (pair it with Pydantic
   `json_schema_serialization_defaults_required=True`; see doc 07 section 2).
3. CI regenerates and **fails on any diff**, so the API and the front end cannot drift.

## Conventions (for someone new to TypeScript)

Strict mode on; `any` is a lint error; types come from the generated schema, never hand-copied; one pattern per
concern (a query hook and a mutation hook per endpoint, one form pattern, one table pattern, one chart wrapper);
small components. See the spike's `routes.tsx` for the patterns in use
(`spikes/s1-spa-cookie-auth` on the branch `spike/s1-spa-cookie-auth`).

## Not here yet

End-to-end tests (Playwright driving the installed Edge, with a CSP-violation watcher and a control test; added
with the API in M5), ECharts, Tiptap, forms (React Hook Form + Zod), TanStack Table (v8 or v9: decide in M4).
