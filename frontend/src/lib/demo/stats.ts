import Decimal from 'decimal.js'
import type {
  AccountSummary,
  DailyResult,
  EquityPoint,
  PerformanceScore,
  ScoreAxis,
  SummaryStats,
  TradeRow,
} from '../data/model'
import type { Filters, Preset } from '../filters'

/*
 * The sample source's statistics. A direct port of the backend's `finance.summarize`, `equity_curve`, `daily_results`
 * and `performance_score`, so sample numbers obey the same definitions (a win is net > 0; max drawdown is peak to
 * trough of the cumulative net, starting at 0; the score has the same six axes and ranges). The real app never runs
 * this: the server computes every statistic.
 */

const ZERO = new Decimal(0)

export const SCORE_RULES: Record<string, string> = {
  'Win rate': 'Winning trades ÷ (winning + losing trades). 60% or higher scores 100, scaling down to 0 at 0%.',
  'Profit factor':
    'Total won ÷ total lost. 1.0 or lower scores 0; 2.5 or higher scores 100. No losing trades scores 100.',
  'Avg win/loss': 'Average win ÷ average loss. 2.5 or higher scores 100, scaling down to 0 at 0.',
  Consistency:
    'Share of winning-day profit that came from your single best day. 20% or less scores 100; 100% (one day made it all) scores 0.',
  'Max drawdown':
    'Deepest drop in cumulative net P&L as a share of total won. 0% scores 100; giving back 100% or more scores 0.',
  'Recovery factor': 'Net P&L ÷ max drawdown. 3 or higher scores 100; 0 or lower scores 0.',
}

const dec = (value: string) => new Decimal(value)
const ratio = (top: Decimal, bottom: Decimal): number | null => (bottom.isZero() ? null : top.div(bottom).toNumber())
const sum = (values: Decimal[]) => values.reduce((total, v) => total.plus(v), ZERO)

function atMost1dp(value: number): number {
  return Math.round(value * 10) / 10
}

/** Map a value linearly so `low` becomes 0 and `high` 100, clamped. */
function scale(value: number, low: number, high: number): number {
  return atMost1dp(Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100)))
}

export function equityCurve(nets: Decimal[]): { equity: Decimal; drawdown: Decimal }[] {
  let equity = ZERO
  let peak = ZERO
  return nets.map((net) => {
    equity = equity.plus(net)
    peak = Decimal.max(peak, equity)
    return { equity, drawdown: equity.minus(peak) }
  })
}

export function dailyResults(trades: readonly TradeRow[]): DailyResult[] {
  const totals = new Map<string, { net: Decimal; count: number }>()
  for (const trade of trades) {
    const day = totals.get(trade.trade_day) ?? { net: ZERO, count: 0 }
    totals.set(trade.trade_day, { net: day.net.plus(trade.net_pnl), count: day.count + 1 })
  }
  return [...totals.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([day, { net, count }]) => ({ day, net_pnl: net.toFixed(2), trade_count: count }))
}

export function summarize(trades: readonly TradeRow[]): SummaryStats {
  const nets = trades.map((t) => dec(t.net_pnl))
  const wins = nets.filter((n) => n.greaterThan(0))
  const losses = nets.filter((n) => n.lessThan(0))
  const totalWon = sum(wins)
  const totalLost = sum(losses)
  const net = sum(nets)
  const days = dailyResults(trades)
  const avgWin = wins.length ? totalWon.div(wins.length) : null
  const avgLoss = losses.length ? totalLost.div(losses.length) : null
  const curve = equityCurve(nets)
  const worst = curve.reduce((low, point) => Decimal.min(low, point.drawdown), ZERO)
  const fixed = (value: Decimal | null) => (value === null ? null : value.toFixed(2))
  return {
    trade_count: trades.length,
    account_count: new Set(trades.map((t) => t.account.id)).size,
    win_count: wins.length,
    loss_count: losses.length,
    breakeven_count: nets.length - wins.length - losses.length,
    gross_pnl: sum(trades.map((t) => dec(t.gross_pnl))).toFixed(2),
    fees: sum(trades.map((t) => dec(t.fees))).toFixed(2),
    net_pnl: net.toFixed(2),
    total_won: totalWon.toFixed(2),
    total_lost: totalLost.toFixed(2),
    win_rate: ratio(new Decimal(wins.length), new Decimal(wins.length + losses.length)),
    profit_factor: ratio(totalWon, totalLost.neg()),
    avg_win: fixed(avgWin),
    avg_loss: fixed(avgLoss),
    win_loss_ratio: avgWin && avgLoss ? ratio(avgWin, avgLoss.neg()) : null,
    expectancy: trades.length ? net.div(trades.length).toFixed(2) : null,
    largest_win: wins.length ? Decimal.max(...wins).toFixed(2) : null,
    largest_loss: losses.length ? Decimal.min(...losses).toFixed(2) : null,
    trading_days: days.length,
    winning_days: days.filter((d) => dec(d.net_pnl).greaterThan(0)).length,
    losing_days: days.filter((d) => dec(d.net_pnl).lessThan(0)).length,
    max_drawdown: worst.neg().toFixed(2),
  }
}

