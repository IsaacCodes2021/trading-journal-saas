import type { TemplateQuery } from '../../lib/adminRules'

/** What the firm rules address holds, with only the valid keys kept (the page applies the defaults). */
export interface RulesSearch {
  q?: string
  page?: number
  /** The template that is open beside the list. */
  template?: number
}

const whole = (raw: unknown, max: number): number | null => {
  if (typeof raw !== 'number' && typeof raw !== 'string') return null // `true` is not template 1
  const n = Number(raw)
  return Number.isInteger(n) && n >= 1 && n <= max ? n : null
}

export function pickRulesSearch(raw: Record<string, unknown>): RulesSearch {
  const q = typeof raw['q'] === 'string' ? raw['q'].trim().slice(0, 100) : ''
  const page = whole(raw['page'], 100_000)
  const template = whole(raw['template'], Number.MAX_SAFE_INTEGER)
  return {
    ...(q ? { q } : {}),
    ...(page && page > 1 ? { page } : {}),
    ...(template ? { template } : {}),
  }
}

export function rulesQueryOf(search: RulesSearch): TemplateQuery {
  return { q: search.q ?? '', page: search.page ?? 1 }
}

/** The address for a query (and the open template), leaving out whatever is the default. */
export function rulesSearchOf(query: TemplateQuery, template: number | null): RulesSearch {
  return {
    ...(query.q ? { q: query.q } : {}),
    ...(query.page > 1 ? { page: query.page } : {}),
    ...(template ? { template } : {}),
  }
}
