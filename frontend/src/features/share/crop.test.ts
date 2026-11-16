import { describe, expect, it } from 'vitest'
import { CENTRED, clampZoom, place } from './crop'

// A 16:9 screenshot, and the gold card's picture space with everything on.
const IMG: [number, number] = [1600, 900]
const GOLD: [number, number] = [446, 134]
const HOLO: [number, number] = [456, 154]

/** The point of the picture (0 to 1) that sits at the centre of the frame. */
const centreOf = (frame: [number, number], p: ReturnType<typeof place>) => [
  (frame[0] / 2 - p.left) / p.width,
  (frame[1] / 2 - p.top) / p.height,
]

describe('placing the screenshot in its frame', () => {
  it('at zoom 1 the picture just covers the frame, centred', () => {
    const p = place(...GOLD, ...IMG, CENTRED)
    expect(p.width).toBeCloseTo(446) // the wider side fits exactly
    expect(p.height).toBeCloseTo(250.875) // the other overflows, never short
    expect(p.left).toBeCloseTo(0)
    expect(p.top).toBeCloseTo((134 - 250.875) / 2)
  })

  it('covers a frame taller than the picture too, by its height', () => {
    const p = place(100, 300, ...IMG, CENTRED)
    expect(p.height).toBeCloseTo(300)
    expect(p.width).toBeGreaterThan(100)
  })

  it('zooms from that cover size', () => {
    const p = place(...GOLD, ...IMG, { ...CENTRED, zoom: 2 })
    expect(p.width).toBeCloseTo(892)
    expect(p.height).toBeCloseTo(501.75)
  })

  it('never shows beyond the picture: the focus point is clamped at every edge', () => {
    const topLeft = place(...GOLD, ...IMG, { focusX: 0, focusY: 0, zoom: 2 })
    expect(topLeft.left).toBeCloseTo(0)
    expect(topLeft.top).toBeCloseTo(0)
    const bottomRight = place(...GOLD, ...IMG, { focusX: 1, focusY: 1, zoom: 2 })
    expect(bottomRight.left + bottomRight.width).toBeCloseTo(446)
    expect(bottomRight.top + bottomRight.height).toBeCloseTo(134)
    // At zoom 1 the picture is exactly as wide as the frame, so it cannot move sideways at all.
    expect(place(...GOLD, ...IMG, { focusX: 0.9, focusY: 0.5, zoom: 1 }).left).toBeCloseTo(0)
  })

  it('reports the clamped focus point, which is what the cropper stores', () => {
    const p = place(...GOLD, ...IMG, { focusX: -3, focusY: 7, zoom: 1 })
    expect(p.focusX).toBeCloseTo(0.5)
    expect(p.focusY).toBeCloseTo(1 - 67 / 250.875)
  })

  it('gives the same picture in two frame sizes of one shape: everything scales together', () => {
    const focus = { focusX: 0.7, focusY: 0.4, zoom: 2.5 }
    const small = place(...GOLD, ...IMG, focus)
    const large = place(GOLD[0] * 2, GOLD[1] * 2, ...IMG, focus)
    expect(large.width).toBeCloseTo(small.width * 2)
    expect(large.height).toBeCloseTo(small.height * 2)
    expect(large.left).toBeCloseTo(small.left * 2)
    expect(large.top).toBeCloseTo(small.top * 2)
  })

  it('keeps the same point of the picture at the centre when the frame changes shape (another style)', () => {
    const focus = { focusX: 0.62, focusY: 0.45, zoom: 2 }
    const gold = place(...GOLD, ...IMG, focus)
    const holo = place(...HOLO, ...IMG, focus)
    for (const [frame, p] of [
      [GOLD, gold],
      [HOLO, holo],
    ] as const) {
      const [x, y] = centreOf(frame, p)
      expect(x).toBeCloseTo(0.62)
      expect(y).toBeCloseTo(0.45)
    }
  })

  it('places nothing while either size is unknown', () => {
    expect(place(0, 0, ...IMG, CENTRED)).toMatchObject({ width: 0, height: 0, left: 0, top: 0 })
    expect(place(...GOLD, 0, 0, CENTRED)).toMatchObject({ width: 0, height: 0 })
  })
})

describe('the zoom', () => {
  it('stays between 1 and 4, to two decimals, so repeated steps do not drift', () => {
    expect(clampZoom(0.4)).toBe(1)
    expect(clampZoom(9)).toBe(4)
    expect(clampZoom(1 + 0.1 + 0.1 + 0.1)).toBe(1.3)
  })
})
