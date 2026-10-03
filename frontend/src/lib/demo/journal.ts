import type { ScreenshotRef, TagCatalog, TagRef, TradeRow } from '../data/model'
import { ScreenshotRefused } from '../data/model'
import { SAMPLE_TAGS, SAMPLE_TAG_GROUPS, notesFor, screenshotsFor } from './generate'

/*
 * The sample journal: notes, tags and screenshots a person can change while trying the screens. It is in memory only:
 * a reload puts everything back, and the "Sample data" banner says edits are not kept. The real source keeps these on
 * the server. Reads overlay what was changed onto the generated trades, so the trade log shows a tag the detail page
 * just added.
 */

interface Entry {
  notes: string
  tagIds: number[]
  screenshots: ScreenshotRef[]
}

export const MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
export const SCREENSHOT_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] as const
const TAG_COLOR = '#6EA8FE'
const MAX_TAG_NAME = 40

let entries = new Map<number, Entry>()
let extraTags: TagRef[] = []
let nextShotId = 1_000_000

/** Back to the generated state. For tests; a page reload does the same. */
export function resetDemoJournal(): void {
  entries = new Map()
  extraTags = []
  nextShotId = 1_000_000
}

export function allTags(): TagRef[] {
  return [...Object.values(SAMPLE_TAGS), ...extraTags]
}

export function tagCatalog(): TagCatalog {
  return { groups: SAMPLE_TAG_GROUPS, tags: allTags() }
}

function entryFor(trade: TradeRow): Entry {
  let entry = entries.get(trade.id)
  if (!entry) {
    entry = {
      notes: trade.has_notes ? notesFor(trade) : '',
      tagIds: trade.tags.map((t) => t.id),
      screenshots: screenshotsFor(trade),
    }
    entries.set(trade.id, entry)
  }
  return entry
}

export function hasText(html: string): boolean {
  return /\S/.test(html.replace(/<[^>]*>/g, ''))
}

/** A generated trade with the journal's changes applied. */
export function withJournal(trade: TradeRow): TradeRow {
  const entry = entries.get(trade.id)
  if (!entry) return trade
  const tags = entry.tagIds.flatMap((id) => allTags().filter((t) => t.id === id))
  return { ...trade, tags, has_notes: hasText(entry.notes), screenshot_count: entry.screenshots.length }
}

export function notesOf(trade: TradeRow): string {
  return entryFor(trade).notes
}

export function screenshotsOf(trade: TradeRow): ScreenshotRef[] {
  return entryFor(trade).screenshots
}

export function saveNotes(trade: TradeRow, html: string): void {
  entryFor(trade).notes = html
}

export function setTradeTag(trade: TradeRow, tagId: number, on: boolean): void {
  const entry = entryFor(trade)
  const without = entry.tagIds.filter((id) => id !== tagId)
  entry.tagIds = on ? [...without, tagId] : without
}

/** A tag in a group, found by name (ignoring case) or made. Names are trimmed and their spaces collapsed. */
export function createTag(groupId: number, rawName: string): TagRef {
  const name = rawName.split(/\s+/).filter(Boolean).join(' ')
  if (!name) throw new Error('Give the tag a name.')
  if (name.length > MAX_TAG_NAME) throw new Error(`Tag names are at most ${MAX_TAG_NAME} characters.`)
  if (!SAMPLE_TAG_GROUPS.some((g) => g.id === groupId)) throw new Error('Choose a group for the tag.')
  const existing = allTags().find((t) => t.group_id === groupId && t.name.toLowerCase() === name.toLowerCase())
  if (existing) return existing
  const tag: TagRef = { id: 100 + extraTags.length, group_id: groupId, name, color: TAG_COLOR }
  extraTags = [...extraTags, tag]
  return tag
}

/** Checks each file the way the server will (type and size; the server also reads the bytes), then keeps all or none. */
export function addScreenshots(trade: TradeRow, files: readonly File[]): ScreenshotRef[] {
  for (const file of files) {
    if (!(SCREENSHOT_TYPES as readonly string[]).includes(file.type)) {
      throw new ScreenshotRefused(`${file.name || 'That file'} is not a PNG, JPEG, GIF or WebP image.`)
    }
    if (file.size > MAX_SCREENSHOT_BYTES) {
      throw new ScreenshotRefused(`${file.name || 'That file'} is larger than 10 MB.`)
    }
  }
  const added = files.map((file): ScreenshotRef => ({
    id: nextShotId++,
    original_name: file.name || 'pasted-image.png',
    created_at: new Date().toISOString(),
    src: URL.createObjectURL(file),
  }))
  const entry = entryFor(trade)
  entry.screenshots = [...entry.screenshots, ...added]
  return added
}

export function deleteScreenshot(trade: TradeRow, screenshotId: number): void {
  const entry = entryFor(trade)
  entry.screenshots = entry.screenshots.filter((s) => s.id !== screenshotId)
}
