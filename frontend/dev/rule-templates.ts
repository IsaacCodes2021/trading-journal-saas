import type { IncomingMessage } from 'node:http'
import { FIELDS, GROUPS, TEMPLATES } from '../src/lib/demo/ruleData'

/*
 * DEV ONLY, for the session stub: the operator's firm rules screen with no backend. The templates are the sample's rows of
 * the research CSV; an edit is kept until the dev server stops, and an import only answers with sample counts.
 */

type Send = (status: number, body?: unknown) => void
type Values = Record<string, string | number | null>

const EMPTY: Values = Object.fromEntries(FIELDS.map((f) => [f.name, null]))
const KEY = ['firm', 'program', 'account_size', 'phase', 'rule_version'] as const

const kindOf = (reason: string) => {
  const lower = reason.trim().toLowerCase()
  if (lower.startsWith('conflict')) return 'conflict'
  return lower.startsWith('third-party only') ? 'third-party only' : 'unknown'
}

interface Stored {
  id: number
  values: Values
  flags: { field: string; reason: string; kind: string }[]
  source_notes: Record<string, string>
  updated_at: string
  updated_by: string
}

const templates = new Map<number, Stored>(
  TEMPLATES.map((t) => [
    t.id,
    {
      id: t.id,
      values: { ...EMPTY, ...t.values },
      flags: t.flags,
      source_notes: t.source_notes,
      updated_at: '2026-10-14T14:08:00Z',
      updated_by: 'seed',
    },
  ]),
)

const nameOf = (values: Values) => KEY.map((k) => String(values[k] ?? '')).join(' · ')

function keyOf(t: Stored) {
  const v = t.values
  return {
    id: t.id,
    firm: v['firm'],
    program: v['program'],
    account_size: v['account_size'],
    phase: v['phase'],
    rule_version: v['rule_version'],
    effective_from: v['effective_from'] ?? null,
    effective_from_note: t.source_notes['effective_from'] ?? null,
    data_confidence: v['data_confidence'] ?? null,
    flagged: t.flags.length,
  }
}

/** Answers `/api/v1/rule-fields` and `/api/admin/v1/rule-templates…`; false when the path is neither. */
export function answerRuleTemplates(request: IncomingMessage, path: string, send: Send): boolean {
  if (path === '/api/v1/rule-fields') {
    send(200, { groups: GROUPS, fields: FIELDS })
    return true
  }
  if (!path.startsWith('/api/admin/v1/rule-templates')) return false
  const query = new URL(request.url ?? '', 'http://x').searchParams
  if (path === '/api/admin/v1/rule-templates' && request.method === 'GET') {
    const words = (query.get('q') ?? '').toLowerCase().split(/\s+/).filter(Boolean)
    const limit = Number(query.get('limit') ?? 50)
    const offset = Number(query.get('offset') ?? 0)
    const hits = [...templates.values()].filter((t) => words.every((w) => nameOf(t.values).toLowerCase().includes(w)))
    send(200, { templates: hits.slice(offset, offset + limit).map(keyOf), total: hits.length, limit, offset })
    return true
  }
  if (path === '/api/admin/v1/rule-templates/import' && request.method === 'POST') {
    request.resume() // the file itself is not read: the sample only says what an import of the research CSV would do
    request.on('end', () => {
      const first = templates.values().next().value
      send(200, {
        applied: query.get('dry_run') !== 'true',
        inserted: 0,
        updated: 1,
        unchanged: templates.size - 1,
        changed: first ? [nameOf(first.values)] : [],
        problems: [],
      })
    })
    return true
  }
  const id = Number(path.split('/')[5])
  const found = templates.get(id)
  if (!found) {
    send(404, { type: 'about:blank', title: 'Not Found', status: 404, detail: 'There is no such rule template.' })
    return true
  }
  if (request.method === 'GET') {
    send(200, found)
    return true
  }
  let raw = ''
  request.on('data', (chunk: Buffer) => (raw += chunk.toString()))
  request.on('end', () => {
    const body = JSON.parse(raw || '{}') as { values?: Values; flags?: { field: string; reason: string }[] }
    const values = { ...EMPTY, ...body.values }
    const saved = {
      ...found,
      values,
      flags: (body.flags ?? []).map((f) => ({ ...f, kind: kindOf(f.reason) })),
      // A field that now has a value loses the source note that explained its empty cell (as the server).
      source_notes: Object.fromEntries(Object.entries(found.source_notes).filter(([name]) => values[name] === null)),
      updated_at: new Date().toISOString(),
      updated_by: 'admin:5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11', // the stub's signed-in operator: "by you"
    }
    templates.set(id, saved)
    send(200, saved)
  })
  return true
}
