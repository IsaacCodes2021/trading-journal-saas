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

/** One month of the calendar: what traded each day, the month's statistics, and which days have a note. */
export interface CalendarMonth {
  daily: DailyResult[] // only the days that traded
  summary: SummaryStats
  noted: string[] // trading days ("YYYY-MM-DD") with a note, oldest first
}

/** One trading day: its statistics, its trades in the order they were entered, and how many there were in all. */
export interface DayData {
  summary: SummaryStats
  equity_by_trade: EquityPoint[]
  trades: TradeRow[]
  total: number // more than `trades.length` when a very busy day was cut off
}

/** The most trades one day's page shows. */
export const DAY_TRADES_LIMIT = 200

/** What a report can break the filtered trades down by. */
export const REPORT_BYS = ['symbol', 'weekday', 'hour', 'hold', 'tag', 'account'] as const
export type ReportBy = (typeof REPORT_BYS)[number]

/** One row of a report: a contract, a weekday, an hour, a hold time, a tag or an account, with the dashboard's numbers. */
export interface ReportGroup {
  key: string // stable: a contract root, a weekday number, an hour, a hold step, a tag id, an account id
  label: string // what to show: "NQ", "Monday", "09:00", "5 to 15 minutes", "A+ setup", "Apex 07"
  detail: string | null // the tag's category, or the account's firm and type; nothing for the others
  summary: SummaryStats
}

export interface ReportData {
  by: ReportBy
  groups: ReportGroup[] // in the order to read them
  summary: SummaryStats // over every trade the filters matched (a trade with several tags is one trade here)
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
/** Where an account is in a firm's program. The type says evaluation or funded; a funded account can be live. */
export const PHASES = ['evaluation', 'funded', 'live'] as const
export type Phase = (typeof PHASES)[number]
export const PHASE_LABELS: Record<Phase, string> = { evaluation: 'Evaluation', funded: 'Funded', live: 'Live' }
export const PHASES_BY_TYPE: Record<AccountType, readonly Phase[]> = {
  evaluation: ['evaluation'],
  funded: ['funded', 'live'],
}

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
  purchase_date: string | null // "YYYY-MM-DD": which version of the firm's rules applies; null on older accounts
  phase: Phase
  pass_prompt_dismissed_at: string | null // the "evaluation passed" prompt was dismissed
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
  purchase_date: string | null // required for a new account
  phase: Phase | null // null: the same as the type
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
  amount: string // a payout's net: what the trader received before any fee
  entry_date: string
  note: string
  // A payout's own facts: null on costs, and on payouts logged before they were kept.
  payout_number: number | null
  gross_amount: string | null
  profit_split_pct: string | null
  payout_fee: string | null
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

// ---- prop-firm rules, payouts and the Accounts tab (docs/prop-rules/PLAN.md) --------------------------------------------

/*
 * A rule value crosses the API as its field's kind says: money and percentages as decimal strings, whole numbers as
 * numbers, dates as "YYYY-MM-DD", everything else as text; an empty field is null. A rule set is one object keyed by
 * field name, the same shape for a firm's template and for an account's verified rules.
 */
export type RuleKind = 'text' | 'money' | 'int' | 'pct' | 'date' | 'choice'
export type RuleValue = string | number | null
export type RuleValues = Record<string, RuleValue>

export interface RuleField {
  name: string
  kind: RuleKind
  group: string
  label: string
  choices: string[] // the allowed values of a choice field
  hint: string
}

export interface RuleFields {
  groups: string[] // in the form's order
  fields: RuleField[]
}

export type FlagKind = 'unknown' | 'conflict' | 'third-party only'

/** A field the research could not confirm: `unknown` and `conflict` fields were left empty on purpose; a `third-party
 * only` field keeps its value. The reason is the research's own words (a conflict names both values). */
export interface RuleFlag {
  field: string
  reason: string
  kind: FlagKind
}

/** One line of the rules picker. */
export interface RuleTemplateSummary {
  id: number
  firm: string
  program: string
  account_size: string
  phase: Phase
  rule_version: string
  effective_from: string | null
  effective_from_note: string | null // what the source says when the start is not a clean date
  data_confidence: string | null
  flagged: number
  preselect: boolean // the version to suggest for the purchase date asked about
}

export interface RuleTemplate {
  id: number
  values: RuleValues
  flags: RuleFlag[]
  source_notes: Record<string, string> // the original text of cells that were not a clean value ("up to 1500")
  updated_at: string
}

/** The rules a person checked and submitted for one account: a copy, so a later template change never reaches it. */
export interface RuleSet {
  id: number
  account_id: number
  source_template_id: number | null
  verified_at: string
  values: RuleValues
  flags: RuleFlag[]
  source_notes: Record<string, string>
}

export interface RuleSetInput {
  values: RuleValues
  flags: { field: string; reason: string }[]
  source_notes: Record<string, string>
  source_template_id: number | null // null for rules typed in by hand
}

export interface PayoutInput {
  entry_date: string
  gross: string
  split_pct: string | null // null: the split in the account's rules
  fee: string | null
  note: string
}

export interface Payouts {
  payouts: LedgerEntry[] // oldest first
  split_pct: string | null // the share the account's rules promise; null when they give none
  next_number: number
  lifetime_gross: string
  lifetime_net: string
  lifetime_fees: string
}

export type RuleStatus = 'pass' | 'fail' | 'n/a' | 'not_verifiable'
export type AccountStatus = 'ok' | 'at_risk' | 'breached' | 'passed' | 'payout_ready'

export interface RuleResult {
  key: string
  group: string
  label: string
  status: RuleStatus
  current: string | null
  limit: string | null
  message: string
}

/** One bar from breach (left) to goal (right). */
export interface Spectrum {
  breach_level: string | null
  breach_kind: 'drawdown' | 'daily_loss' | null
  target_level: string | null
  target_kind: 'profit_target' | 'payout' | null
  balance: string
  to_breach: string | null
  to_target: string | null
  position: number | null // 0 at the breach level, 1 at the target
}

export interface PayoutReadiness {
  ready: boolean
  gross: string | null
  net: string | null
  split_pct: string | null
  next_number: number
  progress: string // "3/5 qualifying days · best day 38% of 50% max · $640 to buffer"
  blockers: string[]
}

export interface InactivityStatus {
  days_left: number | null
  deadline: string | null
  message: string
}

/** One active account on the Accounts tab. Without verified rules only the money is known. */
export interface AccountRow {
  account: AccountDetail
  program: string | null
  phase: Phase
  rules_set: boolean
  balance: string
  net_pnl: string
  status: AccountStatus | null
  rules: RuleResult[]
  spectrum: Spectrum | null
  payout: PayoutReadiness | null
  consistency_headroom: string | null
  inactivity: InactivityStatus | null
  floor: string | null
  progress: string
  withdrawn_gross: string
  withdrawn_net: string
  pass_prompt: boolean // an evaluation passed: offer to set up the funded account
}

export interface AccountsTotals {
  balance: string
  net_pnl: string
  withdrawable_gross: string // over the payout-ready rows
  withdrawable_net: string
  withdrawn_gross: string // lifetime, over the rows shown
  withdrawn_net: string
}

export interface AccountsOverview {
  accounts: AccountRow[]
  totals: AccountsTotals
}

/** What the rules form says to a refused rule set: the sentence, and the fields that are wrong (`values.max_loss_usd`). */
export class RulesRefused extends Error {
  readonly fields: Record<string, string>

