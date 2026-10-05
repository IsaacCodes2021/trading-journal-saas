import Decimal from 'decimal.js'
import type { ReportBy, ReportData, ReportGroup, TradeRow } from '../data/model'
import { summarize } from './stats'

/*
 * The sample source's reports: a port of the backend's `api/reports.py`, so the sample groups are made by the same rules.
 * The real app never runs this: the server groups the trades and computes every statistic.
 */

/** The sample data is New York futures trading, so its hours are read in New York. A real account uses its own. */
export const SAMPLE_TIMEZONE = 'America/New_York'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/** [label, the first number of seconds that is NOT in the step]. The last step has no end. */
const HOLD_STEPS: readonly (readonly [string, number | null])[] = [
  ['Under 1 minute', 60],
  ['1 to 5 minutes', 300],
  ['5 to 15 minutes', 900],
  ['15 to 30 minutes', 1800],
  ['30 to 60 minutes', 3600],
  ['1 to 2 hours', 7200],
  ['Over 2 hours', null],
]

/** A step includes its lower edge and excludes its upper: 60 seconds is "1 to 5 minutes". */
function holdStep(seconds: number): number {
  const index = HOLD_STEPS.findIndex(([, end]) => end === null || seconds < end)
  return index < 0 ? HOLD_STEPS.length - 1 : index
}

const hourFormat = new Intl.DateTimeFormat('en-US', { hour: '2-digit', hourCycle: 'h23', timeZone: SAMPLE_TIMEZONE })

/** The hour (0 to 23) a trade was entered, in the sample timezone. */
function hourOf(entryAt: string): number {
  return Number(hourFormat.format(new Date(entryAt)))
}

const net = (trades: readonly TradeRow[]) => trades.reduce((total, t) => total.plus(t.net_pnl), new Decimal(0))

function group(key: string, label: string, detail: string | null, trades: readonly TradeRow[]): ReportGroup {
  return { key, label, detail, summary: summarize(trades) }
}

type Described = Map<string, { label: string; detail: string | null; trades: TradeRow[] }>

function add(groups: Described, key: string, label: string, detail: string | null, trade: TradeRow) {
  const found = groups.get(key)
  if (found) found.trades.push(trade)
  else groups.set(key, { label, detail, trades: [trade] })
}

/** Best net result first; ties by label ignoring capitals, then by key, so the order is the same every time. */
function byBestResult(groups: Described): ReportGroup[] {
  return [...groups.entries()]
    .sort(([keyA, a], [keyB, b]) => {
      const order = net(b.trades).comparedTo(net(a.trades))
      if (order !== 0) return order
      const [labelA, labelB] = [a.label.toLowerCase(), b.label.toLowerCase()]
      return labelA < labelB ? -1 : labelA > labelB ? 1 : keyA < keyB ? -1 : keyA > keyB ? 1 : 0
    })
    .map(([key, { label, detail, trades }]) => group(key, label, detail, trades))
}

function bucketed(trades: readonly TradeRow[], count: number, indexOf: (t: TradeRow) => number): TradeRow[][] {
  const buckets: TradeRow[][] = Array.from({ length: count }, () => [])
  for (const trade of trades) buckets[indexOf(trade)]?.push(trade)
  return buckets
}

/** `trades` in the order they closed; `categories` names a tag's category by its id (read only for tags). */
export function report(by: ReportBy, trades: readonly TradeRow[], categories: ReadonlyMap<number, string>): ReportData {
  const summary = summarize(trades)
  const groups = new Map() as Described
  if (by === 'symbol') {
    for (const t of trades) add(groups, t.symbol_root, t.symbol_root, null, t)
    return { by, groups: byBestResult(groups), summary }
  }
  if (by === 'account') {
    for (const t of trades) {
      const kind = t.account.account_type === 'funded' ? 'Funded' : 'Evaluation'
      add(groups, String(t.account.id), t.account.name, `${t.account.firm} · ${kind}`, t)
    }
    return { by, groups: byBestResult(groups), summary }
  }
  if (by === 'tag') {
    for (const t of trades) {
      if (t.tags.length === 0) add(groups, 'untagged', 'Untagged', null, t)
      for (const tag of t.tags) add(groups, String(tag.id), tag.name, categories.get(tag.group_id) ?? null, t)
    }
    return { by, groups: byBestResult(groups), summary }
  }
  if (by === 'weekday') {
    // Monday is 0, as in the backend: JavaScript counts Sunday as 0, so shift by one.
    const days = bucketed(trades, 7, (t) => (new Date(`${t.trade_day}T00:00:00Z`).getUTCDay() + 6) % 7)
    return { by, groups: WEEKDAYS.map((name, i) => group(String(i), name, null, days[i] ?? [])), summary }
  }
  if (by === 'hour') {
    const hours = bucketed(trades, 24, (t) => hourOf(t.entry_at))
    const listed = hours.flatMap((hour, h) =>
      hour.length ? [group(String(h), `${String(h).padStart(2, '0')}:00`, null, hour)] : [],
    )
    return { by, groups: listed, summary }
  }
  const steps = bucketed(trades, HOLD_STEPS.length, (t) => holdStep(t.hold_seconds))
  return { by, groups: HOLD_STEPS.map(([label], i) => group(String(i), label, null, steps[i] ?? [])), summary }
}
