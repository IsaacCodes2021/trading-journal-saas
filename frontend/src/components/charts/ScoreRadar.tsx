import type { ScoreAxis } from '../../lib/data/model'
import { radarPoints } from './scale'

const SIZE = 200
const CENTER = 100
const RADIUS = 80

const toPoints = (points: readonly (readonly [number, number])[]) =>
  points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')

/** One spoke per score axis, however many the backend defines. 0 at the centre, 100 at the rim. */
export function ScoreRadar({ axes }: { axes: readonly ScoreAxis[] }) {
  const n = axes.length
  const rim = radarPoints(Array(n).fill(100) as number[], RADIUS, CENTER, CENTER)
  const half = radarPoints(Array(n).fill(50) as number[], RADIUS, CENTER, CENTER)
  const shape = radarPoints(
    axes.map((a) => a.score),
    RADIUS,
    CENTER,
    CENTER,
  )
  const label = `Radar chart of ${n} score components: ${axes.map((a) => `${a.name} ${Math.round(a.score)}`).join(', ')}`
  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      width="100%"
      role="img"
      aria-label={label}
      className="mx-auto mt-2 block max-w-[240px]"
    >
      <polygon points={toPoints(rim)} fill="none" className="stroke-line-strong" />
      <polygon points={toPoints(half)} fill="none" className="stroke-line-soft" />
      {rim.map(([x, y], i) => (
        <line key={axes[i]?.name ?? i} x1={CENTER} y1={CENTER} x2={x} y2={y} className="stroke-line-soft" />
      ))}
      <polygon points={toPoints(shape)} strokeWidth={2} className="fill-accent/[0.22] stroke-accent" />
    </svg>
  )
}
