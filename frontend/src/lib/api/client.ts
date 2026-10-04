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

/** The API's problem document (RFC 9457), as far as the pages need it. */
export interface ProblemBody {
  title?: string
  status?: number
  detail?: string | null
  errors?: { field: string; message: string }[] | null
}

/** The id the server gave a request (`X-Request-ID`): shown only when it is a short plain token, so nothing odd is ever put on a page. */
const REQUEST_ID = /^[A-Za-z0-9._-]{8,64}$/

/** A request the server answered with an error. `message` is safe to show: it is the server's `detail`, or a plain fallback.
 * A server failure with no detail of its own also carries the request's reference, which the operator can look up in the log. */
export class ApiError extends Error {
  readonly status: number
  readonly fields: Record<string, string>
  readonly requestId: string | null

  constructor(status: number, problem?: ProblemBody, requestId?: string | null) {
    const reference = requestId && REQUEST_ID.test(requestId) ? requestId : null
    super(
      problem?.detail ||
        (status >= 500
          ? `The server had a problem. Try again in a moment.${reference ? ` If it keeps happening, tell us this reference: ${reference}.` : ''}`
          : 'The request was refused.'),
    )
    this.name = 'ApiError'
    this.status = status
    this.fields = Object.fromEntries((problem?.errors ?? []).map((e) => [e.field, e.message]))
    this.requestId = reference
  }
}

/** Turn an openapi-fetch result into its data, or throw an ApiError that says what the server said. */
export async function unwrap<T>(result: Promise<{ data?: T; error?: unknown; response?: Response }>): Promise<T> {
  const { data, error, response } = await result
  if (data === undefined) {
    throw new ApiError(
      response?.status ?? 0,
      typeof error === 'object' && error !== null ? (error as ProblemBody) : undefined,
      response?.headers.get('X-Request-ID'),
    )
  }
  return data
}
