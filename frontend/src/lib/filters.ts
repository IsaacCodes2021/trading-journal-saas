/**
 * The filters every page shares. They live in the URL (`/?preset=30d&firms=Apex`), so a view survives a reload and can
 * be bookmarked or sent; nothing is held in a global. The server resolves `preset` in the user's timezone (doc 07
 * section 2): the client never computes a date range.
 */

export const PRESETS = ['7d', '30d', '90d', 'ytd', 'all'] as const
export type Preset = (typeof PRESETS)[number]

export const PRESET_LABELS: Record<Preset, string> = { '7d': '7D', '30d': '30D', '90d': '90D', ytd: 'YTD', all: 'All' }

export const ACCOUNT_TYPES = ['evaluation', 'funded'] as const
export type AccountType = (typeof ACCOUNT_TYPES)[number]
export const ACCOUNT_TYPE_LABELS: Record<AccountType, string> = { evaluation: 'Evaluation', funded: 'Funded' }

export const DIRECTIONS = ['long', 'short'] as const
export type Direction = (typeof DIRECTIONS)[number]

export interface Filters {
  preset: Preset
  account_type?: AccountType
  firms?: string[]
  symbol_roots?: string[]
  direction?: Direction
}

export const DEFAULT_PRESET: Preset = '30d'

function oneOf<T extends string>(allowed: readonly T[], value: unknown): T | undefined {
  return allowed.find((option) => option === value)
}

/** One or many values from a search param: `firms=Apex`, `firms=Apex&firms=Lucid` and `firms=["Apex"]` all work. */
function stringList(value: unknown): string[] | undefined {
  const items = Array.isArray(value) ? value : value === undefined || value === '' ? [] : [value]
  const clean = [...new Set(items.filter((item): item is string => typeof item === 'string' && item.trim() !== ''))]
  return clean.length > 0 ? clean : undefined
}

/**
 * What a route keeps from the URL: only valid filter keys, with no default filled in. Tolerant: unknown or malformed
 * values are dropped, never thrown.
 */
export function pickFilters(search: Record<string, unknown>): Partial<Filters> {
  const filters: Partial<Filters> = {}
  const preset = oneOf(PRESETS, search['preset'])
  if (preset) filters.preset = preset
  const accountType = oneOf(ACCOUNT_TYPES, search['account_type'])
  const direction = oneOf(DIRECTIONS, search['direction'])
  const firms = stringList(search['firms'])
  const symbols = stringList(search['symbol_roots'])
  if (accountType) filters.account_type = accountType
  if (firms) filters.firms = firms
  if (symbols) filters.symbol_roots = symbols
  if (direction) filters.direction = direction
  return filters
}

/** The page's view of the filters, with the default date range when the URL names none. */
export function parseFilters(search: Record<string, unknown>): Filters {
  return { preset: DEFAULT_PRESET, ...pickFilters(search) }
}

/** Only the filter keys of any search object, so navigating between pages keeps the filters and drops the rest. */
export function filtersOf(search: Partial<Filters>): Filters {
  return parseFilters({ ...search })
}

export function hasNarrowingFilters(filters: Filters): boolean {
  return Boolean(filters.account_type ?? filters.firms ?? filters.symbol_roots ?? filters.direction)
}

/** A short label for a multi-select button: "All symbols", "NQ", "Apex, Lucid", "3 firms". */
export function summarizeSelection(selected: string[] | undefined, allLabel: string, noun: string): string {
  if (!selected || selected.length === 0) return allLabel
  if (selected.length <= 2) return selected.join(', ')
  return `${selected.length} ${noun}`
}

export const DIRECTION_LABELS: Record<Direction | 'both', string> = {
  both: 'Long + short',
  long: 'Long only',
  short: 'Short only',
}
