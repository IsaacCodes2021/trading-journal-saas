import { vi } from 'vitest'
import type { AdminUser, AdminUserDetail } from '../../lib/admin'
import type { BetaPerson } from '../../lib/adminBeta'
import type { AuditEvent, GrantCode, Invite, Overview } from '../../lib/adminCodes'
import type { AdminTemplate, AdminTemplateKey, TemplateImport } from '../../lib/adminRules'
import { FIELDS, GROUPS } from '../../lib/demo/ruleData'
import { flagKind } from '../../lib/rules'
import { json, stubApi, type ApiOptions } from '../../test-utils'

/** Test helpers for the operator screens: an in-memory `/api/admin/v1` that behaves like the server's, and what it was asked. */

export const SELF = '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11' // the signed-in person's own id, as the session stub gives it

export const user = (id: string, email: string, over: Partial<AdminUser> = {}): AdminUser => ({
  id,
  email,
  status: 'active',
  is_admin: false,
  created_at: '2026-08-14T09:00:00Z',
  last_login_at: '2026-10-01T19:40:00Z',
  deletion_scheduled_at: null,
  plan: { effective: 'free', source: 'free', expires_at: null },
  usage: { accounts: 2, trades: 1204, screenshots: 3, storage_bytes: 38 * 1024 * 1024, last_import_at: null },
  ...over,
})

export const detail = (u: AdminUser, over: Partial<AdminUserDetail> = {}): AdminUserDetail => ({
  user: u,
  active_sessions: 2,
  overrides: [],
  redemptions: [],
  events: [],
  subscription: null,
  billing_events: [],
  ...over,
})

export const invite = (id: number, label: string, over: Partial<Invite> = {}): Invite => ({
  id,
  label,
  email: null,
  max_uses: 1,
  used: 0,
  expires_at: null,
  revoked_at: null,
  created_at: '2026-09-22T10:00:00Z',
  grant_code_id: null,
  grant_label: null,
  status: 'works',
  ...over,
})

export const grant = (id: number, label: string, over: Partial<GrantCode> = {}): GrantCode => ({
  id,
  code: null,
  kind: 'free_days',
  label,
  plan: 'pro',
  days: 90,
  max_redemptions: null,
  used: 0,
  expires_at: null,
  created_by: 'admin:x',
  created_at: '2026-09-22T10:00:00Z',
  revoked_at: null,
  status: 'works',
  ...over,
})

export const event = (id: number, action: string, over: Partial<AuditEvent> = {}): AuditEvent => ({
  id,
  at: '2026-10-02T08:14:00Z',
  actor: 'user',
  action,
  actor_email: null,
  user_id: null,
  user_email: null,
  meta: {},
  ...over,
})

export const person = (id: number, firstName: string, over: Partial<BetaPerson> = {}): BetaPerson => ({
  id,
  first_name: firstName,
  email: `${firstName.toLowerCase()}@example.test`,
  prop_firm: 'Topstep',
  joined_at: '2026-10-09T09:00:00Z',
  state: 'active',
  code: 'used',
  access_ends_at: '2026-11-08T09:00:00Z',
  revoked_at: null,
  code_email_sent_at: '2026-10-09T09:00:05Z',
  code_email_failures: 0,
  notice_sent_at: null,
  ...over,
})

/** A firm's template as the operator API gives it: every field (empty unless given), its flags and source notes. */
export const template = (
  id: number,
  values: Record<string, string | number | null>,
  over: Partial<Omit<AdminTemplate, 'values'>> = {},
): AdminTemplate => ({
  id,
  values: { ...Object.fromEntries(FIELDS.map((f) => [f.name, null])), ...values },
  flags: [],
  source_notes: {},
  updated_at: '2026-10-14T14:08:00Z',
  updated_by: 'cli:owner',
  ...over,
})

