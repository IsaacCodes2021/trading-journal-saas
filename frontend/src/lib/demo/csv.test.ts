import { describe, expect, it } from 'vitest'
import { csv, type Row } from '../../test-csv'
import { REQUIRED_COLUMNS, countTrades, parsePerformanceCsv, splitCsv } from './csv'

describe('splitCsv', () => {
  it('splits fields and lines, with or without a final line break', () => {
    expect(splitCsv('a,b\nc,d')).toEqual([
      ['a', 'b'],
      ['c', 'd'],
    ])
    expect(splitCsv('a,b\r\nc,d\r\n')).toEqual([
      ['a', 'b'],
      ['c', 'd'],
    ])
    expect(splitCsv('')).toEqual([])
  })

  it('reads quotes: commas, doubled quotes and line breaks inside a field', () => {
    expect(splitCsv('"a,b","say ""hi""","x\ny"')).toEqual([['a,b', 'say "hi"', 'x\ny']])
  })

  it('keeps empty fields', () => {
    expect(splitCsv('a,,c\n,,')).toEqual([
      ['a', '', 'c'],
      ['', '', ''],
    ])
  })
})

describe('parsePerformanceCsv', () => {
  it('reads a good export', () => {
    const result = parsePerformanceCsv(csv([{}, { buyFillId: '1003', sellFillId: '1004', qty: '2', pnl: '$(300.00)' }]))
    expect(result.fatal).toBeNull()
    expect(result.errors).toEqual([])
    expect(result.pairs).toHaveLength(2)
    expect(result.pairs[0]).toMatchObject({
      line: 2,
      symbol: 'NQZ6',
      qty: 1,
      buyPrice: '20412.25',
      sellPrice: '20431.75',
      pnl: '390.00',
      boughtAt: '09/30/2026 09:41:07',
    })
    expect(result.pairs[1]).toMatchObject({ line: 3, qty: 2, pnl: '-300.00' })
  })

  it.each([
    ['$625.00', '625.00'],
    ['$(475.00)', '-475.00'],
    ['$4,400.00', '4400.00'],
    ['-$12.50', '-12.50'],
    ['$-12.50', '-12.50'],
    ['0', '0'],
    ['$(0.00)', '0.00'],
  ])('reads the amount %s as %s', (typed, expected) => {
    expect(parsePerformanceCsv(csv([{ pnl: `"${typed}"` }])).pairs[0]?.pnl).toBe(expected)
  })

  it('upper-cases the symbol, and ignores a byte-order mark and blank lines, keeping line numbers true', () => {
    const text = `\uFEFF${csv([{ symbol: 'mnqz6' }, { buyFillId: '5', sellFillId: '6', qty: 'x' }])}\n\n`
    const result = parsePerformanceCsv(text)
    expect(result.pairs[0]?.symbol).toBe('MNQZ6')
    expect(result.errors).toEqual([{ line: 3, message: "qty: not a positive whole number: 'x'" }])
  })

  it('skips a header repeated by joined exports, and counts a pair repeated in the file once', () => {
    const body = csv([{}, { buyFillId: '1003', sellFillId: '1004' }, {}])
    const withHeader = body.replace('\n', `\n${REQUIRED_COLUMNS.join(',')}\n`)
    const result = parsePerformanceCsv(withHeader)
    expect(result.pairs).toHaveLength(2)
    expect(result.duplicates_in_file).toBe(1)
  })

  it('refuses a file that is not a Performance export, saying which columns are missing', () => {
    const result = parsePerformanceCsv('symbol,qty\nNQZ6,1')
    expect(result.fatal).toMatch(/^Not a Tradovate Performance export: missing column\(s\) _priceFormat, /)
    expect(result.pairs).toEqual([])
  })

  it('recognises an order-history export and says what to do instead', () => {
    expect(parsePerformanceCsv('Account,Order ID,Status\nA,1,Filled').fatal).toContain('order-history export')
    expect(parsePerformanceCsv('Working Orders\nx').fatal).toContain('order-history export')
  })

  it('refuses an empty file', () => {
    expect(parsePerformanceCsv('').fatal).toBe('The file is empty.')
    expect(parsePerformanceCsv('\n\n  \n').fatal).toBe('The file is empty.')
  })

  it.each([
    [{ symbol: '' }, 'symbol is empty'],
    [{ _tickSize: '0' }, "_tickSize must be positive, got '0'"],
    [{ buyFillId: 'abc' }, "buyFillId: not a fill id: 'abc'"],
    [{ qty: '0' }, "qty: not a positive whole number: '0'"],
    [{ qty: '1.5' }, "qty: not a positive whole number: '1.5'"],
    [{ buyPrice: 'x' }, "buyPrice: not a number: 'x'"],
    [{ pnl: 'lots' }, "pnl: not a money amount: 'lots'"],
    [
      { boughtTimestamp: '2026-09-30 09:41:07' },
      "boughtTimestamp: not a timestamp: '2026-09-30 09:41:07' (expected MM/DD/YYYY HH:MM:SS)",
    ],
    [
      { soldTimestamp: '02/30/2026 09:41:07' },
      "soldTimestamp: not a timestamp: '02/30/2026 09:41:07' (expected MM/DD/YYYY HH:MM:SS)",
    ],
  ] as [Row, string][])('reports a bad row in words, with its line: %j', (row, message) => {
    const result = parsePerformanceCsv(csv([row]))
    expect(result.pairs).toEqual([])
    expect(result.errors).toEqual([{ line: 2, message }])
  })

  it('keeps the good rows when one is bad', () => {
    const result = parsePerformanceCsv(
      csv([{}, { buyFillId: '9', sellFillId: '10', qty: '-1' }, { buyFillId: '11', sellFillId: '12' }]),
    )
    expect(result.pairs.map((p) => p.line)).toEqual([2, 4])
    expect(result.errors.map((e) => e.line)).toEqual([3])
  })

  it('reads columns by name, whatever their order', () => {
    const reversed = [...REQUIRED_COLUMNS].reverse()
    expect(parsePerformanceCsv(csv([{ qty: '3' }], reversed)).pairs[0]).toMatchObject({ qty: 3, symbol: 'NQZ6' })
  })
})

describe('countTrades', () => {
  const pair = (symbol: string, from: number, to: number) =>
    ({ symbol, boughtMs: from, soldMs: to }) as Parameters<typeof countTrades>[0][number]

  it('counts pairs that overlap or touch as one trade', () => {
    expect(countTrades([pair('NQ', 0, 10), pair('NQ', 5, 20), pair('NQ', 20, 30)])).toBe(1)
  })

  it('counts separated pairs, and different contracts, separately', () => {
    expect(countTrades([pair('NQ', 0, 10), pair('NQ', 11, 20), pair('ES', 0, 20)])).toBe(3)
  })

  it('reads a short trade (sold before bought) the same way', () => {
    expect(countTrades([pair('NQ', 10, 0), pair('NQ', 5, 15)])).toBe(1)
  })

  it('is zero for nothing', () => {
    expect(countTrades([])).toBe(0)
  })
})
