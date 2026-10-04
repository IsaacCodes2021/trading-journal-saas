import { describe, expect, it } from 'vitest'
import { formatMoney, parseAmount, sumMoney, toChartNumber } from './money'

describe('money helpers', () => {
  it('formats a decimal string without a float round trip', () => {
    // 2^53 is about 9.007e15; this value has 21 significant digits, so a float would visibly corrupt it.
    expect(formatMoney('123456789012345678.99')).toBe('$123,456,789,012,345,678.99')
    expect(formatMoney('-475.00')).toBe('-$475.00')
    expect(formatMoney('0.10')).toBe('$0.10')
  })

  it('adds exactly where floats cannot', () => {
    expect(0.1 + 0.2).not.toBe(0.3) // the problem
    expect(sumMoney(['0.10', '0.20'])).toBe('0.30')
    expect(sumMoney(['125.50', '-80.00', '310.25'])).toBe('355.75')
  })

  it('only converts to a number at the chart boundary', () => {
    expect(toChartNumber('125.50')).toBe(125.5)
  })
})

describe('parseAmount', () => {
  it.each([
    ['2500', '2500.00'],
    ['2,500.5', '2500.50'],
    ['$ 1,234.56', '1234.56'],
    ['  50 ', '50.00'],
    ['.5', '0.50'],
    ['0', '0.00'],
    ['12.', '12.00'],
    ['9007199254740993.10', '9007199254740993.10'],
  ])('reads %j as %s, exactly', (typed, expected) => {
    expect(parseAmount(typed)).toBe(expected)
  })

  it('is null for an empty box, so "not set" is not "zero"', () => {
    expect(parseAmount('')).toBeNull()
    expect(parseAmount('   ')).toBeNull()
    expect(parseAmount('$')).toBeNull()
  })

  it.each(['abc', '-5', '5-', '1.234', '1e3', '12 34', '$$5', '5%', '1,2,3x'])('refuses %j, in words', (typed) => {
    expect(() => parseAmount(typed)).toThrow('Type an amount like 2500 or 2,500.50.')
  })
})
