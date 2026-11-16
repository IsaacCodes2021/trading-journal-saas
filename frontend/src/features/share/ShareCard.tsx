import { memo, useEffect, useId, useLayoutEffect, useRef, useState, type Ref } from 'react'
import { cx } from '../../components/ui/cx'
import type { CardModel, CardStyle } from './cardModel'
import { place, type Focus } from './crop'
import { GUILLOCHE_H, GUILLOCHE_W, STAR, guillochePaths } from './ornaments'

/*
 * The share card: a 520 x 650 certificate of one trade, in one of three styles. It lays out what `cardModel` decided.
 * The pop-up shows it twice: a live preview (scaled to its column, with the foil's motion and a tilt that follows the
 * pointer) and a still copy out of sight, at full size and without motion, which the saved image is made from.
 */

export const CARD_W = 520
export const CARD_H = 650

const STYLE_NAME: Record<CardStyle, string> = { gold: 'gold foil', holo: 'holographic', paper: 'engraved paper' }
const TILT_DEGREES: Record<CardStyle, number> = { gold: 3.5, holo: 7, paper: 3.5 }

/** The screenshot on the card, and the crop that places it. */
export interface CardPicture {
  src: string
  focus: Focus
}

export const prefersReducedMotion = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** The Roundturn mark, still (not the animated `BiteMark`), in the card's fixed colours. */
function Mark({ paper }: { paper: boolean }) {
  const ring = paper ? '#1f5fd1' : '#6ea8fe'
  const dot = paper ? '#0b7d55' : '#34d399'
  return (
    <svg width="20" height="20" viewBox="0 0 64 64" aria-hidden="true">
      <circle
        cx="32"
        cy="32"
        r="20"
        fill="none"
        stroke={ring}
        strokeWidth="11"
        strokeDasharray="111.7 13.96"
        transform="rotate(-60 32 32)"
      />
      <polygon points="39.36,10.39 30.26,22.15 26.79,2.46" fill={ring} />
      <circle cx="32" cy="32" r="6" fill={dot} />
    </svg>
  )
}

/** The engraved rosette and wave band. Memoised: its paths are long and change only with the style. */
const Guilloche = memo(function Guilloche({ style }: { style: CardStyle }) {
  return (
    <svg className="sc-guilloche" viewBox={`0 0 ${GUILLOCHE_W} ${GUILLOCHE_H}`} preserveAspectRatio="none">
      <g fill="none" stroke="currentColor" strokeWidth=".7">
        {guillochePaths(style).map((d, i) => (
          <path key={i} d={d} />
        ))}
      </g>
    </svg>
  )
})

/** The foil seal: a starburst of foil with the mark and a ring of text carrying the trade's number. */
const Seal = memo(function Seal({ text }: { text: string }) {
  const ring = `sc-ring-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}` // unique, so two cards on a page never share it
  return (
    <div className="sc-seal" aria-hidden="true">
      <div className="sc-seal-metal sc-metal" style={{ clipPath: STAR }} />
      <svg viewBox="0 0 100 100">
        <defs>
          <path id={ring} d="M50,50 m-33,0 a33,33 0 1,1 66,0 a33,33 0 1,1 -66,0" />
        </defs>
        <circle cx="50" cy="50" r="43" fill="none" stroke="currentColor" strokeOpacity=".5" strokeWidth=".9" />
        <circle
          cx="50"
          cy="50"
          r="40.6"
          fill="none"
          stroke="currentColor"
          strokeOpacity=".35"
          strokeWidth=".6"
          strokeDasharray="1 1.6"
        />
        <circle cx="50" cy="50" r="27" fill="none" stroke="currentColor" strokeOpacity=".5" strokeWidth=".9" />
        <text fontFamily="IBM Plex Mono, monospace" fontSize="6.4" fontWeight="600" fill="currentColor">
          <textPath href={`#${ring}`} textLength="205" lengthAdjust="spacing">
            {text}
          </textPath>
        </text>
        <g transform="translate(50 50) scale(.56) translate(-32 -32)">
          <circle
            cx="32"
            cy="32"
            r="20"
            fill="none"
            stroke="currentColor"
            strokeWidth="9"
            strokeDasharray="111.7 13.96"
            transform="rotate(-60 32 32)"
          />
          <polygon points="39.36,10.39 30.26,22.15 26.79,2.46" fill="currentColor" />
          <circle cx="32" cy="32" r="6" fill="currentColor" />
        </g>
      </svg>
    </div>
  )
})

/** The screenshot's window on the card: it measures itself (its shape depends on the style and options) and places
 * the picture with the crop's focus point and zoom. */
function ShotWindow({
  picture,
  onSize,
}: {
  picture: CardPicture | null
  onSize?: (width: number, height: number) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [frame, setFrame] = useState<[number, number]>([0, 0])
  const [natural, setNatural] = useState<{ src: string; width: number; height: number } | null>(null)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const measure = () =>
      setFrame((old) =>
        old[0] === el.clientWidth && old[1] === el.clientHeight ? old : [el.clientWidth, el.clientHeight],
      )
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (frame[0] > 0 && frame[1] > 0) onSize?.(frame[0], frame[1])
  }, [frame, onSize])

  const size = picture && natural?.src === picture.src ? natural : null
  const placed = size && picture ? place(frame[0], frame[1], size.width, size.height, picture.focus) : null
  return (
    <div ref={box} className="sc-shot-win">
      {picture ? (
        <img
          src={picture.src}
          alt="Chart screenshot of the trade"
          draggable={false}
          onLoad={(e) =>
            setNatural({
              src: picture.src,
              width: e.currentTarget.naturalWidth,
              height: e.currentTarget.naturalHeight,
            })
          }
          style={
            placed
              ? { width: placed.width, height: placed.height, left: placed.left, top: placed.top }
              : { visibility: 'hidden' }
          }
        />
      ) : null}
    </div>
  )
}

