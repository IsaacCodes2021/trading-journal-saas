import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, setCsrfToken } from './client'

function mockFetch() {
  const seen: Request[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      seen.push(request)
      return new Response(JSON.stringify({}), { status: 200, headers: { 'content-type': 'application/json' } })
    }),
  )
  return seen
}

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('adds the CSRF token to unsafe methods only', async () => {
    const seen = mockFetch()
    setCsrfToken('tok-123')
    await api.GET('/api/v1/trades')
    await api.PUT('/api/v1/trades/{trade_id}/notes', { params: { path: { trade_id: 1 } }, body: { notes: 'x' } })
    expect(seen[0]?.headers.get('x-csrf-token')).toBeNull()
    expect(seen[1]?.method).toBe('PUT')
    expect(seen[1]?.headers.get('x-csrf-token')).toBe('tok-123')
  })

  it('sends money requests as JSON strings', async () => {
    const seen = mockFetch()
    setCsrfToken('t')
    await api.POST('/api/v1/ledger', { body: { amount: '12.50', kind: 'cost' } })
    expect(await seen[0]?.clone().text()).toContain('"amount":"12.50"')
  })

  it('redirects to sign-in on a 401', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, pathname: '/trades', search: '?preset=7d', assign })
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })))
    await api.GET('/api/v1/session')
    expect(assign).toHaveBeenCalledWith('/auth/login?next=%2Ftrades%3Fpreset%3D7d')
  })
})
