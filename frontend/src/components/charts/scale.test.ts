import { describe, expect, it } from 'vitest'
import { linePath, linear, niceTicks, radarPoints, spreadIndices } from './scale'

describe('niceTicks', () => {
  it('uses round steps that cover the range', () => {
    expect(niceTicks(0, 1000, 4)).toEqual([0, 500, 1000]) // the first round step of at least 250 is 500
    expect(niceTicks(0, 1000, 8)).toEqual([0, 200, 400, 600, 800, 1000])
    const ticks = niceTicks(-380, 2290, 3)
    expect(ticks).toContain(0)
    expect(ticks[0]).toBeGreaterThanOrEqual(-380)
    expect(ticks[ticks.length - 1]).toBeLessThanOrEqual(2290)
  })

  it('handles a flat range and keeps float noise out', () => {
    expect(niceTicks(5, 5)).toEqual([5])
    expect(niceTicks(0, 1, 10)).toContain(0.3)
  })
})

describe('linear', () => {
  it('maps the domain onto the range, flipping when the range is reversed', () => {
    const y = linear([0, 100], [200, 0])
    expect(y(0)).toBe(200)
    expect(y(100)).toBe(0)
    expect(y(50)).toBe(100)
  })

  it('maps a flat domain to the middle instead of dividing by zero', () => {
    expect(linear([3, 3], [0, 10])(3)).toBe(5)
  })
})

describe('path and index helpers', () => {
  it('builds an SVG path', () => {
    expect(linePath([])).toBe('')
    expect(
      linePath([
        [0, 1.234],
        [10, 2],
      ]),
    ).toBe('M0.0,1.2 L10.0,2.0')
  })

  it('spreads labels evenly and always keeps the first and last', () => {
    expect(spreadIndices(0, 5)).toEqual([])
    expect(spreadIndices(3, 5)).toEqual([0, 1, 2])
    const spread = spreadIndices(22, 5)
    expect(spread[0]).toBe(0)
    expect(spread[spread.length - 1]).toBe(21)
    expect(spread).toHaveLength(5)
  })
})

describe('radarPoints', () => {
  it('points the first axis straight up and clamps to the rim', () => {
    const [top, right] = radarPoints([100, 50, 100, 100], 80, 100, 100)
    expect(top?.[0]).toBeCloseTo(100)
    expect(top?.[1]).toBeCloseTo(20)
    expect(right?.[0]).toBeCloseTo(140)
    expect(right?.[1]).toBeCloseTo(100)
    const [over] = radarPoints([250, 0, 0], 80, 100, 100)
    expect(over?.[1]).toBeCloseTo(20)
  })

  it('works for any number of axes', () => {
    expect(radarPoints(Array(5).fill(100) as number[], 80, 0, 0)).toHaveLength(5)
    expect(radarPoints(Array(6).fill(100) as number[], 80, 0, 0)).toHaveLength(6)
  })
})