export function ShareCard({
  model,
  picture,
  still = false,
  cardRef,
  onShotSize,
}: {
  model: CardModel
  picture: CardPicture | null
  /** The copy the image is made from: no motion, and hidden from assistive technology (the preview is the one read). */
  still?: boolean
  cardRef?: Ref<HTMLDivElement>
  onShotSize?: (width: number, height: number) => void
}) {
  const { style } = model
  return (
    <div
      ref={cardRef}
      className={cx(
        'sc-card',
        `sc-card--${style}`,
        style === 'gold' && 'sc-metal',
        model.tone === 'neg' && 'sc-is-loss',
        model.tone === 'flat' && 'sc-is-flat',
        !model.shot && 'sc-no-shot',
        still && 'sc-still',
      )}
      role={still ? undefined : 'group'}
      aria-label={still ? undefined : `Share card, ${STYLE_NAME[style]} style`}
      aria-hidden={still ? true : undefined}
    >
      <div className="sc-inner">
        <div className="sc-deco" aria-hidden="true">
          <Guilloche style={style} />
          <span className="sc-frame-line" />
          <span className="sc-frame-line sc-frame-line--2" />
          <span className="sc-corner sc-tl sc-metal" />
          <span className="sc-corner sc-tr sc-metal" />
          <span className="sc-corner sc-bl sc-metal" />
          <span className="sc-corner sc-br sc-metal" />
        </div>
        <div className="sc-content">
          <header className="sc-head">
            <span className="sc-wm">
              <Mark paper={style === 'paper'} />
              <span>Roundturn</span>
            </span>
            <span className="sc-no">No. {model.number}</span>
          </header>
          <div className="sc-titleblock">
            <div className="sc-kicker">{model.title}</div>
            <div className="sc-orn sc-metal" aria-hidden="true" />
            <div className="sc-instr">
              <span className="sc-sym">{model.symbol}</span>
              <span className={`sc-dir sc-dir--${model.direction}`}>
                {model.direction === 'long' ? 'Long' : 'Short'}
              </span>
              <span className="sc-meta">{model.meta}</span>
            </div>
          </div>
          <div className="sc-hero">
            <div>
              <div className="sc-label">{model.resultLabel}</div>
              <div className={cx('sc-pnl', model.resultLong && 'sc-is-long')}>{model.result}</div>
              <div className="sc-sub">{model.sub}</div>
            </div>
            {style !== 'paper' ? <Seal text={model.sealText} /> : null}
          </div>
          <div className={cx('sc-legs', model.pointsOnly && 'sc-legs-3')}>
            {model.legs.map((leg) => (
              <div className="sc-leg" key={leg.label}>
                <span className="sc-label">{leg.label}</span>
                <span className="sc-v">{leg.value}</span>
                <span className="sc-s">{leg.sub || ' '}</span>
              </div>
            ))}
          </div>
          {model.shot ? (
            <div className="sc-shot">
              <ShotWindow picture={picture} onSize={onShotSize} />
            </div>
          ) : null}
          {model.notes ? <blockquote className="sc-notes">{model.notes}</blockquote> : null}
          {model.tags ? (
            <div className="sc-tags">
              {model.tags.map((tag) => (
                <span className="sc-tag" key={tag.id}>
                  <i style={{ background: tag.color }} />
                  {tag.name}
                </span>
              ))}
            </div>
          ) : null}
          <footer className="sc-foot">
            {model.signature ? (
              <span className="sc-sig">
                <span className="sc-name">{model.signature}</span>
                <span className="sc-role">Trader</span>
              </span>
            ) : (
              <span />
            )}
            <span className="sc-site">roundturn.app</span>
          </footer>
          {style === 'paper' ? <Seal text={model.sealText} /> : null}
        </div>
        <div className="sc-sheen" aria-hidden="true">
          <span className="sc-holo-film" />
        </div>
      </div>
      <div className="sc-glare" aria-hidden="true" />
    </div>
  )
}

/** The live preview: the card scaled to fit its column, tilting with the pointer (unless motion is reduced). */
export function CardPreview({ model, picture }: { model: CardModel; picture: CardPicture | null }) {
  const fit = useRef<HTMLDivElement>(null)
  const scaler = useRef<HTMLDivElement>(null)
  const card = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const outer = fit.current
    const inner = scaler.current
    if (!outer || !inner) return
    const apply = () => inner.style.setProperty('--s', String(Math.min(1, outer.clientWidth / CARD_W)))
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(apply)
    observer.observe(outer)
    return () => observer.disconnect()
  }, [])

  const max = TILT_DEGREES[model.style]
  useEffect(() => {
    const el = card.current
    if (!el || prefersReducedMotion()) return
    const set = (name: string, value: string) => el.style.setProperty(name, value)
    const move = (event: PointerEvent) => {
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) return
      const px = (event.clientX - r.left) / r.width
      const py = (event.clientY - r.top) / r.height
      set('--ry', `${((px - 0.5) * 2 * max).toFixed(2)}deg`)
      set('--rx', `${((0.5 - py) * 2 * max).toFixed(2)}deg`)
      set('--gx', `${(px * 100).toFixed(1)}%`)
      set('--gy', `${(py * 100).toFixed(1)}%`)
      set('--go', '1')
    }
    const leave = () => {
      set('--rx', '0deg')
      set('--ry', '0deg')
      set('--go', '0')
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerleave', leave)
    return () => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerleave', leave)
    }
  }, [max])

  return (
    <div ref={fit} className="sc-fit">
      <div ref={scaler} className="sc-scaler">
        <ShareCard model={model} picture={picture} cardRef={card} />
      </div>
    </div>
  )
}
