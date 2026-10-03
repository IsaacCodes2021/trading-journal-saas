import { describe, expect, it } from 'vitest'
import {
  daysLeft,
  formatAmount,
  formatAxisMoney,
  formatDay,
  formatDuration,
  formatPercent,
  formatPnl,
  formatRatio,
  formatTradeTime,
  pluralize,
  pnlTone,
} from './format'

const MINUS = '−'

describe('money display', () => {
  it('signs a gain, a loss and zero, with the typographic minus for a loss', () => {
    expect(formatPnl('775.6')).toBe('+$775.60')
    expect(formatPnl('-304.40')).toBe(`${MINUS}$304.40`)
    expect(formatPnl('0')).toBe('$0.00')
    expect(formatPnl('-0.00')).toBe('$0.00')
  })

  it('stays exact beyond what a float can hold', () => {
    expect(formatPnl('9007199254740993.10')).toBe('+$9,007,199,254,740,993.10')
  })

  it('groups thousands and drops the sign for an amount', () => {
    expect(formatAmount('-1234.5')).toBe('$1,234.50')
  })

  it('tones by sign', () => {
    expect(pnlTone('1.00')).toBe('pos')
    expect(pnlTone('-1.00')).toBe('neg')
    expect(pnlTone('0.00')).toBe('flat')
  })

  it('shortens chart axis labels', () => {
    expect(formatAxisMoney(0)).toBe('$0')
    expect(formatAxisMoney(850)).toBe('$850')
    expect(formatAxisMoney(2300)).toBe('$2.3k')
    expect(formatAxisMoney(10_000)).toBe('$10k')
    expect(formatAxisMoney(-2500)).toBe(`${MINUS}$2.5k`)
  })
})

describe('ratios and durations', () => {
  it('formats a percentage and a ratio, with a dash when a ratio is undefined', () => {
    expect(formatPercent(0.583)).toBe('58.3%')
    expect(formatPercent(1, 0)).toBe('100%')
    expect(formatRatio(1.8431)).toBe('1.84')
    expect(formatRatio(null)).toBe('—')
  })

  it('reads a hold time at the right size', () => {
    expect(formatDuration(45)).toBe('45s')
    expect(formatDuration(492)).toBe('8m 12s')
    expect(formatDuration(4000)).toBe('1h 6m')
    expect(formatDuration(-5)).toBe('0s')
  })
})

describe('dates', () => {
  it("shows a trade time in the viewer's time zone", () => {
    expect(formatTradeTime('2026-09-30T13:41:00Z', 'America/New_York')).toBe('Sep 30 · 09:41')
    expect(formatTradeTime('2026-09-30T13:41:00Z', 'America/Los_Angeles')).toBe('Sep 30 · 06:41')
  })

  it('shows a trading day without shifting it by a time zone', () => {
    expect(formatDay('2026-09-30')).toBe('Sep 30')
    expect(formatDay('2026-01-01')).toBe('Jan 1')
  })

  it('counts whole days left, rounding up, and says nothing once past or unset', () => {
    const now = new Date('2026-10-03T12:00:00Z')
    expect(daysLeft('2026-10-12T12:00:00Z', now)).toBe(9)
    expect(daysLeft('2026-10-03T12:00:01Z', now)).toBe(1)
    expect(daysLeft('2026-10-03T11:00:00Z', now)).toBeNull()
    expect(daysLeft(null, now)).toBeNull()
  })

  it('pluralizes with a grouped count', () => {
    expect(pluralize(1, 'trade')).toBe('1 trade')
    expect(pluralize(132, 'trade')).toBe('132 trades')
    expect(pluralize(1200, 'trade')).toBe('1,200 trades')
    expect(pluralize(2, 'entry', 'entries')).toBe('2 entries')
  })
})
