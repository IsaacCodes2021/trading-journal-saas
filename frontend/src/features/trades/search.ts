import { TRADE_SORTS, type TradeSort, type TradeSortField, type TradesQuery } from '../../lib/data/model'
import { parseFilters, pickFilters, type Filters } from '../../lib/filters'

export const PAGE_SIZES = [12, 25, 50] as const
export const DEFAULT_SORT: TradeSort = '-date'

/** The trade log's URL state: the shared filters plus search text, sort order and page. */
export interface TradeSearch extends Filters {
  q?: string
  sort: TradeSort
  page: number
  per_page: number
}

function isSort(value: unknown): value is TradeSort {
  if (typeof value !== 'string') return false
  const field = value.startsWith('-') ? value.slice(1) : value
  return (TRADE_SORTS as readonly string[]).includes(field)
}

function positiveInt(value: unknown, fallback: number): number {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN
  return Number.isInteger(n) && n >= 1 ? n : fallback
}

/** What the route keeps from the URL: only valid keys, no defaults, so a link can name just the keys it cares about. */
export function pickTradeSearch(search: Record<string, unknown>): Partial<TradeSearch> {
  const q = typeof search['q'] === 'string' ? search['q'].trim() : ''
  const page = positiveInt(search['page'], 0)
  const perPage = positiveInt(search['per_page'], 0)
  return {
    ...pickFilters(search),
    ...(q ? { q } : {}),
    ...(isSort(search['sort']) ? { sort: search['sort'] } : {}),
    ...(page > 0 ? { page } : {}),
    ...((PAGE_SIZES as readonly number[]).includes(perPage) ? { per_page: perPage } : {}),
  }
}

/** The page's view of the URL, with defaults applied. Anything unreadable falls back rather than failing the page. */
export function parseTradeSearch(search: Record<string, unknown>): TradeSearch {
  const picked = pickTradeSearch(search)
  return {
    ...parseFilters(picked),
    ...picked,
    sort: picked.sort ?? DEFAULT_SORT,
    page: picked.page ?? 1,
    per_page: picked.per_page ?? PAGE_SIZES[0],
  }
}

export function toQuery(search: TradeSearch): TradesQuery {
  return { ...search }
}

/** Clicking a column: the first click sorts ascending (descending for date and P&L, where biggest-first is natural), the next flips it. */
export function nextSort(current: TradeSort, field: TradeSortField): TradeSort {
  if (current === field) return `-${field}`
  if (current === `-${field}`) return field
  return field === 'date' || field === 'net_pnl' ? `-${field}` : field
}
