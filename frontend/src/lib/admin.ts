import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { components } from './api/schema'

export type AdminUser = components['schemas']['AdminUserOut']
export type AdminUserDetail = components['schemas']['AdminUserDetailOut']
export type AdminOverride = components['schemas']['OverrideOut']
export type AdminEvent = components['schemas']['EventOut']
export type UserStatus = AdminUser['status']

export const PAGE_SIZE = 25

/** Where signing in again for a fresh second factor comes back to (the server's `stepup` asks the provider for it now). */
export function stepUpUrl(next: string): string {
  return `/auth/login?stepup=1&next=${encodeURIComponent(next)}`
}

export interface UserQuery {
  /** Part of an email address, or an account id. */
  q: string
  status: UserStatus | ''
  /** 1-based. */
  page: number
}

const KEY = ['admin'] as const

/** One page of accounts. The last page stays on screen while the next one loads, so the table does not flash empty. */
export function useAdminUsers(query: UserQuery) {
  return useQuery({
    queryKey: [...KEY, 'users', query],
    queryFn: () =>
      unwrap(
        api.GET('/api/admin/v1/users', {
          params: {
            query: {
              ...(query.q ? { q: query.q } : {}),
              ...(query.status ? { status: query.status } : {}),
              limit: PAGE_SIZE,
              offset: (query.page - 1) * PAGE_SIZE,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/**
 * One account in full. Looking is written to the audit log by the server, so this reads when the page asks and after a
 * change, and not on every return to the window.
 */
export function useAdminUser(id: string | null) {
  return useQuery({
    queryKey: [...KEY, 'user', id],
    queryFn: () => unwrap(api.GET('/api/admin/v1/users/{user_id}', { params: { path: { user_id: id ?? '' } } })),
    enabled: id !== null,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

/** Every change an operator can make to an account. Each needs a reason; the server keeps it in the audit log. */
export type Change =
  | { kind: 'suspend' | 'unsuspend' | 'revoke-sessions' | 'schedule-deletion' | 'cancel-deletion'; reason: string }
  | { kind: 'set-override'; plan: 'free' | 'pro'; expiresAt: string | null; reason: string }
  | { kind: 'revoke-override'; reason: string }

/** What `unwrap` needs of a reply, whichever of the different answers it is. */
type Reply = { data?: unknown; error?: unknown; response?: Response }

function send(id: string, change: Change): Promise<Reply> {
  const path = { user_id: id }
  switch (change.kind) {
    case 'suspend':
      return api.POST('/api/admin/v1/users/{user_id}/suspend', { params: { path }, body: { reason: change.reason } })
    case 'unsuspend':
      return api.POST('/api/admin/v1/users/{user_id}/unsuspend', { params: { path }, body: { reason: change.reason } })
    case 'revoke-sessions':
      return api.POST('/api/admin/v1/users/{user_id}/revoke-sessions', {
        params: { path },
        body: { reason: change.reason },
      })
    case 'schedule-deletion':
      return api.POST('/api/admin/v1/users/{user_id}/schedule-deletion', {
        params: { path },
        body: { reason: change.reason },
      })
    case 'cancel-deletion':
      return api.POST('/api/admin/v1/users/{user_id}/cancel-deletion', {
        params: { path },
        body: { reason: change.reason },
      })
    case 'set-override':
      return api.PUT('/api/admin/v1/users/{user_id}/plan-override', {
        params: { path },
        body: { plan: change.plan, expires_at: change.expiresAt, reason: change.reason },
      })
    case 'revoke-override':
      return api.POST('/api/admin/v1/users/{user_id}/plan-override/revoke', {
        params: { path },
        body: { reason: change.reason },
      })
  }
}

/** Make a change to one account; the list and the account's detail are read again afterwards. */
export function useChangeUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, change }: { id: string; change: Change }) => unwrap(send(id, change)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  })
}

/** "Oct 4, 2026" from an ISO time, in UTC (an operator reads the same dates the server logs). */
export function utcDay(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(iso),
  )
}

/** "Oct 4, 2026 · 09:41 UTC". */
export function utcMoment(iso: string): string {
  const date = new Date(iso)
  const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' })
  return `${utcDay(iso)} · ${time.format(date)} UTC`
}

/** "1.2 MB" from a byte count. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

/** The words for what the log calls each thing an operator or the system did. Anything else is shown as it is written. */
const ACTIONS: Record<string, string> = {
  admin_viewed_user: 'An operator looked at this account',
  admin_invite_created: 'Invite made',
  admin_invite_revoked: 'Invite revoked',
  admin_grant_code_created: 'Plan code made',
  admin_grant_code_revoked: 'Plan code revoked',
  admin_suspended: 'Suspended',
  admin_unsuspended: 'Restored',
  admin_sessions_revoked: 'Sessions ended',
  admin_deletion_scheduled: 'Deletion scheduled',
  admin_deletion_cancelled: 'Deletion cancelled',
  admin_plan_override_set: 'Plan override set',
  admin_plan_override_revoked: 'Plan override revoked',
  operator_granted: 'Made an operator',
  operator_removed: 'Operator removed',
  signup: 'Signed up',
  login: 'Signed in',
  login_failed: 'A sign-in was refused',
  logout: 'Signed out',
  logout_everywhere: 'Signed out everywhere else',
  session_revoked: 'Signed one place out',
  export: 'Downloaded their data',
  import: 'Imported a file',
  import_undone: 'Undid an import',
  deletion_requested: 'Asked to delete the account',
  deletion_cancelled: 'Cancelled the deletion by signing in',
  grant_redeemed: 'Used a plan code',
  grant_refused: 'A plan code was refused',
  account_deleted: 'Account deleted',
}

/** Every kind of event the page has words for, in alphabetical order of its name, for the log's filter. */
export const ACTION_NAMES: string[] = Object.keys(ACTIONS).sort((a, b) => actionText(a).localeCompare(actionText(b)))

export function actionText(action: string): string {
  return ACTIONS[action] ?? action.replaceAll('_', ' ')
}
