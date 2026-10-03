import type { ImportIssue } from '../data/model'

/*
 * A reader for Tradovate "Performance" exports, for the sample import only. The real parser is the backend's
 * `parser.py`, the only one that will ever read a real file (it also does the timezone and the P&L cross-check); this
 * one follows its header and row rules closely enough that the screen shows what the real thing will, and its messages
 * are the backend's. Nothing here leaves the browser.
 */

export const REQUIRED_COLUMNS = [
  'symbol',
  '_priceFormat',
  '_priceFormatType',
  '_tickSize',
  'buyFillId',
  'sellFillId',
  'qty',
  'buyPrice',
  'sellPrice',
  'pnl',
  'boughtTimestamp',
  'soldTimestamp',
  'duration',
] as const

/** Splits CSV text into rows of fields: quoted fields, doubled quotes, and line breaks inside quotes (RFC 4180). */
export function splitCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const c = text.charAt(i)
    if (quoted) {
      if (c === '"' && text.charAt(i + 1) === '"') {
        field += '"'
        i++
      } else if (c === '"') quoted = false
      else field += c
    } else if (c === '"') quoted = true
    else if (c === ',') {
      row.push(field)
      field = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text.charAt(i + 1) === '\n') i++
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else field += c
  }
  if (field !== '' || row.length > 0) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

export interface ParsedPair {
  line: number
  symbol: string
  qty: number
  buyFillId: string
  sellFillId: string
  buyPrice: string
  sellPrice: string
  pnl: string // a decimal string, "-475.00"
  boughtAt: string // as written in the file
  soldAt: string
  boughtMs: number // wall-clock time as if UTC: for ordering and grouping only, never shown
  soldMs: number
}

export interface ParseResult {
  pairs: ParsedPair[]
  errors: ImportIssue[]
  duplicates_in_file: number
  fatal: string | null
}

const DECIMAL = /^[+-]?(?:\d+\.?\d*|\.\d+)$/
const TIMESTAMP = /^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2}):(\d{2})$/

function headerProblem(rows: string[][]): string | null {
  const first = rows.find((r) => r.some((cell) => cell.trim() !== ''))
  if (!first) return 'The file is empty.'
  const header = first.map((c) => c.trim())
  const missing = REQUIRED_COLUMNS.filter((c) => !header.includes(c))
  if (missing.length === 0) return null
  const lead = header[0] ?? ''
  if (lead.startsWith('Working Orders') || lead.startsWith('Completed Orders') || header[0] === 'Account') {
    return 'This looks like an order-history export (Rithmic / R|Trader style), not a Tradovate Performance export. Export Performance from Tradovate instead.'
  }
  return `Not a Tradovate Performance export: missing column(s) ${missing.join(', ')}.`
}

function parseMoney(text: string): string {
  const cleaned = text.trim().replaceAll(',', '').replaceAll(' ', '')
  // "$625.00", "$(475.00)", "-$12.50", "$-12.50", "625"
  const match = /^(-)?\$?(-)?(\()?(\d+(?:\.\d+)?)(\))?$/.exec(cleaned)
  if (!match || Boolean(match[3]) !== Boolean(match[5])) throw new Error(`not a money amount: '${text}'`)
  const negatives = Number(Boolean(match[1])) + Number(Boolean(match[2])) + Number(Boolean(match[3]))
  if (negatives > 1) throw new Error(`not a money amount: '${text}'`)
  const value = match[4] ?? '0'
  return negatives && Number(value) !== 0 ? `-${value}` : value
}

function parseTimestamp(text: string): number {
  const m = TIMESTAMP.exec(text.trim())
  if (!m) throw new Error(`not a timestamp: '${text}' (expected MM/DD/YYYY HH:MM:SS)`)
  const [month, day, year, hour, minute, second] = m.slice(1).map(Number) as [
    number,
    number,
    number,
    number,
    number,
    number,
  ]
  const ms = Date.UTC(year, month - 1, day, hour, minute, second)
  const d = new Date(ms)
  if (d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) {
    throw new Error(`not a timestamp: '${text}' (expected MM/DD/YYYY HH:MM:SS)`)
  }
  return ms
}

function parseDecimal(text: string): string {
  const t = text.trim()
  if (!DECIMAL.test(t)) throw new Error(`not a number: '${text}'`)
  return t
}

