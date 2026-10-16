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
  /**
   * Billing switched on, and where it stands (`billingBody` makes one). A list is answered in turn, the last one from then
   * on (a checkout the provider confirms after a few reads). Default: billing is off, and `GET /api/v1/billing` is a 404.
   */
  billing?: object | object[]
  /** Answer a billing action with this instead of what the fake provider would do. */
  billingAnswers?: Partial<Record<'checkout' | 'cancel' | 'resume' | 'portal', { status: number; body: object }>>
}

/** What `GET /api/v1/billing` says: on a trial with 5 days left, unless `changes` say otherwise. */
export function billingBody(changes: object = {}) {
  return {
    state: 'trial',
    price: '15.00',
    currency: 'USD',
    interval: 'month',
    price_text: 'USD 15 a month, plus applicable tax',
    ends_at: new Date(Date.now() + 4.5 * 86_400_000).toISOString(),
    renews_at: null,
    first_charge_pending: false,
    can_subscribe: true,
    billing_starts_at: new Date(Date.now() + 4.5 * 86_400_000).toISOString(),
    can_manage: false,
    reason: null,
    discount: null,
    ...changes,
  }
}

const NOT_FOUND = { title: 'Not Found', status: 404, detail: 'Not Found', errors: null }

export function sessionBody(opts: ApiOptions = {}) {
  return {
    user: { id: '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11', email: 'alice@example.test' },
    settings: { ...SETTINGS, ...opts.settings, theme: opts.theme ?? 'dark' },
    plan: opts.plan ?? { effective: 'free', source: 'free', expires_at: null },
    access: { state: 'active', ends_at: null, reason: null },
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
  const billingReads = Array.isArray(opts.billing) ? [...opts.billing] : opts.billing ? [opts.billing] : []
  let billing: Record<string, unknown> | undefined // what a cancel or a resume changed
  const billingNow = () =>
    billing ?? ((billingReads.length > 1 ? billingReads.shift() : billingReads[0]) as Record<string, unknown>)
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
      const path = new URL(request.url).pathname
      if (path === '/api/v1/billing' || path.startsWith('/api/v1/billing/')) {
        if (billingReads.length === 0) return json(NOT_FOUND, 404)
        const action = path.split('/')[4] as 'checkout' | 'cancel' | 'resume' | 'portal' | undefined
        if (request.method === 'GET' && action === undefined) return json(billingNow())
        const answer = action ? opts.billingAnswers?.[action] : undefined
        if (answer) return json(answer.body, answer.status)
        const now = billingNow()
        if (action === 'checkout') return json({ pay_url: '/billing/pay?_ptxn=txn_test_1' })
        if (action === 'portal') return json({ url: 'https://billing.example.test/portal/ctm_1' })
        if (action === 'cancel') billing = { ...now, state: 'cancelling', ends_at: now['renews_at'], renews_at: null }
        if (action === 'resume') billing = { ...now, state: 'active', renews_at: now['ends_at'], ends_at: null }
        return json(billing)
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
  | '/accounts'
  | '/calendar'
  | '/reports'
  | '/help'
  | '/import'
  | '/signin'
  | '/signup'
  | '/join'
  | '/invite'
  | '/welcome'
  | '/settings'
  | '/admin'
  | '/admin/beta'
  | '/admin/codes'
  | '/admin/rules'
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
