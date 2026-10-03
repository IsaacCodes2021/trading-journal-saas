import { REQUIRED_COLUMNS } from './lib/demo/csv'

/* Test helper only: builds Tradovate Performance exports. */

export type Row = Partial<Record<(typeof REQUIRED_COLUMNS)[number], string>>

const GOOD: Required<Row> = {
  symbol: 'NQZ6',
  _priceFormat: '-2',
  _priceFormatType: '0',
  _tickSize: '0.25',
  buyFillId: '1001',
  sellFillId: '1002',
  qty: '1',
  buyPrice: '20412.25',
  sellPrice: '20431.75',
  pnl: '$390.00',
  boughtTimestamp: '09/30/2026 09:41:07',
  soldTimestamp: '09/30/2026 09:49:19',
  duration: '8min 12sec',
}

/** A Performance export: the real header, then one line per row (missing fields take the good default). */
export function csv(rows: Row[], header: readonly string[] = REQUIRED_COLUMNS): string {
  const line = (r: Row) =>
    header.map((c) => (c in r ? (r as Record<string, string>)[c] : (GOOD as Record<string, string>)[c])).join(',')
  return [header.join(','), ...rows.map(line)].join('\n')
}

const two = (n: number) => String(n).padStart(2, '0')

/** `count` different pairs (own fill ids, ten minutes apart, so each is its own trade), starting at `firstId`. */
export function manyRows(count: number, firstId = 1000, over: Row = {}): Row[] {
  return Array.from({ length: count }, (_, i) => {
    const date = `09/${two(1 + Math.floor(i / 84))}/2026` // 84 pairs a day: 9:00 to 22:50
    const hour = two(9 + Math.floor((i % 84) / 6))
    const minute = (i % 6) * 10
    return {
      buyFillId: String(firstId + i * 2),
      sellFillId: String(firstId + i * 2 + 1),
      boughtTimestamp: `${date} ${hour}:${two(minute)}:00`,
      soldTimestamp: `${date} ${hour}:${two(minute + 3)}:00`,
      ...over,
    }
  })
}

/** A File holding text, the way a browser hands one over. */
export function csvFile(text: string, name = 'Performance_2026-09-30.csv'): File {
  return new File([text], name, { type: 'text/csv' })
}
