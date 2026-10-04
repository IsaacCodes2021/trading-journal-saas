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

/** A tag with how many trades carry it, for the tags screen. */
export interface CatalogTag extends TagRef {
  trade_count: number
}

export interface ColorPreset {
  color: string // "#rrggbb"
  name: string // "Blue"
}

export interface TagCatalog {
  groups: TagGroupRef[]
  tags: CatalogTag[]
  presets: ColorPreset[] // the colours on offer for a tag
  custom_colors: string[] // colours already on tags that are not presets, oldest first, so they can be reused
}

/** A tag as it should now be: the whole thing, saved at once. */
export interface TagEdit {
  name: string
  group_id: number
  color: string
}

/** The trades either side of one in the list the user came from, so the arrows follow the list's sort and filters. */
export interface Neighbors {
  previous: number | null
  next: number | null
}

// ---- imports ----------------------------------------------------------------------------------------------------

/** A line of the file that could not be used, with the line number a person can find in a spreadsheet. */
export interface ImportIssue {
  line: number
  message: string
}

export type PairStatus = 'new' | 'duplicate' | 'other_account'

/** One fill pair as the file states it (times as written in the file, in the timezone the settings name). */
export interface PreviewRow {
  line: number
  status: PairStatus
  /** For `other_account`: the account the pair is already stored under. */
  account: string | null
  symbol: string
  bought_at: string
  sold_at: string
  qty: number
  buy_price: string
  sell_price: string
  pnl: string
}

/** What importing a file would do, worked out without saving anything (the "dry run"). Mirrors `FileImportReport`. */
export interface ImportPreview {
  file_name: string
  file_bytes: number
  /** The whole file was refused (wrong kind of export, empty): a reason in words. */
  fatal: string | null
  rows_parsed: number // valid fill pairs read from the file
  rows_added: number // of those, new to this account
  duplicates: number // already stored in this account
  duplicates_in_file: number // repeated within the file
  duplicates_other_accounts: Record<string, number> // stored under another account, by account name
  errors: ImportIssue[]
  warnings: ImportIssue[]
  trades_built: number // trades the new pairs would create or extend
  rows: PreviewRow[] // the first rows, for the table
  rows_shown_limit: number
}

/** One line of the import history. Mirrors the `imports` table. */
export interface ImportRecord {
  id: number
  imported_at: string // ISO UTC
  file_name: string
  account: AccountRef
  rows_added: number
  trades_built: number
  undone_at: string | null
}

/** What undoing an import would remove, before it is confirmed. Mirrors `UndoReport`. */
export interface UndoPreview {
  import_id: number
  rows_removed: number
  rows_kept: number // still contained in another import
  trades_removed: number
  trades_with_journal_removed: number // removed trades that had notes, tags or screenshots
  trades_changed: number
}

/** Why a file was refused before it was even read, in words. */
export class ImportRefused extends Error {}

// ---- prop accounts -----------------------------------------------------------------------------------------------

export const FIRMS = ['Apex', 'Bulenox', 'Lucid', 'My Funded Futures', 'Take Profit', 'Tradeify', 'Other'] as const
export const OUTCOMES = ['passed', 'failed', 'closed'] as const
export type Outcome = (typeof OUTCOMES)[number]
export const OUTCOME_LABELS: Record<Outcome, string> = { passed: 'Passed', failed: 'Failed', closed: 'Closed' }
/** How an account can end: an evaluation passes or fails, a funded account only fails or is closed. */
export const OUTCOMES_BY_TYPE: Record<AccountType, readonly Outcome[]> = {
  evaluation: ['passed', 'failed', 'closed'],
  funded: ['failed', 'closed'],
}
export const TRAILING_TYPES = ['eod', 'intraday'] as const
export type TrailingType = (typeof TRAILING_TYPES)[number]
export const TRAILING_LABELS: Record<TrailingType, string> = { eod: 'End of day', intraday: 'Intraday' }

/** Everything stored about an account. Money is a decimal string or null (not set). Mirrors `accounts.Account`. */
export interface AccountDetail extends AccountRef {
  account_size: string | null
  starting_balance: string
  max_drawdown: string | null
  trailing_type: TrailingType | null
  trail_lock_enabled: boolean
  trail_lock_level: string | null
  daily_loss_limit: string | null
  profit_target: string | null
  created_at: string
  outcome: Outcome | null // set when archived
  ended_on: string | null // "YYYY-MM-DD"
  archive_note: string
}

