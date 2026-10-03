import { useCallback, useState } from 'react'

/**
 * The width of an element in CSS pixels, kept current as it resizes. A chart draws at this width, so its labels stay a
 * readable size on a phone instead of shrinking with the picture. `fallback` is used until the element is measured, and
 * wherever nothing is laid out (the tests).
 */
export function useMeasuredWidth<T extends HTMLElement>(fallback = 800) {
  const [width, setWidth] = useState(fallback)
  const ref = useCallback((node: T | null) => {
    if (!node || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(([entry]) => {
      const measured = Math.round(entry?.contentRect.width ?? 0)
      if (measured > 0) setWidth(measured)
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}