const keyOf = (t: AdminTemplate): AdminTemplateKey => ({
  id: t.id,
  firm: String(t.values['firm']),
  program: String(t.values['program']),
  account_size: String(t.values['account_size']),
  phase: t.values['phase'] as AdminTemplateKey['phase'],
  rule_version: String(t.values['rule_version']),
  effective_from: (t.values['effective_from'] as string | null) ?? null,
  effective_from_note: t.source_notes['effective_from'] ?? null,
  data_confidence: (t.values['data_confidence'] as string | null) ?? null,
  flagged: t.flags.length,
})

export const OVERVIEW: Overview = {
  accounts: 64,
  signups_7d: 18,
  active_7d: 41,
  active: 60,
  suspended: 2,
  pending_deletion: 2,
  pro: 7,
  free: 57,
  open_invites: 3,
  open_grant_codes: 1,
  grants_ending_7d: 4,
  paying: 5,
  on_trial: 9,
}

export interface Call {
  method: string
  path: string
  query: Record<string, string>
  body: unknown
  csrf: string | null
}

export interface AdminStubOptions extends ApiOptions {
  users?: AdminUser[]
  details?: Record<string, AdminUserDetail>
  /** Answer every change with this problem instead of making it. */
  changeFails?: { status: number; detail: string }
  /** Answer the list with this problem. */
  listFails?: { status: number; detail: string }
  /** Answer a look at one account with this problem. */
  detailFails?: { status: number; detail: string }
  invites?: Invite[]
  grantCodes?: GrantCode[]
  /** Newest first, as the server gives them. */
  auditEvents?: AuditEvent[]
  overview?: Overview
  /** Answer the overview, the invites, the plan codes or the log with a problem. */
  codesFail?: { status: number; detail: string }
  /** The people who joined the beta, newest first, as the server gives them. */
  beta?: BetaPerson[]
  /** Revoking works, but the "access ended" email does not leave. */
  betaNoticeFails?: boolean
  /** The firms' rule templates, in the server's order. An edit replaces the template the next read sees. */
  templates?: AdminTemplate[]
  /** Answer a template edit with this problem (with field errors, as the server's 422). */
  templateEditFails?: { status: number; detail: string; errors?: { field: string; message: string }[] }
  /** What an import answers, by whether it was a dry run. */
  importAnswer?: (dryRun: boolean) => TemplateImport
}

