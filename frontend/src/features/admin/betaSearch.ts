import { BETA_STATES, type BetaQuery, type BetaState } from '../../lib/adminBeta'

/** What the beta page's address holds, with only the valid keys kept (the page applies the defaults). */
export interface BetaSearch {
  q?: string
  state?: BetaState
  page?: number
  /** The person who is open beside the list (a sign-up's number). */
  person?: number
}

export function pickBetaSearch(raw: Record<string, unknown>): BetaSearch {
  const q = typeof raw['q'] === 'string' ? raw['q'].trim().slice(0, 100) : ''
  const state =
    typeof raw['state'] === 'string' && (BETA_STATES as readonly string[]).includes(raw['state'])
      ? (raw['state'] as BetaState)
      : null
  const page = Number(raw['page'])
  const person = Number(raw['person'])
  return {
    ...(q ? { q } : {}),
    ...(state ? { state } : {}),
    ...(Number.isInteger(page) && page > 1 && page <= 100_000 ? { page } : {}),
    ...(Number.isInteger(person) && person > 0 && person <= 2_147_483_647 ? { person } : {}),
  }
}

export function betaQueryOf(search: BetaSearch): BetaQuery {
  return { q: search.q ?? '', state: search.state ?? '', page: search.page ?? 1 }
}

/** The address for a query (and the open person), leaving out whatever is the default. */
export function betaSearchOf(query: BetaQuery, person: number | null): BetaSearch {
  return {
    ...(query.q ? { q: query.q } : {}),
    ...(query.state ? { state: query.state } : {}),
    ...(query.page > 1 ? { page: query.page } : {}),
    ...(person ? { person } : {}),
  }
}
