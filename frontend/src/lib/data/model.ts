import type { AccountType, Direction, Filters } from '../filters'

/*
 * What the pages read. These shapes mirror the backend's `finance.Summary`, `PerformanceScore` and trade rows, so the
 * stats and trades endpoints (M3) can return them as they are. Money is always a decimal STRING ("-475.00"): the client
 * never does arithmetic on it. Ratios and counts are plain numbers. When those endpoints exist their generated types in
 * `lib/api/schema.d.ts` replace these, and a page only changes its import.
 */

export interface AccountRef {
  id: number
  name: string
  firm: string
  account_type: AccountType
}

export interface TagGroupRef {
  id: number
  name: string // "Setup", "Mistake", "Emotion"
}

export interface TagRef {
  id: number
  group_id: number
  name: string
  color: string // "#rrggbb"
}

export interface TradeRow {
  id: number
  account: AccountRef
  symbol: string // the contract, "NQZ6"
  symbol_root: string // "NQ"
  direction: Direction
  entry_at: string // ISO 8601 UTC
  exit_at: string
  trade_day: string // "YYYY-MM-DD", the trading day (computed on the server)
  hold_seconds: number
  total_qty: number
  avg_entry: string
  avg_exit: string
  gross_pnl: string
  fees: string
  net_pnl: string
  tags: TagRef[]
  has_notes: boolean
  screenshot_count: number
}

export const TRADE_SORTS = ['date', 'account', 'symbol', 'direction', 'qty', 'hold', 'net_pnl'] as const
export type TradeSortField = (typeof TRADE_SORTS)[number]
/** "date" ascending, "-date" descending (the API's `sort` convention, doc 07 section 2). */
export type TradeSort = TradeSortField | `-${TradeSortField}`

export interface TradesQuery extends Filters {
  q?: string
  sort: TradeSort
  page: number // 1-based
  per_page: number
}

export interface TradesPage {
  items: TradeRow[]
  total: number // trades matching every filter, across all pages
  net_pnl: string // their net result: the count and the net follow every filter
}

export interface SummaryStats {
  trade_count: number
  account_count: number
  win_count: number
  loss_count: number
  breakeven_count: number
  gross_pnl: string
  fees: string
  net_pnl: string
  total_won: string
  total_lost: string // negative
  win_rate: number | null // 0..1
  profit_factor: number | null
  avg_win: string | null
  avg_loss: string | null // negative
  win_loss_ratio: number | null
  expectancy: string | null
  largest_win: string | null
  largest_loss: string | null
  trading_days: number
  winning_days: number
  losing_days: number
  max_drawdown: string // a positive amount
}

export interface EquityPoint {
  at: string // ISO UTC of the trade's exit, or the trading day when the series is by day
  equity: string // cumulative net P&L
  drawdown: string // equity minus its running peak: zero or negative
}

export interface DailyResult {
  day: string
  net_pnl: string
  trade_count: number
}

export interface ScoreAxis {
  name: string
  score: number // 0..100
  rule: string // how the measured value maps to the score, in words
}

export interface PerformanceScore {
  overall: number
  axes: ScoreAxis[] // as many as the backend defines (six today), drawn generically
}

export interface AccountSummary {
  account: AccountRef
  trade_count: number
  net_pnl: string
}

export interface DashboardData {
  range: { start: string; end: string } | null // the days the data covers; null when there are no trades
  summary: SummaryStats
  equity_by_trade: EquityPoint[]
  equity_by_day: EquityPoint[]
  daily: DailyResult[]
  score: PerformanceScore | null // null when there are no trades
  accounts: AccountSummary[]
}

export interface FilterOptions {
  firms: string[]
  symbol_roots: string[]
}

/** One row of the broker's export: a buy fill matched to a sell fill. Several of them make one trade. */
export interface FillPairRow {
  id: number
  bought_at: string // ISO UTC
  sold_at: string
  qty: number
  buy_price: string
  sell_price: string
  pnl: string // this pair's gross result: before fees, which are charged per trade
}

export interface ScreenshotRef {
  id: number
  original_name: string
  created_at: string // ISO UTC
  /** Same-origin URL of the image; null in the sample data, which has no real pictures. */
  src: string | null
}

/** Everything the detail page shows about one trade. */
export interface TradeDetail extends TradeRow {
  points: string // per contract, positive = favourable
  fill_pairs: FillPairRow[]
  notes_html: string // sanitized again on render, whatever the server did
  screenshots: ScreenshotRef[]
}

export interface TagCatalog {
  groups: TagGroupRef[]
  tags: TagRef[]
}

/** The trades either side of one in the list the user came from, so the arrows follow the list's sort and filters. */
export interface Neighbors {
  previous: number | null
  next: number | null
}

/** Why a screenshot was refused, in words for the person who chose it. */
export class ScreenshotRefused extends Error {}

/** Where the pages get data. The only implementation today is the sample-data source; the API one replaces it. */
export interface DataSource {
  /** Shown as a banner on every page, so sample numbers are never mistaken for real ones. */
  readonly label: string | null
  dashboard(filters: Filters, signal?: AbortSignal): Promise<DashboardData>
  trades(query: TradesQuery, signal?: AbortSignal): Promise<TradesPage>
  filterOptions(signal?: AbortSignal): Promise<FilterOptions>
  /** Null when there is no such trade (or it is not this user's: the two look the same from outside). */
  trade(id: number, signal?: AbortSignal): Promise<TradeDetail | null>
  neighbors(id: number, query: TradesQuery, signal?: AbortSignal): Promise<Neighbors>
  tagCatalog(signal?: AbortSignal): Promise<TagCatalog>
  saveNotes(id: number, html: string): Promise<void>
  setTradeTag(id: number, tagId: number, on: boolean): Promise<void>
  createTag(groupId: number, name: string): Promise<TagRef>
  addScreenshots(id: number, files: readonly File[]): Promise<ScreenshotRef[]>
  deleteScreenshot(id: number, screenshotId: number): Promise<void>
}
