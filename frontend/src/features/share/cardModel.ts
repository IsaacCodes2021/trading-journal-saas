import { sanitize } from '../../components/SafeHtml'
import type { TagRef, TradeDetail } from '../../lib/data/model'
import type { Direction } from '../../lib/filters'
import {
  formatAmount,
  formatClock,
  formatDuration,
  formatLongDay,
  formatPnl,
  formatPoints,
  formatPrice,
  formatZone,
  pnlTone,
  type Tone,
} from '../../lib/format'

/*
 * What goes on a share card, as strings: a pure function of the trade, the options picked in the pop-up, the username
 * and the viewer's time zone. The card component only lays these out, so everything a person reads on the card is
 * decided (and tested) here.
 */

export type CardStyle = 'gold' | 'holo' | 'paper'
export type Amounts = 'dollars' | 'points'

export const CARD_STYLES: readonly { value: CardStyle; label: string }[] = [
  { value: 'gold', label: 'Gold foil' },
  { value: 'holo', label: 'Holographic' },
  { value: 'paper', label: 'Paper' },
]

export interface CardOptions {
  style: CardStyle
  shot: boolean // the caller has already checked that there is a screenshot to show
  notes: boolean
  tags: boolean
  account: boolean
  signature: boolean
  amounts: Amounts
}

export interface CardLeg {
  label: string // "Bought", "Sold", "Qty", "Hold"
  value: string
  sub: string // a time, "contracts", points; "" leaves the line blank
}

/** What a card is about: one trade, one trading day, or one week. */
export type CardKind = 'trade' | 'day' | 'week'

/** A day or week card's best or worst trade, as one line of the card. */
export interface CardExtreme {
  label: string // "Best trade", "Worst trade", "Only trade"
  symbol: string
  direction: Direction
  when: string // "09:42 ET"; on a week card "Mon 09:42 ET"
  value: string // "+$450.00", or "+18.25 pts" in Points only
  sub: string // "+18.25 pts · 2 contracts", or "2 contracts"
  tone: Tone
}

export interface CardModel {
  kind: CardKind
  style: CardStyle
  number: string // "00042"
  sealText: string
  title: string
  heading: string // the contract on a trade's card ("NQZ6"), the day or the week on the others
  direction: Direction | null // a trade's; none on a day or week card
  meta: string // "Fri, Nov 13, 2026 · Funded 50K"
  tone: Tone // of the figure shown: a flat result gets the win foil and plain ink
  resultLabel: string
  result: string
  resultLong: boolean // more than 10 characters: one size down, so it never runs into the seal
  sub: string
  legs: CardLeg[]
  extremes: CardExtreme[] | null // a day or week card's best and worst trades, in the screenshot's place
  pointsOnly: boolean
  shot: boolean
  notes: string | null
  tags: TagRef[] | null
  signature: string | null
}

/** "00001": the trade's number, at least five digits. */
export function cardNumber(n: number): string {
  return String(n).padStart(5, '0')
}

const BLOCKS = new Set(['P', 'LI', 'BLOCKQUOTE', 'H1', 'H2', 'H3', 'UL', 'OL'])

/** A trade's notes as one line of plain text: sanitized, then the text alone (bold, links and lists flattened), with a
 * space where one paragraph or list item ends and the next begins, and runs of white space collapsed. */
export function notesText(html: string): string {
  if (html.trim() === '') return ''
  const doc = new DOMParser().parseFromString(sanitize(html), 'text/html') // inert: nothing in it runs or loads
  const parts: string[] = []
  const walk = (node: Node) => {
    node.childNodes.forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) {
        parts.push(child.textContent ?? '')
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const name = (child as Element).tagName
        if (name === 'BR') parts.push(' ')
        walk(child)
        if (BLOCKS.has(name)) parts.push(' ')
      }
    })
  }
  walk(doc.body)
  return parts.join('').replace(/\s+/g, ' ').trim()
}

export interface CardContext {
  username: string | null
  timeZone: string
}

export function cardModel(trade: TradeDetail, options: CardOptions, { username, timeZone }: CardContext): CardModel {
  const pointsOnly = options.amounts === 'points'
  const result = pointsOnly ? `${formatPoints(trade.points)} pts` : formatPnl(trade.net_pnl)
  const at = (iso: string) => `${formatClock(iso, timeZone)} ${formatZone(timeZone, iso)}`
  const long = trade.direction === 'long'
  // In the order they happened: a long buys first, a short sells first.
  const legs: CardLeg[] = [
    { label: long ? 'Bought' : 'Sold', value: formatPrice(trade.avg_entry), sub: at(trade.entry_at) },
    { label: long ? 'Sold' : 'Bought', value: formatPrice(trade.avg_exit), sub: at(trade.exit_at) },
  ]
  if (!pointsOnly) {
    legs.push({
      label: 'Qty',
      value: trade.total_qty.toLocaleString('en-US'),
      sub: trade.total_qty === 1 ? 'contract' : 'contracts',
    })
  }
  legs.push({
    label: 'Hold',
    value: formatDuration(trade.hold_seconds),
    sub: pointsOnly ? '' : `${formatPoints(trade.points)} pts`,
  })
  const number = cardNumber(trade.number)
  const notes = options.notes ? notesText(trade.notes_html) : ''
  return {
    kind: 'trade',
    style: options.style,
    number,
    sealText: `ROUNDTURN · JOURNALED TRADE · No. ${number} ·`,
    title: options.style === 'holo' ? 'Journaled trade' : 'Certificate of Trade',
    heading: trade.symbol,
    direction: trade.direction,
    meta: formatLongDay(trade.trade_day) + (options.account ? ` · ${trade.account.name}` : ''),
    tone: pnlTone(pointsOnly ? trade.points : trade.net_pnl),
    resultLabel: pointsOnly ? 'Result' : 'Net P&L',
    result,
    resultLong: result.length > 10,
    sub: pointsOnly ? 'per contract' : `Gross ${formatPnl(trade.gross_pnl)} · Fees ${formatAmount(trade.fees)}`,
    legs,
    extremes: null,
    pointsOnly,
    shot: options.shot,
    notes: notes === '' ? null : notes,
    tags: options.tags && trade.tags.length > 0 ? trade.tags : null,
    signature: options.signature && username ? username : null,
  }
}
