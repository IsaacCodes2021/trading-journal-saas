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

export const SETTINGS = {
  timezone: 'America/Los_Angeles',
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
}

export function sessionBody(opts: ApiOptions = {}) {
  return {
    user: { id: '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11', email: 'alice@example.test' },
    settings: { ...SETTINGS, ...opts.settings, theme: opts.theme ?? 'dark' },
    plan: opts.plan ?? { effective: 'free', source: 'free', expires_at: null },
    entitlements: opts.entitlements ?? {},
    flags: { is_admin: false, mfa_fresh: false },
    csrf_token: 'tok',
    app_version: 'test',
  }
}

/** Replaces `fetch`. Returns the bodies of the theme saves it saw. */
export function stubApi(opts: ApiOptions = {}) {
  const saved: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      if (request.method === 'PATCH') {
        const body = (await request.clone().json()) as Record<string, string>
        saved.push(body)
        if (opts.saveProblem) return json(opts.saveProblem.body, opts.saveProblem.status)
        return opts.saveFails
          ? json({ title: 'Internal Server Error', status: 500, detail: null, errors: null }, 500)
          : json({ ...SETTINGS, ...opts.settings, ...body })
      }
      return json(sessionBody(opts))
    }),
  )
  return saved
}

export type AppPath =
  '/' | '/trades' | '/import' | '/invite' | '/settings' | `/trades/${string}` | `/settings/${string}`

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