export function equityByTrade(trades: readonly TradeRow[]): EquityPoint[] {
  const ordered = [...trades].sort((a, b) => (a.exit_at < b.exit_at ? -1 : a.exit_at > b.exit_at ? 1 : 0))
  return equityCurve(ordered.map((t) => dec(t.net_pnl))).map((p, i) => ({
    at: ordered[i]?.exit_at ?? '',
    equity: p.equity.toFixed(2),
    drawdown: p.drawdown.toFixed(2),
  }))
}

export function equityByDay(daily: readonly DailyResult[]): EquityPoint[] {
  return equityCurve(daily.map((d) => dec(d.net_pnl))).map((p, i) => ({
    at: daily[i]?.day ?? '',
    equity: p.equity.toFixed(2),
    drawdown: p.drawdown.toFixed(2),
  }))
}

export function performanceScore(summary: SummaryStats, days: readonly DailyResult[]): PerformanceScore | null {
  if (summary.trade_count === 0) return null
  const hasWins = summary.win_count > 0
  const hasLosses = summary.loss_count > 0
  const undefinedScore = (fallback: boolean) => (fallback ? 100 : 0)
  const winningDays = days.map((d) => dec(d.net_pnl)).filter((n) => n.greaterThan(0))
  const best = winningDays.length ? Decimal.max(...winningDays) : null
  const bestShare = best ? best.div(sum(winningDays)).toNumber() : null
  const won = dec(summary.total_won)
  const drawdownShare = won.isZero() ? null : dec(summary.max_drawdown).div(won).toNumber()
  const drawdown = dec(summary.max_drawdown)
  const recovery = drawdown.isZero() ? null : dec(summary.net_pnl).div(drawdown).toNumber()
  const noLosses = hasWins && !hasLosses

  const scores: [string, number][] = [
    ['Win rate', summary.win_rate === null ? 0 : scale(summary.win_rate, 0, 0.6)],
    ['Profit factor', summary.profit_factor === null ? undefinedScore(noLosses) : scale(summary.profit_factor, 1, 2.5)],
    [
      'Avg win/loss',
      summary.win_loss_ratio === null ? undefinedScore(noLosses) : scale(summary.win_loss_ratio, 0, 2.5),
    ],
    ['Consistency', bestShare === null ? 0 : scale(1 - bestShare, 0, 0.8)],
    ['Max drawdown', drawdownShare === null ? 0 : scale(1 - drawdownShare, 0, 1)],
    [
      'Recovery factor',
      recovery === null ? undefinedScore(dec(summary.net_pnl).greaterThan(0)) : scale(recovery, 0, 3),
    ],
  ]
  const axes: ScoreAxis[] = scores.map(([name, score]) => ({ name, score, rule: SCORE_RULES[name] ?? '' }))
  return { overall: atMost1dp(axes.reduce((total, a) => total + a.score, 0) / axes.length), axes }
}

export function accountSummaries(trades: readonly TradeRow[]): AccountSummary[] {
  const byAccount = new Map<number, { trade: TradeRow; count: number; net: Decimal }>()
  for (const trade of trades) {
    const row = byAccount.get(trade.account.id) ?? { trade, count: 0, net: ZERO }
    byAccount.set(trade.account.id, { trade, count: row.count + 1, net: row.net.plus(trade.net_pnl) })
  }
  return [...byAccount.values()]
    .sort((a, b) => b.net.comparedTo(a.net))
    .map(({ trade, count, net }) => ({ account: trade.account, trade_count: count, net_pnl: net.toFixed(2) }))
}

/** The first day of a preset's range, counting back from the last trading day; null means no lower bound. */
export function presetStart(preset: Preset, lastDay: string): string | null {
  if (preset === 'all') return null
  if (preset === 'ytd') return `${lastDay.slice(0, 4)}-01-01`
  const back = { '7d': 6, '30d': 29, '90d': 89 }[preset]
  return new Date(new Date(`${lastDay}T00:00:00Z`).getTime() - back * 86_400_000).toISOString().slice(0, 10)
}

export function applyFilters(trades: readonly TradeRow[], filters: Filters, lastDay: string): TradeRow[] {
  const start = presetStart(filters.preset, lastDay)
  return trades.filter(
    (t) =>
      (start === null || t.trade_day >= start) &&
      (!filters.account_type || t.account.account_type === filters.account_type) &&
      (!filters.firms || filters.firms.includes(t.account.firm)) &&
      (!filters.symbol_roots || filters.symbol_roots.includes(t.symbol_root)) &&
      (!filters.direction || t.direction === filters.direction),
  )
}
