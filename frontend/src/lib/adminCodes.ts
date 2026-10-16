import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { components } from './api/schema'

export type Invite = components['schemas']['InviteOut']
export type GrantCode = components['schemas']['GrantCodeOut']
export type AuditEvent = components['schemas']['AuditEventOut']
export type Overview = components['schemas']['OverviewOut']
export type CodeStatus = Invite['status']
export type CreatedInvite = components['schemas']['CreatedInviteOut']
export type CreatedGrantCode = components['schemas']['CreatedGrantCodeOut']

export type CodeShow = 'works' | 'ended' | 'revoked'

export const CODES_PAGE = 25
export const AUDIT_PAGE = 50

const KEY = ['admin-codes'] as const

/** The numbers on the overview. */
export function useOverview() {
  return useQuery({
    queryKey: [...KEY, 'overview'],
    queryFn: () => unwrap(api.GET('/api/admin/v1/metrics/overview')),
    retry: false,
  })
}

export function useInvites(page: number) {
  return useQuery({
    queryKey: [...KEY, 'invites', page],
    queryFn: () =>
      unwrap(
        api.GET('/api/admin/v1/invites', { params: { query: { limit: CODES_PAGE, offset: (page - 1) * CODES_PAGE } } }),
      ),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/** Promo and plan codes, a page at a time; `show` narrows them to the ones that work, have ended, or were revoked. */
export function useGrantCodes(page: number, show?: CodeShow) {
  return useQuery({
    queryKey: [...KEY, 'grant-codes', page, show ?? 'all'],
    queryFn: () =>
      unwrap(
        api.GET('/api/admin/v1/grant-codes', {
          params: { query: { limit: CODES_PAGE, offset: (page - 1) * CODES_PAGE, ...(show ? { show } : {}) } },
        }),
      ),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/** What narrows the audit log. `days` is how far back (0 is all of it). */
export interface AuditQuery {
  action: string
  actor: string
  q: string
  days: 7 | 30 | 90 | 0
}

/** The audit log, newest first, a page at a time: each page says where the next one starts. */
export function useAudit(query: AuditQuery) {
  return useInfiniteQuery({
    queryKey: [...KEY, 'audit', query],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/admin/v1/audit', {
          params: {
            query: {
              ...(query.action ? { action: query.action } : {}),
              ...(query.actor ? { actor: query.actor } : {}),
              ...(query.q ? { q: query.q } : {}),
              ...(query.days ? { days: query.days } : {}),
              limit: AUDIT_PAGE,
              ...(pageParam !== null ? { before: pageParam } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_before ?? undefined,
    retry: false,
  })
}

export interface NewInvite {
  label: string
  email: string | null
  maxUses: number
  days: number | null
  grantCodeId: number | null
}

export interface NewGrantCode {
  /** The promo code to type (FREE30), or null for a random one shown once. */
  code: string | null
  label: string
  days: number
  maxRedemptions: number | null
  /** The last day it works, as YYYY-MM-DD (it stops at the end of that day, UTC), or null for never. */
  expiresOn: string | null
}

/** Make an invite. The answer holds the code, which is shown once and never again. */
export function useCreateInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (invite: NewInvite) =>
      unwrap(
        api.POST('/api/admin/v1/invites', {
          body: {
            label: invite.label,
            email: invite.email,
            max_uses: invite.maxUses,
            days: invite.days,
            grant_code_id: invite.grantCodeId,
          },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  })
}

export function useCreateGrantCode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (code: NewGrantCode) =>
      unwrap(
        api.POST('/api/admin/v1/grant-codes', {
          body: {
            ...(code.code ? { code: code.code } : {}),
            label: code.label,
            days: code.days,
            max_redemptions: code.maxRedemptions,
            ...(code.expiresOn ? { expires_on: code.expiresOn } : {}),
          },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  })
}

export type Revoke = { kind: 'invite' | 'grant-code'; id: number; reason: string }

/** Stop an invite or a plan code being used. People who already used it keep what they got. */
export function useRevokeCode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (revoke: Revoke) => {
      const request: Promise<{ data?: unknown; error?: unknown; response?: Response }> =
        revoke.kind === 'invite'
          ? api.POST('/api/admin/v1/invites/{invite_id}/revoke', {
              params: { path: { invite_id: revoke.id } },
              body: { reason: revoke.reason },
            })
          : api.POST('/api/admin/v1/grant-codes/{grant_id}/revoke', {
              params: { path: { grant_id: revoke.id } },
              body: { reason: revoke.reason },
            })
      return unwrap(request)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  })
}

/** A typed promo code as it will be kept: trimmed and in capitals, or null if it is not 3 to 20 letters and digits. */
export function promoCodeOf(text: string): string | null {
  const code = text.trim().toUpperCase()
  return /^[A-Z0-9]{3,20}$/.test(code) ? code : null
}

/** "Dec 31, 2026" for a YYYY-MM-DD day, in UTC (the day a code stops working is a UTC day). */
function dayText(day: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${day}T00:00:00Z`),
  )
}

/** What people are told a code gives, in one sentence, for the form's preview. */
export function promoPreview(code: {
  code: string | null
  days: number
  people: number | null
  endsOn: string | null
}) {
  const who = code.code ? `Anyone who enters ${code.code}` : 'Anyone with this code'
  const limits = [
    code.people === null
      ? null
      : `the first ${code.people.toLocaleString('en-US')} ${code.people === 1 ? 'person' : 'people'}`,
    code.endsOn ? `until the end of ${dayText(code.endsOn)}` : null,
  ].filter((part): part is string => part !== null)
  const days = `${code.days} ${code.days === 1 ? 'day' : 'days'} of Pro, free`
  return `${who} gets ${days}${limits.length ? ` (${limits.join(', ')})` : ''}. Their journal stays theirs when the days end.`
}

/** The words for where a code stands. */
export const STATUS_TEXT: Record<CodeStatus, string> = {
  works: 'Works',
  used_up: 'Used up',
  expired: 'Expired',
  revoked: 'Revoked',
}

/** Words for a fact's name where the stored name is an abbreviation. Any other name is shown with its underscores as spaces. */
const FACT_WORDS: Record<string, string> = { mfa: 'two-step verification' }
const factWord = (key: string) => FACT_WORDS[key] ?? key.replaceAll('_', ' ')

/** What an event says besides its kind: the reason first, then the other small facts, as one short line. */
export function metaText(meta: Record<string, string | number | boolean | null>): string {
  const parts: string[] = []
  const reason = meta['reason']
  if (typeof reason === 'string' && reason) parts.push(reason)
  for (const [key, value] of Object.entries(meta)) {
    if (key === 'reason' || value === null || value === '' || value === false) continue
    parts.push(value === true ? factWord(key) : `${factWord(key)} ${String(value)}`)
  }
  const line = parts.join(' · ')
  return line.length > 140 ? `${line.slice(0, 137)}…` : line
}
