import type { ColorPreset, ScreenshotRef, TagCatalog, TagEdit, TagGroupRef, TagRef, TradeRow } from '../data/model'
import { ScreenshotRefused } from '../data/model'
import { SAMPLE_TAGS, SAMPLE_TAG_GROUPS, SAMPLE_TRADES, notesFor, screenshotsFor } from './generate'

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

/** The colours on offer for a tag: the same nine as the server's. */
export const TAG_COLOR_PRESETS: ColorPreset[] = [
  { color: '#2a78d6', name: 'Blue' },
  { color: '#6e5adc', name: 'Violet' },
  { color: '#0ca30c', name: 'Green' },
  { color: '#eb6834', name: 'Orange' },
  { color: '#e87ba4', name: 'Pink' },
  { color: '#1baf7a', name: 'Aqua' },
  { color: '#d03b3b', name: 'Red' },
  { color: '#eda100', name: 'Amber' },
  { color: '#898781', name: 'Gray' },
]

const freshGroups = (): TagGroupRef[] => SAMPLE_TAG_GROUPS.map((g) => ({ ...g }))
const freshTags = (): TagRef[] => Object.values(SAMPLE_TAGS).map((t) => ({ ...t }))

let entries = new Map<number, Entry>()
let groups = freshGroups()
let tags = freshTags()
let nextTagId = 100
let nextGroupId = 100
let nextShotId = 1_000_000

/** Back to the generated state. For tests; a page reload does the same. */
export function resetDemoJournal(): void {
  entries = new Map()
  groups = freshGroups()
  tags = freshTags()
  nextTagId = 100
  nextGroupId = 100
  nextShotId = 1_000_000
}

export function allTags(): TagRef[] {
  return tags
}

/** How many trades carry each tag, counting the generated trades with the journal's changes applied. */
function usage(): Map<number, number> {
  const counts = new Map<number, number>()
  for (const trade of SAMPLE_TRADES) {
    const ids = entries.get(trade.id)?.tagIds ?? trade.tags.map((t) => t.id)
    for (const id of new Set(ids)) counts.set(id, (counts.get(id) ?? 0) + 1)
  }
  return counts
}

export function tagCatalog(): TagCatalog {
  const counts = usage()
  const presets = new Set(TAG_COLOR_PRESETS.map((p) => p.color))
  const shown = tags.filter((t) => groups.some((g) => g.id === t.group_id))
  return {
    groups: groups.map((g) => ({ ...g })),
    tags: shown.map((t) => ({ ...t, color: t.color.toLowerCase(), trade_count: counts.get(t.id) ?? 0 })),
    presets: TAG_COLOR_PRESETS,
    custom_colors: [...new Set(shown.map((t) => t.color.toLowerCase()).filter((c) => !presets.has(c)))],
  }
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

/** A generated trade with the journal's changes applied, its tags always read from the current catalog. */
export function withJournal(trade: TradeRow): TradeRow {
  const entry = entries.get(trade.id)
  const ids = entry ? entry.tagIds : trade.tags.map((t) => t.id)
  const own = ids.flatMap((id) => tags.filter((t) => t.id === id))
  if (!entry) return { ...trade, tags: own }
  return { ...trade, tags: own, has_notes: hasText(entry.notes), screenshot_count: entry.screenshots.length }
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

function cleanName(raw: string): string {
  const name = raw.split(/\s+/).filter(Boolean).join(' ')
  if (!name) throw new Error('Name is empty.')
  if (name.length > MAX_TAG_NAME) throw new Error(`Keep names under ${MAX_TAG_NAME} characters.`)
  return name
}

const sameName = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()

/** A tag in a group, found by name (ignoring case) or made. Names are trimmed and their spaces collapsed. */
export function createTag(groupId: number, rawName: string): TagRef {
  const name = cleanName(rawName)
  if (!groups.some((g) => g.id === groupId)) throw new Error('That category does not exist.')
  const existing = tags.find((t) => t.group_id === groupId && sameName(t.name, name))
  if (existing) return existing
  const tag: TagRef = { id: nextTagId++, group_id: groupId, name, color: TAG_COLOR }
  tags = [...tags, tag]
  return tag
}

// ---- managing categories and tags (the tags screen) -----------------------------------------------------------------

function groupNamed(name: string, except?: number): TagGroupRef | undefined {
  return groups.find((g) => g.id !== except && sameName(g.name, name))
}

export function createGroup(rawName: string): void {
  const name = cleanName(rawName)
  if (groupNamed(name)) throw new Error(`There is already a category called ${name}.`)
  groups = [...groups, { id: nextGroupId++, name }]
}

export function renameGroup(id: number, rawName: string): void {
  if (!groups.some((g) => g.id === id)) throw new Error('That category does not exist.')
  const name = cleanName(rawName)
  if (groupNamed(name, id)) throw new Error(`There is already a category called ${name}.`)
  groups = groups.map((g) => (g.id === id ? { ...g, name } : g))
}

/** Takes the category's tags with it (and so their links to trades); the trades stay. */
export function deleteGroup(id: number): void {
  if (!groups.some((g) => g.id === id)) throw new Error('That category does not exist.')
  groups = groups.filter((g) => g.id !== id)
  tags = tags.filter((t) => t.group_id !== id)
}

/** "#ABC" is "#aabbcc"; anything that is not a hex colour is refused in the server's words. */
function cleanColor(raw: string): string {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(raw.trim())
  const digits = match?.[1]?.toLowerCase()
  if (!digits) throw new Error(`Not a hex color like #2a78d6: '${raw}'.`)
  return '#' + (digits.length === 6 ? digits : [...digits].map((c) => c + c).join(''))
}

export function editTag(id: number, edit: TagEdit): void {
  if (!tags.some((t) => t.id === id)) throw new Error('That tag does not exist.')
  if (!groups.some((g) => g.id === edit.group_id)) throw new Error('That category does not exist.')
  const name = cleanName(edit.name)
  const clash = tags.find((t) => t.id !== id && t.group_id === edit.group_id && sameName(t.name, name))
  if (clash) throw new Error(`There is already a tag called ${clash.name}.`)
  const color = cleanColor(edit.color)
  tags = tags.map((t) => (t.id === id ? { ...t, name, group_id: edit.group_id, color } : t))
}

/** Every trade with the first tag gets the second instead (once, if it had both), then the first is deleted. */
export function mergeTag(id: number, intoId: number): void {
  if (!tags.some((t) => t.id === id) || !tags.some((t) => t.id === intoId)) throw new Error('That tag does not exist.')
  if (id === intoId) throw new Error("Can't merge a tag into itself.")
  for (const trade of SAMPLE_TRADES) {
    const entry = entryFor(trade)
    if (!entry.tagIds.includes(id)) continue
    entry.tagIds = [...entry.tagIds.filter((tagId) => tagId !== id && tagId !== intoId), intoId]
  }
  deleteTag(id)
}

/** Removes the tag everywhere; trades that carried it simply no longer show it. */
export function deleteTag(id: number): void {
  if (!tags.some((t) => t.id === id)) throw new Error('That tag does not exist.')
  tags = tags.filter((t) => t.id !== id)
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