function parseWhole(text: string, what: string): number {
  const t = text.trim()
  if (!/^\d+$/.test(t) || Number(t) <= 0) throw new Error(`not a ${what}: '${text}'`)
  return Number(t)
}

function parseId(text: string): string {
  const t = text.trim()
  if (!/^\d+$/.test(t)) throw new Error(`not a fill id: '${text}'`)
  return t
}

/** One field, named in the message the way the backend names it: "qty: not a positive whole number: 'x'". */
function field<T>(column: string, text: string, convert: (value: string) => T): T {
  try {
    return convert(text)
  } catch (error) {
    throw new Error(`${column}: ${error instanceof Error ? error.message : 'unreadable'}`, { cause: error })
  }
}

export function parsePerformanceCsv(raw: string): ParseResult {
  const text = raw.replace(/^\uFEFF/, '').replaceAll('\x1a', '')
  const rows = splitCsv(text)
  const fatal = headerProblem(rows)
  if (fatal) return { pairs: [], errors: [], duplicates_in_file: 0, fatal }

  const headerIndex = rows.findIndex((r) => r.some((cell) => cell.trim() !== ''))
  const header = (rows[headerIndex] ?? []).map((c) => c.trim())
  const at = (record: string[], column: string) => (record[header.indexOf(column)] ?? '').trim()

  const pairs: ParsedPair[] = []
  const errors: ImportIssue[] = []
  const seen = new Set<string>()
  let duplicates = 0
  for (let i = headerIndex + 1; i < rows.length; i++) {
    const record = rows[i] ?? []
    const line = i + 1
    if (REQUIRED_COLUMNS.every((c) => at(record, c) === '')) continue // a blank line
    if (at(record, 'symbol').toLowerCase() === 'symbol') continue // a header repeated by joined exports
    const key = `${at(record, 'buyFillId')}-${at(record, 'sellFillId')}`
    if (seen.has(key)) {
      duplicates++
      continue
    }
    seen.add(key)
    try {
      const symbol = at(record, 'symbol').toUpperCase()
      if (!symbol) throw new Error('symbol is empty')
      const tick = field('_tickSize', at(record, '_tickSize'), parseDecimal)
      if (Number(tick) <= 0) throw new Error(`_tickSize must be positive, got '${at(record, '_tickSize')}'`)
      const buyFillId = field('buyFillId', at(record, 'buyFillId'), parseId)
      const sellFillId = field('sellFillId', at(record, 'sellFillId'), parseId)
      const qty = field('qty', at(record, 'qty'), (v) => parseWhole(v, 'positive whole number'))
      const buyPrice = field('buyPrice', at(record, 'buyPrice'), parseDecimal)
      const sellPrice = field('sellPrice', at(record, 'sellPrice'), parseDecimal)
      const pnl = field('pnl', at(record, 'pnl'), parseMoney)
      const boughtMs = field('boughtTimestamp', at(record, 'boughtTimestamp'), parseTimestamp)
      const soldMs = field('soldTimestamp', at(record, 'soldTimestamp'), parseTimestamp)
      pairs.push({
        line,
        symbol,
        qty,
        buyFillId,
        sellFillId,
        buyPrice,
        sellPrice,
        pnl,
        boughtAt: at(record, 'boughtTimestamp'),
        soldAt: at(record, 'soldTimestamp'),
        boughtMs,
        soldMs,
      })
    } catch (error) {
      errors.push({ line, message: error instanceof Error ? error.message : 'unreadable row' })
    }
  }
  return { pairs, errors, duplicates_in_file: duplicates, fatal: null }
}

/**
 * How many trades a set of pairs makes: pairs of one contract whose time ranges overlap or touch are one trade (a
 * scale-in or scale-out). The backend's `grouping.py` is the authority; this is the same idea, for the preview number.
 */
export function countTrades(pairs: readonly ParsedPair[]): number {
  const bySymbol = new Map<string, [number, number][]>()
  for (const p of pairs) {
    const spans = bySymbol.get(p.symbol) ?? []
    spans.push([Math.min(p.boughtMs, p.soldMs), Math.max(p.boughtMs, p.soldMs)])
    bySymbol.set(p.symbol, spans)
  }
  let trades = 0
  for (const spans of bySymbol.values()) {
    spans.sort((a, b) => a[0] - b[0])
    let end = -Infinity
    for (const [start, stop] of spans) {
      if (start > end) trades++
      end = Math.max(end, stop)
    }
  }
  return trades
}
