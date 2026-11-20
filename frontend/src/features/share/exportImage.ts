import { getFontEmbedCSS } from 'html-to-image'
import type { SharePeriod, TradeDetail } from '../../lib/data/model'
import { cardNumber } from './cardModel'
import { CARD_H, CARD_W } from './ShareCard'

/*
 * The saved image: the still card (520 x 650, out of sight, no motion) drawn into a 1080 x 1350 PNG on the person's
 * own device. Nothing is uploaded.
 *
 * How: every element of the card is copied with its computed style written onto it, the fonts and the screenshot are
 * put inline as data URLs, the copy goes into an SVG `foreignObject`, and the browser draws that SVG onto a canvas. It is
 * the same card the preview shows, so the image matches it. html-to-image supplies the fonts (`getFontEmbedCSS`); the
 * copying is done here because html-to-image's own copy shrinks every font size a little (10.5 px becomes 9.9 px),
 * which would make the image differ from the preview.
 *
 * Nothing here touches the page's markup: the copy is never put in the document, so the content security policy (no
 * inline styles in the page) is not involved, and the SVG is shown to the canvas as an image.
 */

export const IMAGE_W = 1080
export const IMAGE_H = 1350

const SVG_NS = 'http://www.w3.org/2000/svg'
const XHTML_NS = 'http://www.w3.org/1999/xhtml'

/** "roundturn-NQZ6-2026-11-13-no-00042.png" */
export function imageFileName(trade: Pick<TradeDetail, 'symbol' | 'trade_day' | 'number'>): string {
  const symbol = trade.symbol.replace(/[^A-Za-z0-9_-]/g, '') || 'trade'
  return `roundturn-${symbol}-${trade.trade_day}-no-${cardNumber(trade.number)}.png`
}

/** "roundturn-day-2026-11-13-no-00057.png", "roundturn-week-2026-11-29-no-00012.png" (the week by its Sunday). */
export function periodFileName(period: SharePeriod, number: number): string {
  return `roundturn-${period.kind}-${period.start}-no-${cardNumber(number)}.png`
}

function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('The picture could not be read.'))
    reader.readAsDataURL(blob)
  })
}

/** A picture on the card as a data URL: the file itself when the server has it, else drawn from the loaded picture
 * (one made on this device has an object URL, which the policy does not let `fetch` read). */
async function pictureData(img: HTMLImageElement): Promise<string> {
  const src = img.currentSrc || img.src
  if (src.startsWith('data:')) return src
  if (!src.startsWith('blob:')) {
    try {
      const response = await fetch(src, { credentials: 'same-origin' })
      if (response.ok) return await readAsDataUrl(await response.blob())
    } catch {
      // fall through and draw it
    }
  }
  const canvas = document.createElement('canvas')
  canvas.width = img.naturalWidth
  canvas.height = img.naturalHeight
  canvas.getContext('2d')?.drawImage(img, 0, 0)
  return canvas.toDataURL('image/png')
}

function copyStyle(source: Element, target: HTMLElement | SVGElement) {
  const computed = getComputedStyle(source)
  for (let i = 0; i < computed.length; i++) {
    const name = computed.item(i)
    target.style.setProperty(name, computed.getPropertyValue(name), computed.getPropertyPriority(name))
  }
  // Chrome reports a line-clamped block's display as `flow-root`; copied as that, the clamp and its "…" are lost (the
  // notes would just be cut off). The clamp needs the box it was written with.
  const clamp = computed.getPropertyValue('-webkit-line-clamp')
  if (clamp !== '' && clamp !== 'none') target.style.setProperty('display', '-webkit-box')
}

let pseudoCount = 0

/** ::before and ::after are not elements, so they cannot be copied: a rule for each, on a class of its own. */
function copyPseudo(source: Element, target: Element, rules: string[]) {
  for (const pseudo of ['::before', '::after']) {
    const computed = getComputedStyle(source, pseudo)
    const content = computed.getPropertyValue('content')
    if (content === '' || content === 'none' || content === 'normal') continue
    const name = `sc-p${++pseudoCount}`
    target.classList.add(name)
    const declarations: string[] = []
    for (let i = 0; i < computed.length; i++) {
      const property = computed.item(i)
      declarations.push(`${property}:${computed.getPropertyValue(property)}`)
    }
    rules.push(`.${name}${pseudo}{${declarations.join(';')}}`)
  }
}

function copyNode(source: Node, rules: string[], pictures: Map<HTMLImageElement, string>): Node | null {
  if (source.nodeType === Node.TEXT_NODE) return source.cloneNode()
  if (!(source instanceof Element)) return null
  if (source instanceof SVGSVGElement) {
    // Its shapes carry their look in attributes; the outer element's computed style (colour, size, place) is enough.
    const svg = source.cloneNode(true) as SVGSVGElement
    copyStyle(source, svg)
    return svg
  }
  const copy = source.cloneNode(false) as HTMLElement
  copyStyle(source, copy)
  copyPseudo(source, copy, rules)
  if (source instanceof HTMLImageElement) {
    const data = pictures.get(source)
    if (data) copy.setAttribute('src', data)
    copy.removeAttribute('srcset')
  }
  source.childNodes.forEach((child) => {
    const childCopy = copyNode(child, rules, pictures)
    if (childCopy) copy.appendChild(childCopy)
  })
  return copy
}

/** Waits for a promise, but no longer than `ms`. `decode()` in particular can stay pending for good (seen in Chrome on
 * an out-of-sight picture after the tab was in the background), and the image must never hang on it. */
const atMost = (promise: Promise<unknown>, ms: number) =>
  Promise.race([promise.catch(() => undefined), new Promise((resolve) => window.setTimeout(resolve, ms))])

