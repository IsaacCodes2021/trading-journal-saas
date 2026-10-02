import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

it('reads filters from the URL, calls the typed API with them, and renders money from strings', async () => {
  window.history.pushState({}, '', '/trades?preset=7d&direction=long')
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      calls.push(url.pathname + url.search)
      if (url.pathname === '/api/v1/session') {
        return json({
          user: { id: 1, email: 'alice@example.test', display_name: 'Alice' },
          settings: { timezone: 'America/Los_Angeles', theme: 'dark' },
          flags: { is_admin: false, mfa_fresh: false },
          csrf_token: 'tok',
          app_version: 't',
        })
      }
      return json({
        items: [
          { id: 7, symbol: 'NQ', direction: 'long', trade_date: '2026-10-01', net_pnl: '125.50', fees: '4.12', notes: '' },
        ],
        total: 1,
      })
    }),
  )

  const { router } = await import('./routes')
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )

  expect(await screen.findByRole('link', { name: 'NQ' })).toBeInTheDocument()
  expect(screen.getByText('$125.50')).toBeInTheDocument()
  expect(screen.getByTestId('who')).toHaveTextContent('alice@example.test')
  expect(calls).toContain('/api/v1/trades?preset=7d&direction=long')
})
