import { describe, expect, it } from 'vitest'
import {
  buildWeeks,
  dayLabel,
  extremeDay,
  isDay,
  largestDay,
  monthLabel,
  monthOf,
  monthRange,
  parseMonth,
  shade,
  shiftDay,
  shiftMonth,
} from './calendar'

const day = (date: string, net: string, count = 1) => ({ day: date, net_pnl: net, trade_count: count })

describe('months', () => {
  it('keeps a month between 2000 and 2100 and drops anything else', () => {
    for (const ok of ['2000-01', '2024-02', '2026-09', '2100-12']) expect(parseMonth(ok)).toBe(ok)
    for (const bad of [
      '1999-12',
      '2101-01',
      '2024-13',
      '2024-00',
      '2024-1',
      '2024-01-05',
      '24-01',
      '',
      'abc',
      5,
      null,
      undefined,
    ]) {
      expect(parseMonth(bad)).toBeUndefined()
    }
  })

  it('moves by months across a year, both ways', () => {
    expect(shiftMonth('2026-09', 1)).toBe('2026-10')
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
    expect(shiftMonth('2026-03', 12)).toBe('2027-03')
    expect(shiftMonth('2026-03', -12)).toBe('2025-03')
    expect(shiftMonth('2026-03', 0)).toBe('2026-03')
  })

  it('gives the first and last day of a month, leap years and short months included', () => {
    expect(monthRange('2024-02')).toEqual({ start: '2024-02-01', end: '2024-02-29' })
    expect(monthRange('2023-02')).toEqual({ start: '2023-02-01', end: '2023-02-28' })
    expect(monthRange('2026-04')).toEqual({ start: '2026-04-01', end: '2026-04-30' })
    expect(monthRange('2026-12')).toEqual({ start: '2026-12-01', end: '2026-12-31' })
    expect(monthRange('2100-12')).toEqual({ start: '2100-12-01', end: '2100-12-31' })
  })

  it('names a month and takes the month out of a day', () => {
    expect(monthLabel('2026-09')).toBe('September 2026')
    expect(monthLabel('2024-02')).toBe('February 2024')
    expect(monthOf('2026-09-08')).toBe('2026-09')
  })
})

describe('days', () => {
  it('knows a real day from one that rolls over, and the edges of the range', () => {
    for (const ok of ['2024-01-05', '2024-02-29', '2000-01-01', '2100-12-31', '2026-12-31'])
      expect(isDay(ok)).toBe(true)
    for (const bad of [
      '2023-02-29',
      '2024-02-30',
      '2024-04-31',
      '2024-13-01',
      '2024-00-10',
      '2024-01-00',
      '1999-12-31',
      '2101-01-01',
      '2024-1-5',
      '2024-01-05T00:00',
      '',
      5,
      null,
    ]) {
      expect(isDay(bad)).toBe(false)
    }
  })

  it('names a day in full', () => {
    expect(dayLabel('2024-01-05')).toBe('Friday, January 5, 2024')
    expect(dayLabel('2026-09-08')).toBe('Tuesday, September 8, 2026')
  })

  it('moves by days across a month, a year and a leap day', () => {
    expect(shiftDay('2026-09-30', 1)).toBe('2026-10-01')
    expect(shiftDay('2026-01-01', -1)).toBe('2025-12-31')
    expect(shiftDay('2024-02-28', 1)).toBe('2024-02-29')
    expect(shiftDay('2023-02-28', 1)).toBe('2023-03-01')
    expect(shiftDay('2026-09-08', 0)).toBe('2026-09-08')
    expect(shiftDay('2026-09-08', 7)).toBe('2026-09-15')
  })
})

