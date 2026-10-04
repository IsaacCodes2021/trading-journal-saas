import type { UserQuery, UserStatus } from '../../lib/admin'

/** What the operator's address holds, with only the valid keys kept (the page applies the defaults). */
export interface AdminSearch {
  q?: string
  status?: UserStatus
  page?: number
  /** The account that is open beside the list. */
  user?: string
}

const STATUSES: readonly string[] = ['active', 'suspended', 'pending_deletion']
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function pickAdminSearch(raw: Record<string, unknown>): AdminSearch {
  const q = typeof raw['q'] === 'string' ? raw['q'].trim().slice(0, 100) : ''
  const status =
    typeof raw['status'] === 'string' && STATUSES.includes(raw['status']) ? (raw['status'] as UserStatus) : null
  const page = Number(raw['page'])
  const user = typeof raw['user'] === 'string' && ID.test(raw['user']) ? raw['user'].toLowerCase() : null
  return {
    ...(q ? { q } : {}),
    ...(status ? { status } : {}),
    ...(Number.isInteger(page) && page > 1 && page <= 100_000 ? { page } : {}),
    ...(user ? { user } : {}),
  }
}

export function queryOf(search: AdminSearch): UserQuery {
  return { q: search.q ?? '', status: search.status ?? '', page: search.page ?? 1 }
}

/** The address for a query (and the open account), leaving out whatever is the default. */
export function searchOf(query: UserQuery, user: string | null): AdminSearch {
  return {
    ...(query.q ? { q: query.q } : {}),
    ...(query.status ? { status: query.status } : {}),
    ...(query.page > 1 ? { page: query.page } : {}),
    ...(user ? { user } : {}),
  }
}