/** A picture on the card that has not finished loading yet: wait for it (a loaded one needs no wait). */
function loaded(img: HTMLImageElement): Promise<unknown> {
  if (img.complete && img.naturalWidth > 0) return Promise.resolve()
  return atMost(
    new Promise((resolve) => {
      img.addEventListener('load', resolve, { once: true })
      img.addEventListener('error', resolve, { once: true })
    }),
    10_000,
  )
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const timer = window.setTimeout(() => reject(new Error('The card took too long to draw.')), 20_000)
    img.onload = () => {
      window.clearTimeout(timer)
      resolve(img)
    }
    img.onerror = () => {
      window.clearTimeout(timer)
      reject(new Error('The card could not be drawn.'))
    }
    img.src = url
  })
}

// A short timer, not an animation frame: a page in a background tab gets no frames, and the image must still finish.
const aMoment = () => new Promise((resolve) => window.setTimeout(resolve, 30))

/*
 * Ways to blow the 520 x 650 card up to 1080 x 1350, tried in order until one fills the image. Safari does not scale the
 * card's HTML by the SVG's viewBox: it draws it at 520 x 650 in the top-left corner of an otherwise see-through image.
 * - zoom: CSS zoom lays the card out at full size, with no scaling by the SVG or the canvas.
 * - viewBox: the SVG scales the card (Firefox before version 126 has no zoom).
 * - canvas: the SVG stays card-sized and the canvas scales it while drawing (a last resort; can come out softer).
 */
type Way = 'zoom' | 'viewBox' | 'canvas'
const WAYS: readonly Way[] = ['zoom', 'viewBox', 'canvas']
const ZOOM = IMAGE_W / CARD_W

function svgMarkup(copy: Node, css: string, way: Way): string {
  const [width, height] = way === 'zoom' ? [IMAGE_W, IMAGE_H] : [CARD_W, CARD_H]
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('width', String(way === 'canvas' ? CARD_W : IMAGE_W))
  svg.setAttribute('height', String(way === 'canvas' ? CARD_H : IMAGE_H))
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`)
  const holder = document.createElementNS(SVG_NS, 'foreignObject')
  holder.setAttribute('x', '0')
  holder.setAttribute('y', '0')
  holder.setAttribute('width', String(width))
  holder.setAttribute('height', String(height))
  const page = document.createElementNS(XHTML_NS, 'div')
  if (way === 'zoom') page.style.setProperty('zoom', String(ZOOM))
  const style = document.createElementNS(XHTML_NS, 'style')
  style.textContent = css
  page.append(style, copy)
  holder.append(page)
  svg.append(holder)
  return new XMLSerializer().serializeToString(svg)
}

/*
 * Points inside the card at full size but outside it at 520 x 650, away from the rounded corners: they are solid only
 * when the card was drawn whole and at full size.
 */
const CHECK_POINTS: readonly (readonly [number, number])[] = [
  [IMAGE_W - 40, IMAGE_H / 2],
  [IMAGE_W / 2, IMAGE_H - 40],
]

function fillsTheImage(context: CanvasRenderingContext2D): boolean {
  return CHECK_POINTS.every(([x, y]) => context.getImageData(x, y, 1, 1).data[3] === 255)
}

async function draw(context: CanvasRenderingContext2D, markup: string): Promise<boolean> {
  const picture = await loadImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`)
  await atMost(picture.decode(), 2_000)
  context.clearRect(0, 0, IMAGE_W, IMAGE_H)
  context.drawImage(picture, 0, 0, IMAGE_W, IMAGE_H)
  // Safari can draw an SVG with inlined pictures and fonts blank the first time; a second draw a moment later is whole.
  await aMoment()
  context.clearRect(0, 0, IMAGE_W, IMAGE_H)
  context.drawImage(picture, 0, 0, IMAGE_W, IMAGE_H)
  return fillsTheImage(context)
}

/** The still card as a 1080 x 1350 PNG. Refuses rather than save a card drawn small or not at all. */
export async function cardToBlob(card: HTMLElement): Promise<Blob> {
  await atMost(document.fonts.ready, 5_000)
  const images = [...card.querySelectorAll('img')]
  await Promise.all(images.map(loaded))
  const pictures = new Map(await Promise.all(images.map(async (img) => [img, await pictureData(img)] as const)))
  const fonts = await getFontEmbedCSS(card)

  const rules: string[] = []
  const copy = copyNode(card, rules, pictures)
  if (!copy) throw new Error('Nothing to draw.')
  const css = `${fonts}\n${rules.join('\n')}`

  const canvas = document.createElement('canvas')
  canvas.width = IMAGE_W
  canvas.height = IMAGE_H
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('This browser cannot draw the image.')

  for (const way of WAYS) {
    if (await draw(context, svgMarkup(copy, css, way))) {
      return new Promise((resolve, reject) =>
        canvas.toBlob(
          (blob) => (blob ? resolve(blob) : reject(new Error('The image could not be made.'))),
          'image/png',
        ),
      )
    }
  }
  throw new Error('This browser drew the card at the wrong size.')
}

/** Save through the browser's own download: an `<a download>` on an object URL, let go of afterwards. */
export function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Whether this browser can put a picture on the clipboard. */
export function canCopyImage(): boolean {
  return typeof ClipboardItem !== 'undefined' && typeof navigator.clipboard?.write === 'function'
}

/** The image goes on the clipboard as it is being made: Safari needs the clipboard call in the click itself, so it
 * gets the promise of the picture rather than the picture. */
export async function copyImage(blob: Promise<Blob>) {
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
}
