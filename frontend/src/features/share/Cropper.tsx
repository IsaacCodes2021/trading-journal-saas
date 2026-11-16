import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import {
  CENTRED,
  KEY_MOVE_STEP,
  KEY_ZOOM_STEP,
  ZOOM_MAX,
  ZOOM_MIN,
  ZOOM_STEP,
  clampZoom,
  place,
  type Focus,
} from './crop'

const MOVES: Record<string, [number, number]> = {
  ArrowLeft: [-KEY_MOVE_STEP, 0],
  ArrowRight: [KEY_MOVE_STEP, 0],
  ArrowUp: [0, -KEY_MOVE_STEP],
  ArrowDown: [0, KEY_MOVE_STEP],
}

/**
 * The crop frame: exactly the picture the card will show, in the live shape of the card's picture space (`aspect`).
 * Drag the picture (mouse, pen or touch) or use the arrow keys to move it; the slider, or plus and minus, zoom. The
 * focus point that comes out is clamped to this frame, so the frame never shows beyond the picture's edge.
 */
export function Cropper({
  src,
  aspect,
  focus,
  onChange,
}: {
  src: string
  aspect: readonly [number, number]
  focus: Focus
  onChange: (next: Focus) => void
}) {
  const stage = useRef<HTMLDivElement>(null)
  const [frame, setFrame] = useState<[number, number]>([0, 0])
  const [natural, setNatural] = useState<{ src: string; width: number; height: number } | null>(null)
  const drag = useRef<[number, number] | null>(null)
  // Several pointer moves can arrive before the next render; each starts from the latest focus, not the rendered one.
  const latest = useRef(focus)
  useLayoutEffect(() => {
    latest.current = focus
  }, [focus])

  useLayoutEffect(() => {
    const el = stage.current
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

  const size = natural?.src === src ? natural : null
  const placeIn = (next: Focus) => (size ? place(frame[0], frame[1], size.width, size.height, next) : null)
  const placed = placeIn(focus)

  /** Store a change with its focus point clamped to this frame, so dragging past an edge does not build up. */
  const commit = (next: Focus) => {
    const zoom = clampZoom(next.zoom)
    const at = placeIn({ ...next, zoom })
    const stored = at ? { zoom, focusX: at.focusX, focusY: at.focusY } : { ...next, zoom }
    latest.current = stored
    onChange(stored)
  }

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    drag.current = [event.clientX, event.clientY]
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current
    const el = stage.current
    if (!start || !el) return
    const now = placeIn(latest.current)
    if (!now || now.width === 0) return
    const shown = el.getBoundingClientRect().width
    const k = shown > 0 ? el.clientWidth / shown : 1 // pointer pixels to frame pixels, should the frame be scaled
    drag.current = [event.clientX, event.clientY]
    commit({
      zoom: latest.current.zoom,
      focusX: now.focusX - ((event.clientX - start[0]) * k) / now.width,
      focusY: now.focusY - ((event.clientY - start[1]) * k) / now.height,
    })
  }
  const endDrag = () => {
    drag.current = null
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const move = MOVES[event.key]
    const current = latest.current
    if (move) {
      commit({ ...current, focusX: current.focusX + move[0], focusY: current.focusY + move[1] })
    } else if (event.key === '+' || event.key === '=') {
      commit({ ...current, zoom: current.zoom + KEY_ZOOM_STEP })
    } else if (event.key === '-') {
      commit({ ...current, zoom: current.zoom - KEY_ZOOM_STEP })
    } else {
      return
    }
    event.preventDefault()
  }

  return (
    <div className="sc-cropper">
      {/* A focusable, labelled frame the picture is dragged in, as in the design: the arrow keys move the picture and
          plus and minus zoom (the slider below does the same), so it takes keys and pointer events. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={stage}
        className="sc-crop-stage"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        role="group"
        aria-label="Screenshot crop. Drag, or use the arrow keys, to move the picture."
        style={{ aspectRatio: `${aspect[0]} / ${aspect[1]}` }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
        data-step-focus
      >
        <img
          src={src}
          alt="Your screenshot"
          draggable={false}
          onLoad={(e) =>
            setNatural({ src, width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })
          }
          style={
            placed
              ? { width: placed.width, height: placed.height, left: placed.left, top: placed.top }
              : { visibility: 'hidden' }
          }
        />
      </div>
      <div className="sc-crop-zoom">
        <label htmlFor="sc-crop-zoom">Zoom</label>
        <input
          id="sc-crop-zoom"
          type="range"
          min={ZOOM_MIN}
          max={ZOOM_MAX}
          step={ZOOM_STEP}
          value={focus.zoom}
          onChange={(e) => commit({ ...latest.current, zoom: Number(e.target.value) })}
        />
        <button type="button" className="sc-linkbtn" onClick={() => commit(CENTRED)}>
          Reset
        </button>
      </div>
    </div>
  )
}
