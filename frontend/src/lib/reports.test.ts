import { describe, expect, it } from 'vitest'
import { REPORT_BYS, type ReportGroup, type SummaryStats } from './data/model'
import { REPORT_KINDS, barShare, largestResult, parseBy } from './reports'

const group = (net: string): ReportGroup => ({
  key: net,
  label: net,
  detail: null,
  summary: { net_pnl: net } as SummaryStats,
})

describe('which report an address names', () => {
  it('accepts each of the six', () => {
    for (const by of REPORT_BYS) expect(parseBy(by)).toBe(by)
    expect(REPORT_BYS).toHaveLength(6)
  })

  it('drops anything else, including a different capital, a list and a missing value', () => {
    for (const bad of ['Symbol', 'colour', '', ' hour', ['hour'], 1, null, undefined, {}])
      expect(parseBy(bad), String(bad)).toBeUndefined()
  })

  it('has a label, a heading and a sentence for every kind, and the labels differ', () => {
    for (const by of REPORT_BYS) {
      const kind = REPORT_KINDS[by]
      expect(kind.label && kind.heading && kind.how).toBeTruthy()
    }
    expect(new Set(REPORT_BYS.map((by) => REPORT_KINDS[by].label)).size).toBe(6)
  })
})

describe('the length of the bars', () => {
  it('measures every bar against the biggest result, wins and losses alike', () => {
    expect(largestResult([group('120.50'), group('-300.25'), group('40.00')])).toBe('300.25')
    expect(largestResult([group('10.00'), group('-9.99')])).toBe('10.00')
  })

  it('is zero for no groups or only breakeven ones', () => {
    expect(largestResult([])).toBe('0.00')
    expect(largestResult([group('0.00'), group('0.00')])).toBe('0.00')
  })

  it('gives the biggest result the whole bar and the others their share, whichever way they went', () => {
    expect(barShare('300.25', '300.25')).toBe(1)
    expect(barShare('-300.25', '300.25')).toBe(1)
    expect(barShare('75.00', '300.00')).toBe(0.25)
    expect(barShare('-150.00', '300.00')).toBe(0.5)
  })

  it('gives nothing no bar, and never more than the whole bar', () => {
    expect(barShare('0.00', '300.00')).toBe(0)
    expect(barShare('50.00', '0.00')).toBe(0)
    expect(barShare('900.00', '300.00')).toBe(1)
  })
})