/** How an account is doing against its rules. The server works these out from closed trades. */
export interface AccountProgress {
  net_pnl: string
  trade_count: number
  target_progress: number | null // 0..1 of the profit target; null when there is no target
  drawdown_left: string | null // what is left of the allowed drawdown; null when there is no limit
  last_trading_day: string | null // "YYYY-MM-DD" of its latest trade; null when it has none
}

export interface AccountOverview {
  account: AccountDetail
  progress: AccountProgress
}

export interface AccountDetailsInput {
  name: string
  firm: string
  account_type: AccountType
  account_size: string | null
  starting_balance: string
}

export interface AccountRulesInput {
  profit_target: string | null
  max_drawdown: string | null
  trailing_type: TrailingType | null
  trail_lock_enabled: boolean
  trail_lock_level: string | null
  daily_loss_limit: string | null
}

export interface ArchiveInput {
  outcome: Outcome
  ended_on: string
  note: string
}

/** The fee for one contract round trip, by symbol root ("NQ"); "*" is any other contract. */
export type FeeSchedule = Record<string, string>

export const LEDGER_KINDS = {
  evaluation_fee: 'Evaluation fee',
  reset_fee: 'Reset',
  activation_fee: 'Activation fee',
  monthly_fee: 'Monthly fee',
  other_cost: 'Other cost',
  payout: 'Payout',
} as const
export type LedgerKind = keyof typeof LEDGER_KINDS

/** Money spent on or received from an account, kept apart from trading P&L. `amount` is always positive. */
export interface LedgerEntry {
  id: number
  account_id: number
  kind: LedgerKind
  amount: string
  entry_date: string
  note: string
}

export interface NewLedgerEntry {
  kind: LedgerKind
  amount: string
  entry_date: string
  note: string
}

/** Across every account, archived ones included: is the prop business itself profitable? */
export interface BusinessTotals {
  evaluations_passed: number
  evaluations_failed: number
  pass_rate: number | null // passed / (passed + failed) over evaluations; closed ones do not count
  costs: string
  payouts: string
  net: string // payouts minus costs
  accounts_without_cost: string[] // names of accounts with no cost recorded
}

/** A change an account's rules or details refused, in words for the person who made it. */
export class AccountRefused extends Error {}

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
  // Managing the tags themselves. Each answers with the whole catalog as it is afterwards.
  createGroup(name: string): Promise<TagCatalog>
  renameGroup(id: number, name: string): Promise<TagCatalog>
  deleteGroup(id: number): Promise<TagCatalog>
  editTag(id: number, edit: TagEdit): Promise<TagCatalog>
  mergeTag(id: number, intoId: number): Promise<TagCatalog>
  deleteTag(id: number): Promise<TagCatalog>
  addScreenshots(id: number, files: readonly File[]): Promise<ScreenshotRef[]>
  deleteScreenshot(id: number, screenshotId: number): Promise<void>
  accounts(signal?: AbortSignal): Promise<AccountRef[]>
  previewImport(accountId: number, file: File): Promise<ImportPreview>
  commitImport(accountId: number, file: File): Promise<ImportRecord>
  imports(signal?: AbortSignal): Promise<ImportRecord[]>
  undoPreview(importId: number): Promise<UndoPreview>
  undoImport(importId: number): Promise<void>
  accountOverviews(signal?: AbortSignal): Promise<AccountOverview[]>
  businessTotals(signal?: AbortSignal): Promise<BusinessTotals>
  createAccount(input: AccountDetailsInput): Promise<AccountDetail>
  updateAccount(id: number, input: AccountDetailsInput): Promise<AccountDetail>
  updateRules(id: number, input: AccountRulesInput): Promise<AccountDetail>
  archiveAccount(id: number, input: ArchiveInput): Promise<AccountDetail>
  restoreAccount(id: number): Promise<AccountDetail>
  fees(id: number, signal?: AbortSignal): Promise<FeeSchedule>
  setFee(id: number, symbolRoot: string, rate: string): Promise<void>
  removeFee(id: number, symbolRoot: string): Promise<void>
  ledger(id: number, signal?: AbortSignal): Promise<LedgerEntry[]>
  addLedgerEntry(id: number, entry: NewLedgerEntry): Promise<LedgerEntry>
  deleteLedgerEntry(id: number, entryId: number): Promise<void>
}
