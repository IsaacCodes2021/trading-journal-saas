import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, setCsrfToken, unwrap } from './client'

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

  it('turns an error answer into an ApiError with the server’s words and its field messages', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              title: 'Unprocessable Content',
              status: 422,
              detail: 'The request is not valid.',
              errors: [{ field: 'timezone', message: 'Unknown timezone' }],
            }),
            { status: 422, headers: { 'content-type': 'application/problem+json' } },
          ),
      ),
    )
    const error = await unwrap(api.PATCH('/api/v1/settings', { body: { timezone: 'Mars/Base' } })).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 422,
      message: 'The request is not valid.',
      fields: { timezone: 'Unknown timezone' },
    })
  })

  it('gives a plain message for a server failure that has no detail, and never leaks anything else', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>boom</html>', { status: 500, headers: { 'content-type': 'text/html' } })),
    )
    const error = await unwrap(api.GET('/api/v1/session')).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).message).toBe('The server had a problem. Try again in a moment.')
    expect((error as ApiError).fields).toEqual({})
  })

  it('hands back the data of a good answer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ theme: 'dark' }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    )
    expect(await unwrap(api.GET('/api/v1/session'))).toEqual({ theme: 'dark' })
  })
})
