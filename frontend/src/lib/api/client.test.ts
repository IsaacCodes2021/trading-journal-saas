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
    await api.GET('/api/v1/session')
    await api.PATCH('/api/v1/settings', { body: { theme: 'light' } })
    expect(seen[0]?.headers.get('x-csrf-token')).toBeNull()
    expect(seen[1]?.method).toBe('PATCH')
    expect(seen[1]?.headers.get('x-csrf-token')).toBe('tok-123')
  })

  it('redirects to sign-in on a 401, keeping where the user was going', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, pathname: '/trades', search: '?preset=7d', assign })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 401 })),
    )
    await api.GET('/api/v1/session')
    expect(assign).toHaveBeenCalledWith('/auth/login?next=%2Ftrades%3Fpreset%3D7d')
  })
})
