import Decimal from 'decimal.js'
import type { DailyResult } from './data/model'

/*
 * Month and day arithmetic for the calendar and day pages. A trading day is a plain date ("2026-09-30") with no time
 * zone, so everything here works in UTC on purpose: it never depends on the browser's own zone, and a month always has
 * the days the calendar says it has. The range 2000 to 2100 is the server's: it refuses a note for any other day.
 */

export const FIRST_MONTH = '2000-01'
export const LAST_MONTH = '2100-12'
export const FIRST_DAY = '2000-01-01'
export const LAST_DAY = '2100-12-31'

const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/
const MS_PER_DAY = 86_400_000

const utc = (day: string) => new Date(`${day}T00:00:00Z`)
const iso = (date: Date) => date.toISOString().slice(0, 10)
const pad = (n: number) => String(n).padStart(2, '0')

/** "2026-09" when `value` is a month between the first and last the app supports, otherwise undefined. */
export function parseMonth(value: unknown): string | undefined {
  if (typeof value !== 'string' || !MONTH.test(value)) return undefined
  return value >= FIRST_MONTH && value <= LAST_MONTH ? value : undefined
}

/** Whether `value` is a real calendar day ("2026-02-30" is not) between the first and last the app supports. */
export function isDay(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const parts = DAY.exec(value)
  if (!parts || monthOf(value) < FIRST_MONTH || monthOf(value) > LAST_MONTH) return false
  const date = utc(value)
  return !Number.isNaN(date.getTime()) && iso(date) === value // a day that rolled over into the next month is not real
}

export function monthOf(day: string): string {
  return day.slice(0, 7)
}

/** The month `by` months from `month` ("2026-01" and -1 give "2025-12"). */
export function shiftMonth(month: string, by: number): string {
  const [year = 0, number = 1] = month.split('-').map(Number)
  const index = year * 12 + (number - 1) + by
  return `${String(Math.floor(index / 12)).padStart(4, '0')}-${pad((index % 12) + 1)}`
}

/** The first and last day of a month, as trading days. */
export function monthRange(month: string): { start: string; end: string } {
  const [year = 0, number = 1] = month.split('-').map(Number)
  const last = new Date(Date.UTC(year, number, 0)).getUTCDate()
  return { start: `${month}-01`, end: `${month}-${pad(last)}` }
}

/** "September 2026". */
export function monthLabel(month: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    utc(`${month}-01`),
  )
}

/** "Friday, January 5, 2024". */
export function dayLabel(day: string): string {
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(utc(day))
}

/** The day `by` days from `day`. */
export function shiftDay(day: string, by: number): string {
  return iso(new Date(utc(day).getTime() + by * MS_PER_DAY))
}

export interface CalendarCell {
  day: string
  number: number // the day of the month, for the cell's corner
  result: DailyResult | undefined // what traded that day; undefined when nothing did
  noted: boolean // the day has a note
}

export interface CalendarWeek {
  /** Monday to Sunday; null for a day that belongs to the month before or after. */
  cells: (CalendarCell | null)[]
  net: string | null // the week's result within this month; null when nothing traded
  trades: number
}

/** The weeks of a month, Monday first, each with its own total. */
export function buildWeeks(month: string, daily: readonly DailyResult[], noted: readonly string[]): CalendarWeek[] {
  const results = new Map(daily.map((d) => [d.day, d]))
  const notes = new Set(noted)
  const { start, end } = monthRange(month)
  const count = Number(end.slice(8))
  const offset = (utc(start).getUTCDay() + 6) % 7 // Monday is 0
  const weeks: CalendarWeek[] = []
  for (let first = 1 - offset; first <= count; first += 7) {
    const cells: (CalendarCell | null)[] = []
    let net = new Decimal(0)
    let trades = 0
    for (let number = first; number < first + 7; number++) {
      if (number < 1 || number > count) {
        cells.push(null)
        continue
      }
      const day = `${month}-${pad(number)}`
      const result = results.get(day)
      if (result) {
        net = net.plus(result.net_pnl)
        trades += result.trade_count
      }
      cells.push({ day, number, result, noted: notes.has(day) })
    }
    weeks.push({ cells, net: trades > 0 ? net.toFixed(2) : null, trades })
  }
  return weeks
}

/** How strongly to shade a day, 0 (none) to 3, by its size against the month's largest day (by size, win or loss). */
export function shade(net: string, largest: string): 0 | 1 | 2 | 3 {
  const size = new Decimal(net).abs()
  const top = new Decimal(largest).abs()
  if (size.isZero() || top.isZero()) return 0
  const share = size.div(top)
  return share.gte(0.66) ? 3 : share.gte(0.33) ? 2 : 1
}

/** The best (`most`) or worst day by net result; the earlier one when two tie; undefined when nothing traded. */
export function extremeDay(daily: readonly DailyResult[], which: 'best' | 'worst'): DailyResult | undefined {
  let found: DailyResult | undefined
  for (const day of daily) {
    if (!found) found = day
    else if (which === 'best' ? new Decimal(day.net_pnl).gt(found.net_pnl) : new Decimal(day.net_pnl).lt(found.net_pnl))
      found = day
  }
  return found
}

/** The size of the biggest day in the month, win or loss, as a decimal string ("0.00" when nothing traded). */
export function largestDay(daily: readonly DailyResult[]): string {
  return daily.reduce((top, d) => Decimal.max(top, new Decimal(d.net_pnl).abs()), new Decimal(0)).toFixed(2)
}
