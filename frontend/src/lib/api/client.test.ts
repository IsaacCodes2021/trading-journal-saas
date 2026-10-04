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

  const failWith = async (status: number, headers: Record<string, string>, body = '<html>boom</html>') => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(body, { status, headers })),
    )
    return (await unwrap(api.GET('/api/v1/session')).catch((e: unknown) => e)) as ApiError
  }

  it('adds the request reference to a server failure that has no detail, so it can be found in the log', async () => {
    const error = await failWith(500, {
      'content-type': 'text/html',
      'X-Request-ID': '0b6e2c1a-4f7d-4a55-9c1e-7d2f3a8b9e10',
    })
    expect(error.message).toBe(
      'The server had a problem. Try again in a moment. If it keeps happening, tell us this reference: 0b6e2c1a-4f7d-4a55-9c1e-7d2f3a8b9e10.',
    )
    expect(error.requestId).toBe('0b6e2c1a-4f7d-4a55-9c1e-7d2f3a8b9e10')
  })

  it('shows a reference only when it is a short plain token', async () => {
    for (const odd of [
      'short',
      'seven77',
      'has a space in it',
      'abcdefgh ijk',
      'bad prefix abcdefghij',
      '<b>nope</b>-1234',
      'a'.repeat(65),
      'semi;colon;1234567',
    ]) {
      const error = await failWith(500, { 'content-type': 'text/html', 'X-Request-ID': odd })
      expect(error.message).toBe('The server had a problem. Try again in a moment.')
      expect(error.requestId).toBeNull()
    }
    for (const fine of ['a'.repeat(8), 'b'.repeat(64)]) {
      const error = await failWith(503, { 'content-type': 'text/html', 'X-Request-ID': fine })
      expect(error.requestId).toBe(fine)
    }
  })

  it('keeps the server’s own sentence when it gave one, and adds no reference to a refusal', async () => {
    const problem = JSON.stringify({
      title: 'Service Unavailable',
      status: 503,
      detail: 'The operator tools are not set up.',
    })
    const withDetail = await failWith(
      503,
      { 'content-type': 'application/problem+json', 'X-Request-ID': 'edge-7f3a9c2b.1' },
      problem,
    )
    expect(withDetail.message).toBe('The operator tools are not set up.')
    const refused = await failWith(403, { 'content-type': 'text/html', 'X-Request-ID': 'edge-7f3a9c2b.1' })
    expect(refused.message).toBe('The request was refused.')
  })

  it('has no reference when the server sent none', async () => {
    expect((await failWith(500, { 'content-type': 'text/html' })).requestId).toBeNull()
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
