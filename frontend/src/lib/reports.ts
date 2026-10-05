import Decimal from 'decimal.js'
import { REPORT_BYS, type ReportBy, type ReportGroup } from './data/model'

/** What each kind of report is called, and the one line that says how its groups are made. */
export const REPORT_KINDS: Record<ReportBy, { label: string; heading: string; how: string }> = {
  symbol: {
    label: 'Contract',
    heading: 'Contract',
    how: 'Grouped by contract, so the monthly roll does not split one.',
  },
  weekday: { label: 'Weekday', heading: 'Weekday', how: 'The weekday of the trading day, Monday first.' },
  hour: { label: 'Time of day', heading: 'Hour of entry', how: 'The hour each trade was entered, in your timezone.' },
  hold: { label: 'Hold time', heading: 'Held for', how: 'How long each trade lasted, from entry to exit.' },
  tag: {
    label: 'Tag',
    heading: 'Tag',
    how: 'A trade counts under every tag it has, so the rows add up to more than the trades.',
  },
  account: { label: 'Account', heading: 'Account', how: 'One row for each account that has trades in view.' },
}

/** The report a page address names, or undefined for anything else (the router keeps what it cannot check). */
export function parseBy(value: unknown): ReportBy | undefined {
  return REPORT_BYS.find((by) => by === value)
}

/** The widest result in a report, as a positive amount: the length every bar is measured against. */
export function largestResult(groups: readonly ReportGroup[]): string {
  return groups.reduce((most, g) => Decimal.max(most, new Decimal(g.summary.net_pnl).abs()), new Decimal(0)).toFixed(2)
}

/** How much of the bar's room a result takes: 0 for nothing, 1 for the largest. Both are decimal strings. */
export function barShare(net: string, largest: string): number {
  const top = new Decimal(largest)
  if (top.isZero()) return 0
  return Math.min(1, new Decimal(net).abs().div(top).toNumber())
}
