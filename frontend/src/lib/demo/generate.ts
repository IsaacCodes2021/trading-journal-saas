import Decimal from 'decimal.js'
import type { AccountRef, TagRef, TradeRow } from '../data/model'

/*
 * Sample data for the pages, until the real stats and trades endpoints exist (M3). Deterministic: the same seed gives
 * the same 132 trades, over the same 22 trading days, every time, so tests and screenshots are repeatable. Prices,
 * P&L and fees are exact decimals built from whole ticks, like the real data.
 *
 * This is NOT product code: nothing here is reachable once a page uses the API source, and the app shows a "Sample
 * data" banner while it is in use.
 */

export const SAMPLE_ACCOUNTS: AccountRef[] = [
  { id: 1, name: 'Apex 50K #1', firm: 'Apex', account_type: 'funded' },
  { id: 2, name: 'Apex 150K', firm: 'Apex', account_type: 'funded' },
  { id: 3, name: 'Lucid 100K', firm: 'Lucid', account_type: 'evaluation' },
  { id: 4, name: 'Apex 50K #2', firm: 'Apex', account_type: 'evaluation' },
]

export const SAMPLE_TAGS = {
  aPlus: { id: 1, name: 'A+ setup', color: '#6EA8FE' },
  calm: { id: 2, name: 'Calm', color: '#34D399' },
  fomo: { id: 3, name: 'FOMO', color: '#F5C26B' },
  chased: { id: 4, name: 'Chased entry', color: '#F5C26B' },
  oversized: { id: 5, name: 'Oversized', color: '#F5C26B' },
  revenge: { id: 6, name: 'Revenge', color: '#F87171' },
  againstTrend: { id: 7, name: 'Against trend', color: '#F87171' },
} satisfies Record<string, TagRef>

interface Instrument {
  root: string
  contract: string
  pointValue: number // dollars per point per contract
  tick: string
  base: number
  fee: string // round-turn fee per contract
  maxQty: number
  weight: number
}

const INSTRUMENTS: Instrument[] = [
  { root: 'NQ', contract: 'NQZ6', pointValue: 20, tick: '0.25', base: 20400, fee: '2.20', maxQty: 3, weight: 4 },
  { root: 'MNQ', contract: 'MNQZ6', pointValue: 2, tick: '0.25', base: 20400, fee: '0.74', maxQty: 10, weight: 3 },
  { root: 'ES', contract: 'ESZ6', pointValue: 50, tick: '0.25', base: 5860, fee: '2.20', maxQty: 3, weight: 2 },
  { root: 'GC', contract: 'GCZ6', pointValue: 100, tick: '0.10', base: 3845, fee: '2.50', maxQty: 2, weight: 1 },
  { root: 'CL', contract: 'CLX6', pointValue: 1000, tick: '0.01', base: 71.8, fee: '2.50', maxQty: 2, weight: 1 },
]

/** A small seeded generator (mulberry32): same seed, same sequence. */
function seeded(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** The trading days of the sample: every weekday from 3 Sep to 2 Oct 2026, 22 of them. */
export function sampleDays(): string[] {
  const days: string[] = []
  for (
    let d = new Date(Date.UTC(2026, 8, 3));
    d <= new Date(Date.UTC(2026, 9, 2));
    d = new Date(d.getTime() + 86_400_000)
  ) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) days.push(d.toISOString().slice(0, 10))
  }
  return days
}

const TRADES_PER_DAY = 6

function pick<T>(random: () => number, items: readonly T[]): T {
  return items[Math.floor(random() * items.length) % items.length] as T
}

function weighted(random: () => number): Instrument {
  const total = INSTRUMENTS.reduce((sum, i) => sum + i.weight, 0)
  let roll = random() * total
  for (const instrument of INSTRUMENTS) {
    roll -= instrument.weight
    if (roll < 0) return instrument
  }
  return INSTRUMENTS[0] as Instrument
}

/** 09:30 New York in September and October is 13:30 UTC (daylight time). */
function utcIso(day: string, minutesAfterOpen: number, seconds = 0): string {
  const base = new Date(`${day}T13:30:00Z`).getTime()
  return new Date(base + minutesAfterOpen * 60_000 + seconds * 1000).toISOString().replace('.000Z', 'Z')
}

function money(value: Decimal): string {
  return value.toFixed(2)
}

export function generateTrades(seed = 20_260_903): TradeRow[] {
  const random = seeded(seed)
  const trades: TradeRow[] = []
  for (const day of sampleDays()) {
    let minute = 2 + Math.floor(random() * 10)
    for (let n = 0; n < TRADES_PER_DAY; n++) {
      const instrument = weighted(random)
      const account = pick(random, SAMPLE_ACCOUNTS)
      const qty = 1 + Math.floor(random() * instrument.maxQty)
      const long = random() < 0.52
      const win = random() < 0.58
      const tick = new Decimal(instrument.tick)
      const moveTicks = win ? 8 + Math.floor(random() * 56) : 6 + Math.floor(random() * 38)
      const drift = new Decimal(Math.floor(random() * 120) - 60).times(tick)
      const entry = new Decimal(instrument.base).plus(drift).toDecimalPlaces(tick.decimalPlaces())
      const signedMove = tick
        .times(moveTicks)
        .times(win ? 1 : -1)
        .times(long ? 1 : -1)
      const exit = entry.plus(signedMove)
      const gross = signedMove
        .times(long ? 1 : -1)
        .times(instrument.pointValue)
        .times(qty)
      const fees = new Decimal(instrument.fee).times(qty)
      const hold = 60 + Math.floor(random() * 2400)
      const entryAt = utcIso(day, minute, Math.floor(random() * 50))
      const exitAt = new Date(new Date(entryAt).getTime() + hold * 1000).toISOString().replace('.000Z', 'Z')
      const net = gross.minus(fees)
      const tags = tagsFor(random, net.isPositive())
      trades.push({
        id: 0, // assigned below, in time order
        account,
        symbol: instrument.contract,
        symbol_root: instrument.root,
        direction: long ? 'long' : 'short',
        entry_at: entryAt,
        exit_at: exitAt,
        trade_day: day,
        hold_seconds: hold,
        total_qty: qty,
        avg_entry: entry.toFixed(tick.decimalPlaces()),
        avg_exit: exit.toFixed(tick.decimalPlaces()),
        gross_pnl: money(gross),
        fees: money(fees),
        net_pnl: money(net),
        tags,
        has_notes: random() < 0.55,
        screenshot_count: random() < 0.35 ? 1 + Math.floor(random() * 3) : 0,
      })
      minute += 18 + Math.floor(random() * 50)
    }
  }
  trades.sort((a, b) => (a.exit_at < b.exit_at ? -1 : a.exit_at > b.exit_at ? 1 : 0))
  return trades.map((trade, index) => ({ ...trade, id: index + 1 }))
}

function tagsFor(random: () => number, winner: boolean): TagRef[] {
  const roll = random()
  if (winner) return roll < 0.45 ? [SAMPLE_TAGS.aPlus] : roll < 0.7 ? [SAMPLE_TAGS.calm] : []
  if (roll < 0.2) return []
  return [
    pick(random, [
      SAMPLE_TAGS.fomo,
      SAMPLE_TAGS.chased,
      SAMPLE_TAGS.oversized,
      SAMPLE_TAGS.revenge,
      SAMPLE_TAGS.againstTrend,
    ]),
  ]
}

export const SAMPLE_TRADES: readonly TradeRow[] = generateTrades()
