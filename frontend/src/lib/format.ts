import Decimal from 'decimal.js'
import { formatMoney } from './money'

/** The typographic minus, so a loss lines up with a gain in a column (a hyphen is narrower). */
const MINUS = '−'

export type Tone = 'pos' | 'neg' | 'flat'

export function pnlTone(value: string): Tone {
  const d = new Decimal(value)
  return d.isZero() ? 'flat' : d.isNegative() ? 'neg' : 'pos'
}

/** "+$775.60", "−$304.40", "$0.00". Exact: formats the decimal string, never a float. */
export function formatPnl(value: string): string {
  const tone = pnlTone(value)
  if (tone === 'flat') return formatMoney('0')
  const magnitude = formatMoney(new Decimal(value).abs().toFixed(2))
  return (tone === 'pos' ? '+' : MINUS) + magnitude
}

/** A magnitude with no sign, for "after $612.40 fees" and axis labels. */
export function formatAmount(value: string): string {
  return formatMoney(new Decimal(value).abs().toFixed(2))
}

/** 0.583 to "58.3%". */
export function formatPercent(ratio: number, digits = 1): string {
  return `${(ratio * 100).toFixed(digits)}%`
}

/** 1.8431 to "1.84"; null (no losing trades, so no ratio) to an em dash. */
export function formatRatio(value: number | null, digits = 2): string {
  return value === null ? '—' : value.toFixed(digits)
}

/** 492 to "8m 12s", 45 to "45s", 4000 to "1h 6m". */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

/** Chart axis labels: 10000 to "$10k", 2300 to "$2.3k", 0 to "$0". */
export function formatAxisMoney(value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? MINUS : ''
  if (abs >= 1000) {
    const k = abs / 1000
    return `${sign}$${Number.isInteger(k) ? k : k.toFixed(1)}k`
  }
  return `${sign}$${Math.round(abs)}`
}

/** "Sep 30 · 09:41" in the user's timezone, from an ISO UTC timestamp. */
export function formatTradeTime(iso: string, timeZone: string): string {
  const date = new Date(iso)
  const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone }).format(date)
  const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone }).format(
    date,
  )
  return `${day} · ${time}`
}

/** "Sep 30" from a trading day, "2026-09-30". A trading day has no time zone, so it is formatted as UTC. */
export function formatDay(day: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${day}T00:00:00Z`),
  )
}

/** "9 days left" until an ISO timestamp, or null when there is no expiry or it has passed. */
export function daysLeft(expiresAt: string | null, now: Date = new Date()): number | null {
  if (expiresAt === null) return null
  const ms = new Date(expiresAt).getTime() - now.getTime()
  return ms > 0 ? Math.ceil(ms / 86_400_000) : null
}

export function pluralize(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString('en-US')} ${count === 1 ? one : many}`
}