describe('the weeks of a month', () => {
  it('starts on Monday: a month that begins on one has no blank in its first week', () => {
    const weeks = buildWeeks('2024-01', [], []) // 1 January 2024 was a Monday
    expect(weeks).toHaveLength(5)
    expect(weeks[0]?.cells.map((c) => c?.number)).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(weeks[4]?.cells.map((c) => c?.number)).toEqual([29, 30, 31, undefined, undefined, undefined, undefined])
  })

  it('leaves blanks before the first day: September 2026 begins on a Tuesday', () => {
    const [first] = buildWeeks('2026-09', [], [])
    expect(first?.cells[0]).toBeNull()
    expect(first?.cells[1]?.day).toBe('2026-09-01')
    expect(first?.cells.map((c) => c?.number)).toEqual([undefined, 1, 2, 3, 4, 5, 6])
  })

  it('puts a Sunday start in the last column, and can need six weeks', () => {
    const weeks = buildWeeks('2023-01', [], []) // 1 January 2023 was a Sunday
    expect(weeks).toHaveLength(6)
    expect(weeks[0]?.cells.map((c) => c?.number)).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      1,
    ])
    expect(weeks[5]?.cells.map((c) => c?.number)).toEqual([
      30,
      31,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
    ])
  })

  it('has every day of the month once, in order, whatever the month', () => {
    for (const month of ['2024-02', '2023-02', '2026-09', '2026-10', '2026-12', '2023-01', '2100-12']) {
      const days = buildWeeks(month, [], []).flatMap((w) => w.cells.flatMap((c) => (c ? [c.number] : [])))
      const last = Number(monthRange(month).end.slice(8))
      expect(days).toEqual(Array.from({ length: last }, (_, i) => i + 1))
    }
  })

  it('attaches what traded and which days have a note, and ignores days outside the month', () => {
    const weeks = buildWeeks(
      '2026-09',
      [day('2026-09-08', '100.00', 2), day('2026-08-31', '999.00'), day('2026-10-01', '999.00')],
      ['2026-09-08', '2026-09-09', '2026-10-02'],
    )
    const cells = weeks.flatMap((w) => w.cells).filter((c) => c !== null)
    const found = (d: string) => cells.find((c) => c.day === d)
    expect(found('2026-09-08')).toMatchObject({ number: 8, noted: true, result: { net_pnl: '100.00', trade_count: 2 } })
    expect(found('2026-09-09')).toMatchObject({ noted: true, result: undefined })
    expect(found('2026-09-10')).toMatchObject({ noted: false, result: undefined })
    expect(cells.some((c) => c.day === '2026-08-31' || c.day === '2026-10-01')).toBe(false)
  })

  it('totals each week exactly, counts its trades, and says nothing for a week with none', () => {
    const weeks = buildWeeks(
      '2026-09',
      [day('2026-09-01', '0.10'), day('2026-09-02', '0.20', 3), day('2026-09-15', '-5.50', 2)],
      [],
    )
    expect(weeks[0]).toMatchObject({ net: '0.30', trades: 4 }) // 0.1 + 0.2, exactly
    expect(weeks[1]).toMatchObject({ net: null, trades: 0 })
    expect(weeks[2]).toMatchObject({ net: '-5.50', trades: 2 })
  })

  it('counts a week whose days add up to nothing as a week that traded', () => {
    const [week] = buildWeeks('2026-09', [day('2026-09-01', '50.00'), day('2026-09-02', '-50.00')], [])
    expect(week).toMatchObject({ net: '0.00', trades: 2 })
  })
})

describe('shading', () => {
  it('goes by size against the biggest day, win or loss, in three steps', () => {
    expect(shade('100.00', '100.00')).toBe(3)
    expect(shade('-100.00', '100.00')).toBe(3)
    expect(shade('66.00', '100.00')).toBe(3) // at the line
    expect(shade('65.99', '100.00')).toBe(2)
    expect(shade('33.00', '100.00')).toBe(2) // at the line
    expect(shade('32.99', '100.00')).toBe(1)
    expect(shade('0.01', '100.00')).toBe(1)
    expect(shade('-50.00', '100.00')).toBe(2)
  })

  it('never shades a flat day or a month with no size', () => {
    expect(shade('0.00', '100.00')).toBe(0)
    expect(shade('0.00', '0.00')).toBe(0)
    expect(shade('10.00', '0.00')).toBe(0)
  })

  it('finds the biggest day by size', () => {
    expect(largestDay([day('a', '10.00'), day('b', '-250.50'), day('c', '99.00')])).toBe('250.50')
    expect(largestDay([])).toBe('0.00')
  })
})

describe('the best and worst day', () => {
  const days = [
    day('2026-09-01', '10.00'),
    day('2026-09-02', '-30.00'),
    day('2026-09-03', '75.00'),
    day('2026-09-04', '75.00'),
    day('2026-09-05', '-30.00'),
  ]

  it('picks by net result and keeps the earlier day when two tie', () => {
    expect(extremeDay(days, 'best')?.day).toBe('2026-09-03')
    expect(extremeDay(days, 'worst')?.day).toBe('2026-09-02')
  })

  it('is nothing when nothing traded, and the only day when one did', () => {
    expect(extremeDay([], 'best')).toBeUndefined()
    expect(extremeDay([], 'worst')).toBeUndefined()
    expect(extremeDay([day('2026-09-01', '-5.00')], 'best')?.day).toBe('2026-09-01')
    expect(extremeDay([day('2026-09-01', '-5.00')], 'worst')?.day).toBe('2026-09-01')
  })

  it('compares as decimals, not as text', () => {
    expect(extremeDay([day('a', '9.00'), day('b', '10.00')], 'best')?.day).toBe('b')
    expect(extremeDay([day('a', '-9.00'), day('b', '-10.00')], 'worst')?.day).toBe('b')
  })
})
