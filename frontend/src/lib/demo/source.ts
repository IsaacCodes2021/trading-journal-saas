import Decimal from 'decimal.js'
import type {
  DashboardData,
  DataSource,
  FilterOptions,
  Neighbors,
  TradeDetail,
  TradeRow,
  TradeSort,
  TradesPage,
  TradesQuery,
} from '../data/model'
import type { Filters } from '../filters'
import { SAMPLE_ACCOUNTS, SAMPLE_TRADES, fillPairsFor, pointsFor } from './generate'
import * as accounts from './accounts'
import * as imports from './imports'
import * as journal from './journal'
import {
  accountSummaries,
  applyFilters,
  dailyResults,
  equityByDay,
  equityByTrade,
  performanceScore,
  summarize,
} from './stats'

const LAST_DAY = SAMPLE_TRADES.reduce((last, t) => (t.trade_day > last ? t.trade_day : last), '0000-00-00')

function dashboard(filters: Filters): DashboardData {
  const trades = applyFilters(SAMPLE_TRADES, filters, LAST_DAY)
  const summary = summarize(trades)
  const daily = dailyResults(trades)
  return {
    range: daily.length ? { start: daily[0]?.day ?? '', end: daily[daily.length - 1]?.day ?? '' } : null,
    summary,
    equity_by_trade: equityByTrade(trades),
    equity_by_day: equityByDay(daily),
    daily,
    score: performanceScore(summary, daily),
    accounts: accountSummaries(trades),
  }
}

function compare(a: TradeRow, b: TradeRow, field: string): number {
  switch (field) {
    case 'account':
      return a.account.name.localeCompare(b.account.name)
    case 'symbol':
      return a.symbol.localeCompare(b.symbol)
    case 'direction':
      return a.direction.localeCompare(b.direction)
    case 'qty':
      return a.total_qty - b.total_qty
    case 'hold':
      return a.hold_seconds - b.hold_seconds
    case 'net_pnl':
      return new Decimal(a.net_pnl).comparedTo(b.net_pnl)
    default:
      return a.entry_at < b.entry_at ? -1 : a.entry_at > b.entry_at ? 1 : 0
  }
}

function sortTrades(trades: TradeRow[], sort: TradeSort): TradeRow[] {
  const descending = sort.startsWith('-')
  const field = descending ? sort.slice(1) : sort
  return [...trades].sort((a, b) => (descending ? -1 : 1) * (compare(a, b, field) || a.id - b.id))
}

function matches(trade: TradeRow, q: string): boolean {
  const needle = q.trim().toLowerCase()
  if (!needle) return true
  return [trade.symbol, trade.symbol_root, trade.account.name, ...trade.tags.map((t) => t.name)].some((text) =>
    text.toLowerCase().includes(needle),
  )
}

/** Every trade matching the filters and search, in the requested order. */
function listFor(query: TradesQuery): TradeRow[] {
  const withChanges = SAMPLE_TRADES.map(journal.withJournal)
  const filtered = applyFilters(withChanges, query, LAST_DAY).filter((t) => matches(t, query.q ?? ''))
  return sortTrades(filtered, query.sort)
}

function trades(query: TradesQuery): TradesPage {
  const ordered = listFor(query)
  const start = (Math.max(1, query.page) - 1) * query.per_page
  return {
    items: ordered.slice(start, start + query.per_page),
    total: ordered.length,
    net_pnl: ordered.reduce((total, t) => total.plus(t.net_pnl), new Decimal(0)).toFixed(2),
  }
}

const find = (id: number): TradeRow | undefined => SAMPLE_TRADES.find((t) => t.id === id)

function detail(id: number): TradeDetail | null {
  const trade = find(id)
  if (!trade) return null
  return {
    ...journal.withJournal(trade),
    points: pointsFor(trade),
    fill_pairs: fillPairsFor(trade),
    notes_html: journal.notesOf(trade),
    screenshots: journal.screenshotsOf(trade),
  }
}

