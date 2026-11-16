import type { CardStyle } from './cardModel'

/*
 * The card's generated shapes, ported from the design mock (docs/design/share-trade-card.html: `guilloche`, `STAR`):
 * the engraved rosette with its wave band, and the starburst edge of the seal. Both are pure and computed once.
 */

/** The rosette's drawing space: the card's inner panel (520 x 650 less the 8 px foil edge each side). */
export const GUILLOCHE_W = 504
export const GUILLOCHE_H = 634

const guillocheCache = new Map<boolean, string[]>()

/** The rosette and the wave band as SVG path data. Paper centres the rosette; the dark styles put it top right. */
export function guillochePaths(style: CardStyle): string[] {
  const centred = style === 'paper'
  const cached = guillocheCache.get(centred)
  if (cached) return cached
  const W = GUILLOCHE_W
  const H = GUILLOCHE_H
  const cx = centred ? W / 2 : W * 0.8
  const cy = centred ? H * 0.33 : H * 0.27
  const paths: string[] = []
  for (let k = 0; k < 16; k++) {
    let p = ''
    for (let i = 0; i <= 200; i++) {
      const th = (i / 200) * Math.PI * 2
      const r = 128 + 18 * Math.sin(12 * th + (k * Math.PI * 2) / 16) + 6 * Math.sin(5 * th - k * 0.35)
      p += (i ? 'L' : 'M') + (cx + r * Math.cos(th)).toFixed(1) + ' ' + (cy + r * Math.sin(th)).toFixed(1)
    }
    paths.push(`${p}Z`)
  }
  for (let k = 0; k < 12; k++) {
    let p = ''
    for (let x = 0; x <= W; x += 6) {
      const y = H - 74 + k * 3.2 + 7 * Math.sin(x / 22 + k * 0.55) + 3 * Math.sin(x / 9 - k * 0.3)
      p += (x ? 'L' : 'M') + x + ' ' + y.toFixed(1)
    }
    paths.push(p)
  }
  guillocheCache.set(centred, paths)
  return paths
}

/** The seal's starburst edge: a clip-path polygon of 36 points. */
export const STAR = (() => {
  const n = 36
  const pts: string[] = []
  for (let i = 0; i < n * 2; i++) {
    const a = (i * Math.PI) / n - Math.PI / 2
    const r = i % 2 ? 46.4 : 50
    pts.push(`${(50 + r * Math.cos(a)).toFixed(2)}% ${(50 + r * Math.sin(a)).toFixed(2)}%`)
  }
  return `polygon(${pts.join(',')})`
})()