/** Replaces `fetch`: the app's usual calls (as an operator who just verified, unless told otherwise) plus the operator API. */
export function stubAdmin(options: AdminStubOptions = {}) {
  const { users = [], details = {}, changeFails, listFails, detailFails, ...rest } = options
  const {
    invites = [],
    grantCodes = [],
    auditEvents = [],
    overview = OVERVIEW,
    codesFail,
    beta: given = [],
    ...more
  } = rest
  const beta = [...given] // a removal takes the person out of the list the next read sees
  const { betaNoticeFails = false, templates: givenTemplates = [], templateEditFails, importAnswer, ...api } = more
  const templates = [...givenTemplates] // an edit replaces the template the next read sees
  let edits = 0
  let nextId = 100
  stubApi({ operator: true, mfaFresh: true, ...api })
  const inner = globalThis.fetch
  const calls: Call[] = []
  const problem = (p: { status: number; detail: string }) =>
    json({ title: 'Problem', status: p.status, detail: p.detail, errors: null }, p.status)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      if (url.pathname === '/api/v1/rule-fields') return json({ groups: GROUPS, fields: FIELDS })
      if (!url.pathname.startsWith('/api/admin/v1')) return inner(request)
      const multipart = (request.headers.get('content-type') ?? '').startsWith('multipart/form-data')
      const body =
        request.method === 'GET'
          ? null
          : multipart
            ? { multipart: await request.clone().text() }
            : await request.clone().json()
      calls.push({
        method: request.method,
        path: url.pathname,
        query: Object.fromEntries(url.searchParams),
        body,
        csrf: request.headers.get('x-csrf-token'),
      })
      if (url.pathname === '/api/admin/v1/users') {
        if (listFails) return problem(listFails)
        const q = (url.searchParams.get('q') ?? '').toLowerCase()
        const status = url.searchParams.get('status')
        const limit = Number(url.searchParams.get('limit') ?? 50)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        const matching = users.filter(
          (u) => (!q || u.email.toLowerCase().includes(q) || u.id === q) && (!status || u.status === status),
        )
        return json({ users: matching.slice(offset, offset + limit), total: matching.length, limit, offset })
      }
      const [, , , , section, rawId, verb] = url.pathname.split('/')
      if (codesFail && ['invites', 'grant-codes', 'audit', 'metrics'].includes(section ?? '')) return problem(codesFail)
      if (section === 'rule-templates') {
        if (request.method === 'GET' && !rawId) {
          if (listFails) return problem(listFails)
          const words = (url.searchParams.get('q') ?? '').toLowerCase().split(/\s+/).filter(Boolean)
          const limit = Number(url.searchParams.get('limit') ?? 50)
          const offset = Number(url.searchParams.get('offset') ?? 0)
          const matching = templates.map(keyOf).filter((k) => {
            const name = `${k.firm} ${k.program} ${k.account_size} ${k.phase} ${k.rule_version}`.toLowerCase()
            return words.every((w) => name.includes(w))
          })
          return json({ templates: matching.slice(offset, offset + limit), total: matching.length, limit, offset })
        }
        if (rawId === 'import') {
          if (changeFails) return problem(changeFails)
          const dryRun = url.searchParams.get('dry_run') === 'true'
          const answer = importAnswer?.(dryRun)
          return json(
            answer ?? {
              applied: !dryRun,
              inserted: 0,
              updated: 0,
              unchanged: templates.length,
              changed: [],
              problems: [],
            },
          )
        }
        const index = templates.findIndex((t) => String(t.id) === rawId)
        const found = templates[index]
        if (!found) return problem({ status: 404, detail: 'There is no such rule template.' })
        if (request.method === 'GET') return detailFails ? problem(detailFails) : json(found)
        if (templateEditFails) {
          const { status, detail: text, errors = null } = templateEditFails
          return json({ title: 'Problem', status, detail: text, errors }, status)
        }
        const sent = body as { values: AdminTemplate['values']; flags: { field: string; reason: string }[] }
        edits += 1
        const saved: AdminTemplate = {
          ...found,
          values: { ...Object.fromEntries(FIELDS.map((f) => [f.name, null])), ...sent.values },
          flags: sent.flags.map((f) => ({ ...f, kind: flagKind(f.reason) })),
          // A field that now has a value loses the source note that explained its empty cell (as the server).
          source_notes: Object.fromEntries(
            Object.entries(found.source_notes).filter(([name]) => (sent.values[name] ?? null) === null),
          ),
          updated_at: `2026-10-14T15:${String(edits).padStart(2, '0')}:00Z`,
          updated_by: `admin:${SELF}`,
        }
        templates[index] = saved
        return json(saved)
      }
      if (section === 'beta') {
        if (request.method === 'GET') {
          if (listFails) return problem(listFails)
          const q = (url.searchParams.get('q') ?? '').toLowerCase()
          const state = url.searchParams.get('state')
          const limit = Number(url.searchParams.get('limit') ?? 50)
          const offset = Number(url.searchParams.get('offset') ?? 0)
          const matching = beta.filter(
            (p) =>
              (!state || p.state === state) &&
              (!q || [p.first_name, p.email, p.prop_firm].some((text) => text.toLowerCase().includes(q))),
          )
          const counts: Record<string, number> = { waiting: 0, active: 0, ended: 0, revoked: 0, all: beta.length }
          for (const p of beta) counts[p.state] = (counts[p.state] ?? 0) + 1
          return json({ people: matching.slice(offset, offset + limit), total: matching.length, limit, offset, counts })
        }
        if (changeFails) return problem(changeFails)
        const found = beta.find((p) => String(p.id) === rawId)
        if (!found) return problem({ status: 404, detail: 'There is no such person.' })
        if (verb === 'remove') {
          if (found.state !== 'waiting') {
            return problem({
              status: 409,
              detail:
                'They have an account. Delete the account instead: that removes the sign-up and its codes with it.',
            })
          }
          beta.splice(beta.indexOf(found), 1)
          return json({ removed: found.id })
        }
        if (found.state === 'active') {
          const changed = { ...found, state: 'revoked' as const, revoked_at: '2026-10-10T12:00:00Z' }
          return json({ person: changed, notice: betaNoticeFails ? 'pending' : 'sent' })
        }
        if (found.state === 'waiting' && found.code === 'unused') {
          return json({ person: { ...found, code: 'withdrawn' as const }, notice: 'not_needed' })
        }
        return problem({ status: 409, detail: 'There is nothing left to end for this person.' })
      }
      if (url.pathname === '/api/admin/v1/metrics/overview') return json(overview)
      if (url.pathname === '/api/admin/v1/audit') {
        const q = (url.searchParams.get('q') ?? '').toLowerCase()
        const action = url.searchParams.get('action')
        const actor = url.searchParams.get('actor')
        const before = Number(url.searchParams.get('before') ?? Infinity)
        const limit = Number(url.searchParams.get('limit') ?? 50)
        const matching = auditEvents.filter(
          (e) =>
            e.id < before &&
            (!action || e.action === action) &&
            (!actor || e.actor === actor) &&
            (!q || (e.user_email ?? '').includes(q) || (e.actor_email ?? '').includes(q)),
        )
        const page = matching.slice(0, limit)
        return json({ events: page, next_before: matching.length > limit ? (page[page.length - 1]?.id ?? null) : null })
      }
      if (section === 'invites' || section === 'grant-codes') {
        const list = section === 'invites' ? invites : grantCodes
        if (request.method === 'GET') {
          const limit = Number(url.searchParams.get('limit') ?? 50)
          const offset = Number(url.searchParams.get('offset') ?? 0)
          // The server's "Show" filter: works, ended (used up or expired), revoked.
          const show = url.searchParams.get('show')
          const shown =
            section === 'invites' || !show
              ? list
              : grantCodes.filter((g) =>
                  show === 'ended' ? g.status === 'used_up' || g.status === 'expired' : g.status === show,
                )
          const rows = shown.slice(offset, offset + limit)
          return json(
            section === 'invites'
              ? { invites: rows, total: shown.length, limit, offset }
              : { grant_codes: rows, total: shown.length, limit, offset },
          )
        }
        if (changeFails) return problem(changeFails)
        if (verb === 'revoke') {
          const found = list.find((c) => String(c.id) === rawId)
          return found
            ? json({ ...found, status: 'revoked', revoked_at: '2026-10-04T12:00:00Z' })
            : problem({ status: 404, detail: 'There is no such code.' })
        }
        nextId += 1
        const sent = body as { label?: string; code?: string }
        return section === 'invites'
          ? json({ invite: invite(nextId, sent.label ?? ''), code: 'TJ-NEW1-CODE' })
          : json({
              grant_code: grant(nextId, sent.label ?? '', { code: sent.code ?? null }),
              code: sent.code ?? 'GR-NEW1-CODE',
            })
      }
      const id = url.pathname.split('/')[5] ?? ''
      if (request.method === 'GET') {
        if (detailFails) return problem(detailFails)
        const account = users.find((u) => u.id === id)
        const found = details[id] ?? (account ? detail(account) : null)
        return found ? json(found) : problem({ status: 404, detail: 'There is no such account.' })
      }
      if (changeFails) return problem(changeFails)
      if (url.pathname.endsWith('/revoke-sessions')) return json({ ended: 3 })
      return json(users.find((u) => u.id === id) ?? user(id, 'someone@example.test'))
    }),
  )
  return calls
}

/** The changes made, in order: what was asked of which account, with the reason and the CSRF token. */
export const changes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET')
