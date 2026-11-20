import { weekLabel } from '../../lib/calendar'
import type { CardTrade, PeriodCard, SharePeriod } from '../../lib/data/model'
import {
  formatAmount,
  formatHourMinute,
  formatLongDay,
  formatPercent,
  formatPnl,
  formatPoints,
  formatZone,
  pnlTone,
} from '../../lib/format'
import {
  cardNumber,
  notesText,
  type Amounts,
  type CardContext,
  type CardExtreme,
  type CardLeg,
  type CardModel,
  type CardStyle,
} from './cardModel'

/*
 * What goes on a day's or a week's share card, as strings: like `cardModel` for a trade, a pure function of the numbers,
 * the options picked in the pop-up, the username and the viewer's time zone.
 */

export interface PeriodOptions {
  style: CardStyle
  notes: boolean // the day's note; a week has none
  account: boolean
  signature: boolean
  amounts: Amounts // Points only hides dollars and fees; trades and contracts stay
}

const contracts = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'contract' : 'contracts'}`

/** "Mon" from a trading day. A trading day has no time zone, so it is read as UTC. */
const weekday = (day: string) =>
  new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'UTC' }).format(new Date(`${day}T00:00:00Z`))

function line(label: string, trade: CardTrade, pointsOnly: boolean, week: boolean, timeZone: string): CardExtreme {
  const time = `${formatHourMinute(trade.entry_at, timeZone)} ${formatZone(timeZone, trade.entry_at)}`
  const points = `${formatPoints(trade.points)} pts`
  return {
    label,
    symbol: trade.symbol,
    direction: trade.direction,
    when: week ? `${weekday(trade.trade_day)} ${time}` : time,
    value: pointsOnly ? points : formatPnl(trade.net_pnl),
    sub: pointsOnly ? contracts(trade.total_qty) : `${points} · ${contracts(trade.total_qty)}`,
    tone: pnlTone(pointsOnly ? trade.points : trade.net_pnl),
  }
}

/** The best and the worst trade, by money or, in Points only, by points: one line when there was only one trade. */
function extremes(card: PeriodCard, pointsOnly: boolean, week: boolean, timeZone: string): CardExtreme[] | null {
  const best = pointsOnly ? card.best_points : card.best
  const worst = pointsOnly ? card.worst_points : card.worst
  if (!best || !worst) return null
  if (card.summary.trade_count === 1) return [line('Only trade', best, pointsOnly, week, timeZone)]
  return [line('Best trade', best, pointsOnly, week, timeZone), line('Worst trade', worst, pointsOnly, week, timeZone)]
}

function accountsText(names: readonly string[]): string {
  if (names.length === 0) return ''
  return names.length === 1 ? (names[0] ?? '') : `${names.length} accounts`
}

export function periodCardModel(
  card: PeriodCard,
  period: SharePeriod,
  options: PeriodOptions,
  { username, timeZone }: CardContext,
  noteHtml = '',
): CardModel {
  const week = period.kind === 'week'
  const pointsOnly = options.amounts === 'points'
  const s = card.summary
  const result = pointsOnly ? `${formatPoints(card.net_points)} pts` : formatPnl(s.net_pnl)

  const trades: CardLeg = {
    label: 'Trades',
    value: s.trade_count.toLocaleString('en-US'),
    sub: `${s.win_count} W · ${s.loss_count} L`,
  }
  const size: CardLeg = { label: 'Contracts', value: card.contracts.toLocaleString('en-US'), sub: '' }
  const points: CardLeg = { label: 'Net points', value: formatPoints(card.net_points), sub: 'per contract' }
  const winRate: CardLeg = {
    label: 'Win rate',
    value: s.win_rate === null ? '—' : formatPercent(s.win_rate, 0),
    sub: '',
  }
  const days: CardLeg = {
    label: 'Days',
    value: String(s.trading_days),
    sub: `${s.winning_days} up · ${s.losing_days} down`,
  }
  // In Points only the net points are the result, so their place goes to the win rate (a day has room for both).
  const legs = week
    ? [days, trades, size, pointsOnly ? winRate : points]
    : pointsOnly
      ? [trades, size, winRate]
      : [trades, size, points, winRate]

  const number = cardNumber(card.number)
  const notes = !week && options.notes ? notesText(noteHtml) : ''
  return {
    kind: period.kind,
    style: options.style,
    number,
    sealText: `ROUNDTURN · TRADING ${week ? 'WEEK' : 'DAY'} · No. ${number} ·`,
    title: options.style === 'holo' ? `Journaled ${period.kind}` : `Certificate of the ${week ? 'Week' : 'Day'}`,
    heading: week ? weekLabel(card.start) : formatLongDay(card.start),
    direction: null,
    meta: options.account ? accountsText(card.accounts) : '',
    tone: pnlTone(pointsOnly ? card.net_points : s.net_pnl),
    resultLabel: pointsOnly ? 'Net points' : 'Net P&L',
    result,
    resultLong: result.length > 10,
    sub: pointsOnly ? 'per contract, added up' : `Gross ${formatPnl(s.gross_pnl)} · Fees ${formatAmount(s.fees)}`,
    legs,
    extremes: extremes(card, pointsOnly, week, timeZone),
    pointsOnly,
    shot: false,
    notes: notes === '' ? null : notes,
    tags: null,
    signature: options.signature && username ? username : null,
  }
}
