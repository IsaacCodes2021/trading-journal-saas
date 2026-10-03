import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no scrolling; the router asks for it on navigation.
window.scrollTo = vi.fn() as typeof window.scrollTo

// ...and no object URLs; the sample journal makes one for each image a test adds.
let blobCount = 0
URL.createObjectURL = () => `blob:test/${++blobCount}`
URL.revokeObjectURL = () => undefined

// ProseMirror (the notes editor) measures text. jsdom has no layout, so these report "nothing, nowhere".
const emptyRect = {
  x: 0,
  y: 0,
  width: 0,
  height: 0,
  top: 0,
  left: 0,
  bottom: 0,
  right: 0,
  toJSON: () => ({}),
} as DOMRect
Range.prototype.getBoundingClientRect = () => emptyRect
Range.prototype.getClientRects = () => [] as unknown as DOMRectList
document.elementFromPoint = () => null

afterEach(cleanup)
