import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './schema'

let csrfToken = ''

/** Set from GET /api/v1/session; held in memory only, never in storage. */
export function setCsrfToken(token: string): void {
  csrfToken = token
}

export function csrfHeader(): Record<string, string> {
  return { 'X-CSRF-Token': csrfToken }
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

const middleware: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method)) {
      request.headers.set('X-CSRF-Token', csrfToken)
    }
    return request
  },
  onResponse({ response }) {
    if (response.status === 401) {
      const next = encodeURIComponent(window.location.pathname + window.location.search)
      window.location.assign(`/auth/login?next=${next}`)
    }
    return undefined
  },
}

// `fetch` is looked up at call time, not captured at import time, so tests (and later MSW) can replace it.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
})
api.use(middleware)

/** Turn an openapi-fetch result into its data, or throw. */
export async function unwrap<T>(result: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data } = await result
  if (data === undefined) throw new Error('request failed')
  return data
}