  constructor(message: string, fields: Record<string, string> = {}) {
    super(message)
    this.name = 'RulesRefused'
    this.fields = fields
  }
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
  /** The calendar's month ("YYYY-MM"): the filters apply, but their date range does not (the month is the range). */
  calendar(month: string, filters: Filters, signal?: AbortSignal): Promise<CalendarMonth>
  /** One trading day ("YYYY-MM-DD"), with the same filters. */
  day(day: string, filters: Filters, signal?: AbortSignal): Promise<DayData>
  /** The filtered trades broken down by `by`, each group with the dashboard's statistics. */
  report(by: ReportBy, filters: Filters, signal?: AbortSignal): Promise<ReportData>
  /** The note for a trading day, as the editor's HTML; "" when nothing is written. */
  dayNote(day: string, signal?: AbortSignal): Promise<string>
  saveDayNote(day: string, html: string): Promise<void>
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
  /** The Accounts tab: every active account against its verified rules, and the totals. */
  accountsOverview(signal?: AbortSignal): Promise<AccountsOverview>
  dismissPassPrompt(id: number): Promise<AccountDetail>
  ruleFields(signal?: AbortSignal): Promise<RuleFields>
  /** The firms' rule sets for an account bought on `purchasedOn` ("YYYY-MM-DD"), the latest version preselected. */
  ruleTemplates(purchasedOn: string | null, signal?: AbortSignal): Promise<RuleTemplateSummary[]>
  ruleTemplate(templateId: number, signal?: AbortSignal): Promise<RuleTemplate>
  /** The account's verified rules; null when there are none yet. */
  ruleSet(id: number, signal?: AbortSignal): Promise<RuleSet | null>
  saveRuleSet(id: number, input: RuleSetInput): Promise<RuleSet>
  payouts(id: number, signal?: AbortSignal): Promise<Payouts>
  addPayout(id: number, input: PayoutInput): Promise<LedgerEntry>
}
