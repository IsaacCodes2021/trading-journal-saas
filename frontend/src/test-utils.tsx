import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryHistory, RouterProvider } from '@tanstack/react-router'
import { render } from '@testing-library/react'
import { vi } from 'vitest'
import { createAppRouter } from './routes'

/*
 * Test helpers only: nothing imports this from the app, so it is never part of the build. `stubApi` stands in for the
 * backend's session and settings calls; `openApp` renders the whole app (shell, router, pages) at a URL.
 */

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

export const SETTINGS: {
  timezone: string
  csv_timezone: string | null
  trading_day_timezone: string
  trading_day_start: string
  theme: string
} = {
  timezone: 'America/Los_Angeles',
  csv_timezone: null,
  trading_day_timezone: 'America/New_York',
  trading_day_start: '18:00',
  theme: 'dark',
}

export interface ApiOptions {
  plan?: object
  entitlements?: Record<string, number | boolean>
  theme?: string
  saveFails?: boolean
  /** Answer every settings save with this problem document instead of saving. */
  saveProblem?: { status: number; body: object }
  settings?: Partial<typeof SETTINGS>
  /** Whether the person signed in again within the last ten minutes (needed to delete the account). Default: yes. */
  recentlyAuthenticated?: boolean
  /** The person is an operator (default: no), and confirmed two-step verification in the last 15 minutes (default: yes). */
  operator?: boolean
  mfaFresh?: boolean
  /** Answer a request to delete the account with this instead of scheduling it for 2026-10-11. */
  deletion?: { status: number; body: object }
  /** The recent-activity list `GET /api/v1/activity` answers with, newest first. Default: none yet. */
  activity?: object[]
  /** Answer redeeming a plan code with this instead of putting the person on Pro until 2027-01-12. */
  redeem?: { status: number; body: object }
}

export function sessionBody(opts: ApiOptions = {}) {
  return {
    user: { id: '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11', email: 'alice@example.test' },
    settings: { ...SETTINGS, ...opts.settings, theme: opts.theme ?? 'dark' },
    plan: opts.plan ?? { effective: 'free', source: 'free', expires_at: null },
    entitlements: opts.entitlements ?? {},
    flags: {
      is_admin: opts.operator ?? false,
      mfa_fresh: opts.mfaFresh ?? false,
      recently_authenticated: opts.recentlyAuthenticated ?? true,
    },
    csrf_token: 'tok',
    app_version: 'test',
  }
}

/** Replaces `fetch`. Returns the bodies of the theme saves it saw. */
export function stubApi(opts: ApiOptions = {}) {
  const saved: unknown[] = []
  let plan: object | undefined = opts.plan // a redeemed code changes the plan the session reports from then on
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      if (request.method === 'POST' && new URL(request.url).pathname === '/api/v1/account/deletion') {
        if (opts.deletion) return json(opts.deletion.body, opts.deletion.status)
        return json({ scheduled_for: '2026-10-11T03:20:00Z' })
      }
      if (request.method === 'POST' && new URL(request.url).pathname === '/api/v1/grants/redeem') {
        if (opts.redeem) return json(opts.redeem.body, opts.redeem.status)
        plan = { effective: 'pro', source: 'grant', expires_at: '2027-01-12T09:30:00Z' }
        return json(plan)
      }
      if (request.method === 'GET' && new URL(request.url).pathname === '/api/v1/activity') {
        return json({ events: opts.activity ?? [] })
      }
      if (request.method === 'PATCH') {
        const body = (await request.clone().json()) as Record<string, string>
        saved.push(body)
        if (opts.saveProblem) return json(opts.saveProblem.body, opts.saveProblem.status)
        if (opts.saveFails) {
          return json({ title: 'Internal Server Error', status: 500, detail: null, errors: null }, 500)
        }
        const merged = { ...SETTINGS, ...opts.settings, ...body }
        if (body['csv_timezone'] === '') merged.csv_timezone = null // "" goes back to following the own timezone
        return json(merged)
      }
      return json(sessionBody({ ...opts, ...(plan ? { plan } : {}) }))
    }),
  )
  return saved
}

export type AppPath =
  | '/'
  | '/trades'
  | '/calendar'
  | '/import'
  | '/invite'
  | '/welcome'
  | '/settings'
  | '/admin'
  | '/admin/codes'
  | `/trades/${string}`
  | `/day/${string}`
  | `/settings/${string}`

export function openApp(to: AppPath, search: Record<string, string | number> = {}) {
  const query = new URLSearchParams(Object.entries(search).map(([key, value]) => [key, String(value)])).toString()
  const router = createAppRouter(createMemoryHistory({ initialEntries: [query ? `${to}?${query}` : to] }))
  const view = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...view, router }
}
