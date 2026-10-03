import Decimal from 'decimal.js'
import type {
  DashboardData,
  DataSource,
  FilterOptions,
  TradeRow,
  TradeSort,
  TradesPage,
  TradesQuery,
} from '../data/model'
import type { Filters } from '../filters'
import { SAMPLE_ACCOUNTS, SAMPLE_TRADES } from './generate'
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

function trades(query: TradesQuery): TradesPage {
  const filtered = applyFilters(SAMPLE_TRADES, query, LAST_DAY).filter((t) => matches(t, query.q ?? ''))
  const ordered = sortTrades(filtered, query.sort)
  const start = (Math.max(1, query.page) - 1) * query.per_page
  return {
    items: ordered.slice(start, start + query.per_page),
    total: filtered.length,
    net_pnl: filtered.reduce((total, t) => total.plus(t.net_pnl), new Decimal(0)).toFixed(2),
  }
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
}
