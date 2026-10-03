import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

it('reads the date range from the URL and shows the signed-in user', async () => {
  window.history.pushState({}, '', '/trades?preset=7d')
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      json({
        user: { id: '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11', email: 'alice@example.test' },
        settings: {
          timezone: 'America/Los_Angeles',
          trading_day_timezone: 'America/New_York',
          trading_day_start: '18:00',
          theme: 'dark',
        },
        plan: { effective: 'free', source: 'free', expires_at: null },
        entitlements: {},
        flags: { is_admin: false, mfa_fresh: false },
        csrf_token: 'tok',
        app_version: 'test',
      }),
    ),
  )

  const { router } = await import('./routes')
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )

  expect(await screen.findByTestId('preset')).toHaveTextContent('Range: 7d') // the filter came from the URL
  expect(screen.getByTestId('who')).toHaveTextContent('alice@example.test')
})
