import Decimal from 'decimal.js'
import type { AccountRef, FillPairRow, ScreenshotRef, TagGroupRef, TagRef, TradeRow } from '../data/model'

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

export const SAMPLE_TAG_GROUPS: TagGroupRef[] = [
  { id: 1, name: 'Setup' },
  { id: 2, name: 'Mistake' },
  { id: 3, name: 'Emotion' },
]

export const SAMPLE_TAGS = {
  aPlus: { id: 1, group_id: 1, name: 'A+ setup', color: '#6EA8FE' },
  openingRange: { id: 2, group_id: 1, name: 'Opening range', color: '#6EA8FE' },
  pullback: { id: 3, group_id: 1, name: 'Pullback', color: '#6EA8FE' },
  reversal: { id: 4, group_id: 1, name: 'Reversal', color: '#6EA8FE' },
  fomo: { id: 5, group_id: 2, name: 'FOMO', color: '#F5C26B' },
  chased: { id: 6, group_id: 2, name: 'Chased entry', color: '#F5C26B' },
  oversized: { id: 7, group_id: 2, name: 'Oversized', color: '#F5C26B' },
  againstTrend: { id: 8, group_id: 2, name: 'Against trend', color: '#F87171' },
  calm: { id: 9, group_id: 3, name: 'Calm', color: '#34D399' },
  anxious: { id: 10, group_id: 3, name: 'Anxious', color: '#F5C26B' },
  revenge: { id: 11, group_id: 3, name: 'Revenge', color: '#F87171' },
  overconfident: { id: 12, group_id: 3, name: 'Overconfident', color: '#F5C26B' },
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

// ---- what the detail page adds to a trade ---------------------------------------------------------------------------

const isoNoMillis = (ms: number) => new Date(ms).toISOString().replace('.000Z', 'Z')

/**
 * The fill pairs behind a trade. Derived from the trade itself, from its own seed, so the pairs always add up to the
 * trade: the pair results sum to the gross result, the first pair enters when the trade does and the last exits when it
 * does. (A trade of one contract is one pair; a larger one is up to three.)
 */
export function fillPairsFor(trade: TradeRow): FillPairRow[] {
  const random = seeded(trade.id * 7919 + 13)
  const count = Math.min(trade.total_qty, 1 + Math.floor(random() * 3))
  const base = Math.floor(trade.total_qty / count)
  const extra = trade.total_qty - base * count
  const perContract = new Decimal(trade.gross_pnl).div(trade.total_qty)
  const entryMs = new Date(trade.entry_at).getTime()
  const exitMs = new Date(trade.exit_at).getTime()
  const long = trade.direction === 'long'
  return Array.from({ length: count }, (_, i) => {
    const qty = base + (i < extra ? 1 : 0)
    const enteredAt = isoNoMillis(entryMs + i * Math.floor(random() * 3) * 1000)
    const exitedAt = isoNoMillis(exitMs - (count - 1 - i) * (1 + Math.floor(random() * 3)) * 1000)
    return {
      id: trade.id * 10 + i,
      bought_at: long ? enteredAt : exitedAt,
      sold_at: long ? exitedAt : enteredAt,
      qty,
      buy_price: long ? trade.avg_entry : trade.avg_exit,
      sell_price: long ? trade.avg_exit : trade.avg_entry,
      pnl: perContract.times(qty).toFixed(2),
    }
  })
}

/** Points per contract, positive when the move was in the trade's favour. */
export function pointsFor(trade: TradeRow): string {
  const move = new Decimal(trade.avg_exit).minus(trade.avg_entry)
  return (trade.direction === 'long' ? move : move.neg()).toFixed(2)
}

const WINNER_NOTES = [
  '<p><strong>Plan:</strong> long the retest of the opening range high after the first pullback held. Stop below the 09:35 low, target the prior day high.</p><p><strong>What happened:</strong> entry was clean, no chasing. Let it run to the target. Hesitated for a second before clicking, but the checklist was fully met.</p><ul><li>Waited for the pullback instead of buying the breakout</li><li>Stop placed before entry, size within the daily loss rule</li></ul><p><strong>Improve:</strong> take the same trade next time even when the previous one lost.</p>',
  '<p><strong>Plan:</strong> trade the first pullback in the direction of the morning trend and nothing else.</p><p><strong>What happened:</strong> followed it. Took profit at the first target and stopped for the session.</p>',
  '<p>Patient entry on a second test of the level. Stayed out of the first move, which would have been a chase.</p>',
]

const LOSER_NOTES = [
  '<p><strong>What happened:</strong> entered late after the move had already started, then moved my stop back. Should have taken the first stop.</p><ul><li>Entry was not on the plan</li><li>Size was bigger than my rule</li></ul><p><strong>Improve:</strong> if I miss the entry, wait for the next setup instead of chasing.</p>',
  '<p>Stopped out on the first pullback. The read was fine and the stop was fair; no change needed.</p>',
  '<p><strong>What happened:</strong> traded again right after a loss to get it back. No setup, just frustration.</p><p><strong>Improve:</strong> after two losses in a row, step away for ten minutes.</p>',
]

/** A believable journal note for a trade the sample marks as having one. */
export function notesFor(trade: TradeRow): string {
  const pool = new Decimal(trade.net_pnl).isPositive() ? WINNER_NOTES : LOSER_NOTES
  return pool[trade.id % pool.length] ?? ''
}

const SHOT_NAMES = ['entry.png', 'exit.png', 'context.png']

export function screenshotsFor(trade: TradeRow): ScreenshotRef[] {
  return Array.from({ length: trade.screenshot_count }, (_, i) => ({
    id: trade.id * 10 + i,
    original_name: SHOT_NAMES[i] ?? `shot-${i + 1}.png`,
    created_at: trade.exit_at,
    src: null,
  }))
}
