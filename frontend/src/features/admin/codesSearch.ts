import type { AuditQuery, CodeShow } from '../../lib/adminCodes'

/** What the codes and audit address holds, with only the valid keys kept (the page applies the defaults). */
export interface CodesSearch {
  /** The promo codes tab (the invites tab is the default). */
  tab?: 'codes'
  /** On the promo codes tab: only the codes that work, have ended, or were revoked (all of them by default). */
  show?: CodeShow
  action?: string
  actor?: string
  q?: string
  /** How far back the audit log goes: 30, 90, or 0 for all of it (the default is 7). */
  days?: 30 | 90 | 0
  /** The page of the invites or plan codes list. */
  page?: number
}

const WORD = /^[a-z0-9_.:-]{1,64}$/i

export function pickCodesSearch(raw: Record<string, unknown>): CodesSearch {
  const action = typeof raw['action'] === 'string' && WORD.test(raw['action']) ? raw['action'] : null
  const actor = typeof raw['actor'] === 'string' && WORD.test(raw['actor']) ? raw['actor'] : null
  const q = typeof raw['q'] === 'string' ? raw['q'].trim().slice(0, 100) : ''
  const days = Number(raw['days'])
  const page = Number(raw['page'])
  return {
    ...(raw['tab'] === 'codes' ? { tab: 'codes' as const } : {}),
    ...(raw['tab'] === 'codes' && (raw['show'] === 'works' || raw['show'] === 'ended' || raw['show'] === 'revoked')
      ? { show: raw['show'] }
      : {}),
    ...(action ? { action } : {}),
    ...(actor ? { actor } : {}),
    ...(q ? { q } : {}),
    ...(days === 30 || days === 90 || (days === 0 && (raw['days'] === 0 || raw['days'] === '0'))
      ? { days: days as 30 | 90 | 0 }
      : {}),
    ...(Number.isInteger(page) && page > 1 && page <= 100_000 ? { page } : {}),
  }
}

export function auditQueryOf(search: CodesSearch): AuditQuery {
  return { action: search.action ?? '', actor: search.actor ?? '', q: search.q ?? '', days: search.days ?? 7 }
}

/** The address for the audit filters, leaving out the defaults. */
export function searchWithAudit(search: CodesSearch, query: AuditQuery): CodesSearch {
  return {
    ...(search.tab ? { tab: search.tab } : {}),
    ...(search.show ? { show: search.show } : {}),
    ...(search.page ? { page: search.page } : {}),
    ...(query.action ? { action: query.action } : {}),
    ...(query.actor ? { actor: query.actor } : {}),
    ...(query.q ? { q: query.q } : {}),
    ...(query.days !== 7 ? { days: query.days } : {}),
  }
}