function neighbors(id: number, query: TradesQuery): Neighbors {
  const ids = listFor(query).map((t) => t.id)
  const at = ids.indexOf(id)
  if (at < 0) return { previous: null, next: null }
  return { previous: ids[at - 1] ?? null, next: ids[at + 1] ?? null }
}

/** The trade a write is about, or a refusal that reads like the server's 404. */
function target(id: number): TradeRow {
  const trade = find(id)
  if (!trade) throw new Error('That trade does not exist.')
  return trade
}

function filterOptions(): FilterOptions {
  return {
    firms: [...new Set(SAMPLE_ACCOUNTS.map((a) => a.firm))].sort(),
    symbol_roots: [...new Set(SAMPLE_TRADES.map((t) => t.symbol_root))].sort(),
  }
}

export const demoSource: DataSource = {
  label: 'Sample data',
  dashboard: (filters) => Promise.resolve(dashboard(filters)),
  trades: (query) => Promise.resolve(trades(query)),
  filterOptions: () => Promise.resolve(filterOptions()),
  trade: (id) => Promise.resolve(detail(id)),
  neighbors: (id, query) => Promise.resolve(neighbors(id, query)),
  tagCatalog: () => Promise.resolve(journal.tagCatalog()),
  saveNotes: (id, html) => run(() => journal.saveNotes(target(id), html)),
  setTradeTag: (id, tagId, on) => run(() => journal.setTradeTag(target(id), tagId, on)),
  createTag: (groupId, name) => run(() => journal.createTag(groupId, name)),
  createGroup: (name) => run(() => (journal.createGroup(name), journal.tagCatalog())),
  renameGroup: (id, name) => run(() => (journal.renameGroup(id, name), journal.tagCatalog())),
  deleteGroup: (id) => run(() => (journal.deleteGroup(id), journal.tagCatalog())),
  editTag: (id, edit) => run(() => (journal.editTag(id, edit), journal.tagCatalog())),
  mergeTag: (id, intoId) => run(() => (journal.mergeTag(id, intoId), journal.tagCatalog())),
  deleteTag: (id) => run(() => (journal.deleteTag(id), journal.tagCatalog())),
  addScreenshots: (id, files) => run(() => journal.addScreenshots(target(id), files)),
  deleteScreenshot: (id, screenshotId) => run(() => journal.deleteScreenshot(target(id), screenshotId)),
  accounts: () => Promise.resolve(accounts.activeAccountRefs()),
  previewImport: (accountId, file) => imports.previewImport(accountId, file),
  commitImport: (accountId, file) => imports.commitImport(accountId, file),
  imports: () => Promise.resolve(imports.listImports()),
  undoPreview: (importId) => run(() => imports.undoPreview(importId)),
  undoImport: (importId) => run(() => imports.undoImport(importId)),
  accountOverviews: () => Promise.resolve(accounts.overviews()),
  businessTotals: () => Promise.resolve(accounts.businessTotals()),
  createAccount: (input) => run(() => accounts.createAccount(input)),
  updateAccount: (id, input) => run(() => accounts.updateAccount(id, input)),
  updateRules: (id, input) => run(() => accounts.updateRules(id, input)),
  archiveAccount: (id, input) => run(() => accounts.archiveAccount(id, input)),
  restoreAccount: (id) => run(() => accounts.restoreAccount(id)),
  fees: (id) => run(() => accounts.feesOf(id)),
  setFee: (id, root, rate) => run(() => accounts.setFee(id, root, rate)),
  removeFee: (id, root) => run(() => accounts.removeFee(id, root)),
  ledger: (id) => run(() => accounts.ledgerOf(id)),
  addLedgerEntry: (id, input) => run(() => accounts.addEntry(id, input)),
  deleteLedgerEntry: (id, entryId) => run(() => accounts.deleteEntry(id, entryId)),
}

/** Runs a write and answers as a promise, so a refusal arrives as a rejection, as it will from the server. */
function run<T>(write: () => T): Promise<T> {
  try {
    return Promise.resolve(write())
  } catch (error) {
    return Promise.reject(error instanceof Error ? error : new Error('The change was refused.'))
  }
}
