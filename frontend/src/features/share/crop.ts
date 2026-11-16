/**
 * The crop of the screenshot on a share card. It is kept as the point of the picture (0 to 1 each way) that sits at the
 * centre of the frame, and a zoom, not as a rectangle: the frame's shape differs between the card styles and options,
 * and a focus point still means the same thing in a frame of another shape. One function, `place`, turns it into a size
 * and an offset, for the cropper and for the card alike.
 */

export interface Focus {
  focusX: number // 0 (left edge of the picture) to 1 (right edge)
  focusY: number // 0 (top) to 1 (bottom)
  zoom: number // 1 = the picture just covers the frame, up to ZOOM_MAX
}

/** Which screenshot, and how it is cropped. Kept in the pop-up only: never sent to the server. */
export interface Crop extends Focus {
  screenshotId: number
}

export const ZOOM_MIN = 1
export const ZOOM_MAX = 4
export const ZOOM_STEP = 0.01 // the slider
export const KEY_ZOOM_STEP = 0.1 // plus and minus with the frame focused
export const KEY_MOVE_STEP = 0.02 // the arrow keys

export const CENTRED: Focus = { focusX: 0.5, focusY: 0.5, zoom: 1 }

/** Where the picture goes in a frame: its drawn size, its offset from the frame's top left, and the focus point after
 * clamping (so the frame never shows beyond the picture's edge). */
export interface Placement {
  width: number
  height: number
  left: number
  top: number
  focusX: number
  focusY: number
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

/** A zoom inside its range, to two decimals (so repeated key presses do not drift). */
export function clampZoom(zoom: number): number {
  return Math.round(clamp(zoom, ZOOM_MIN, ZOOM_MAX) * 100) / 100
}

export function place(frameW: number, frameH: number, imageW: number, imageH: number, focus: Focus): Placement {
  if (!(frameW > 0 && frameH > 0 && imageW > 0 && imageH > 0)) {
    return { width: 0, height: 0, left: 0, top: 0, focusX: focus.focusX, focusY: focus.focusY }
  }
  const scale = Math.max(frameW / imageW, frameH / imageH) * clampZoom(focus.zoom) // zoom 1: just covers the frame
  const width = imageW * scale
  const height = imageH * scale
  const focusX = clamp(focus.focusX, frameW / 2 / width, 1 - frameW / 2 / width)
  const focusY = clamp(focus.focusY, frameH / 2 / height, 1 - frameH / 2 / height)
  return { width, height, left: frameW / 2 - focusX * width, top: frameH / 2 - focusY * height, focusX, focusY }
}
