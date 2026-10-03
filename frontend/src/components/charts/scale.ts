/** Small, pure helpers for the SVG charts. Money strings become numbers here and only here, for drawing. */

/** Round tick values covering [min, max], about `target` of them: steps of 1, 2 or 5 times a power of ten. */
export function niceTicks(min: number, max: number, target = 4): number[] {
  if (min === max) return [min]
  const span = max - min
  const rough = span / target
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? 10 * magnitude
  const ticks: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(Math.round(v * 1e6) / 1e6)
  return ticks
}

/** A function mapping a value in [d0, d1] onto [r0, r1]. A flat domain maps to the middle of the range. */
export function linear(domain: readonly [number, number], range: readonly [number, number]): (value: number) => number {
  const [d0, d1] = domain
  const [r0, r1] = range
  return (value) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((value - d0) / (d1 - d0)) * (r1 - r0))
}

export function linePath(points: readonly (readonly [number, number])[]): string {
  return points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
}

/** Up to `count` indices spread evenly over `length` items, always including the first and the last. */
export function spreadIndices(length: number, count: number): number[] {
  if (length <= 0) return []
  if (length <= count) return Array.from({ length }, (_, i) => i)
  return [...new Set(Array.from({ length: count }, (_, i) => Math.round((i * (length - 1)) / (count - 1))))]
}

/** Corner points of a radar polygon: `values` are 0..100, the first axis points straight up. */
export function radarPoints(values: readonly number[], radius: number, cx: number, cy: number): [number, number][] {
  return values.map((value, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / values.length
    const r = (Math.min(100, Math.max(0, value)) / 100) * radius
    return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)]
  })
}
