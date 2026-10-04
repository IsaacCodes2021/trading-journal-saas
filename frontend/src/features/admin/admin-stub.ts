import { vi } from 'vitest'
import type { AdminUser, AdminUserDetail } from '../../lib/admin'
import { json, stubApi, type ApiOptions } from '../../test-utils'

/** Test helpers for the operator screens: an in-memory `/api/admin/v1` that behaves like the server's, and what it was asked. */

export const SELF = '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11' // the signed-in person's own id, as the session stub gives it

export const user = (id: string, email: string, over: Partial<AdminUser> = {}): AdminUser => ({
  id,
  email,
  status: 'active',
  is_admin: false,
  created_at: '2026-08-14T09:00:00Z',
  last_login_at: '2026-10-01T19:40:00Z',
  deletion_scheduled_at: null,
  plan: { effective: 'free', source: 'free', expires_at: null },
  usage: { accounts: 2, trades: 1204, screenshots: 3, storage_bytes: 38 * 1024 * 1024, last_import_at: null },
  ...over,
})

export const detail = (u: AdminUser, over: Partial<AdminUserDetail> = {}): AdminUserDetail => ({
  user: u,
  active_sessions: 2,
  overrides: [],
  redemptions: [],
  events: [],
  ...over,
})

export interface Call {
  method: string
  path: string
  query: Record<string, string>
  body: unknown
  csrf: string | null
}

export interface AdminStubOptions extends ApiOptions {
  users?: AdminUser[]
  details?: Record<string, AdminUserDetail>
  /** Answer every change with this problem instead of making it. */
  changeFails?: { status: number; detail: string }
  /** Answer the list with this problem. */
  listFails?: { status: number; detail: string }
  /** Answer a look at one account with this problem. */
  detailFails?: { status: number; detail: string }
}

/** Replaces `fetch`: the app's usual calls (as an operator who just verified, unless told otherwise) plus the operator API. */
export function stubAdmin(options: AdminStubOptions = {}) {
  const { users = [], details = {}, changeFails, listFails, detailFails, ...api } = options
  stubApi({ operator: true, mfaFresh: true, ...api })
  const inner = globalThis.fetch
  const calls: Call[] = []
  const problem = (p: { status: number; detail: string }) =>
    json({ title: 'Problem', status: p.status, detail: p.detail, errors: null }, p.status)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      if (!url.pathname.startsWith('/api/admin/v1')) return inner(request)
      const body = request.method === 'GET' ? null : await request.clone().json()
      calls.push({
        method: request.method,
        path: url.pathname,
        query: Object.fromEntries(url.searchParams),
        body,
        csrf: request.headers.get('x-csrf-token'),
      })
      if (url.pathname === '/api/admin/v1/users') {
        if (listFails) return problem(listFails)
        const q = (url.searchParams.get('q') ?? '').toLowerCase()
        const status = url.searchParams.get('status')
        const limit = Number(url.searchParams.get('limit') ?? 50)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        const matching = users.filter(
          (u) => (!q || u.email.toLowerCase().includes(q) || u.id === q) && (!status || u.status === status),
        )
        return json({ users: matching.slice(offset, offset + limit), total: matching.length, limit, offset })
      }
      const id = url.pathname.split('/')[5] ?? ''
      if (request.method === 'GET') {
        if (detailFails) return problem(detailFails)
        const account = users.find((u) => u.id === id)
        const found = details[id] ?? (account ? detail(account) : null)
        return found ? json(found) : problem({ status: 404, detail: 'There is no such account.' })
      }
      if (changeFails) return problem(changeFails)
      if (url.pathname.endsWith('/revoke-sessions')) return json({ ended: 3 })
      return json(users.find((u) => u.id === id) ?? user(id, 'someone@example.test'))
    }),
  )
  return calls
}

/** The changes made, in order: what was asked of which account, with the reason and the CSRF token. */
export const changes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET')
