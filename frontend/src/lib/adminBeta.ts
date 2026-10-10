import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { components } from './api/schema'
import { utcDay } from './admin'

export type BetaPerson = components['schemas']['BetaPersonOut']
export type BetaState = BetaPerson['state']
export type BetaRevoked = components['schemas']['BetaRevokeOut']
export type BetaRemoved = components['schemas']['BetaRemovedOut']

export const BETA_PAGE = 25

/** The states in the order the filter shows them. */
export const BETA_STATES: readonly BetaState[] = ['waiting', 'active', 'ended', 'revoked']

export interface BetaQuery {
  /** Part of a name, an address or a prop firm. */
  q: string
  state: BetaState | ''
  /** 1-based. */
  page: number
}

const KEY = ['admin-beta'] as const

/** One page of the people who joined the beta, newest first; the last page stays on screen while the next loads. */
export function useBeta(query: BetaQuery) {
  return useQuery({
    queryKey: [...KEY, query],
    queryFn: () =>
      unwrap(
        api.GET('/api/admin/v1/beta', {
          params: {
            query: {
              ...(query.q ? { q: query.q } : {}),
              ...(query.state ? { state: query.state } : {}),
              limit: BETA_PAGE,
              offset: (query.page - 1) * BETA_PAGE,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/**
 * End a person's beta access now (they are emailed), or, for someone with no account yet, withdraw their code. The reason
 * goes to the audit log and is never sent. The overview, the audit log and the account list are read again afterwards.
 */
export function useRevokeBeta() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      unwrap(
        api.POST('/api/admin/v1/beta/{signup_id}/revoke', {
          params: { path: { signup_id: id } },
          body: { reason },
        }),
      ),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: KEY }),
        queryClient.invalidateQueries({ queryKey: ['admin-codes'] }),
        queryClient.invalidateQueries({ queryKey: ['admin'] }),
      ]),
  })
}

/**
 * Remove a sign-up that has no account, and every code tied to its address (a removal request by email). The reason goes
 * to the audit log. Someone with an account is refused by the server: deleting the account does it.
 */
export function useRemoveBeta() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      unwrap(
        api.POST('/api/admin/v1/beta/{signup_id}/remove', {
          params: { path: { signup_id: id } },
          body: { reason },
        }),
      ),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: KEY }),
        queryClient.invalidateQueries({ queryKey: ['admin-codes'] }),
        queryClient.invalidateQueries({ queryKey: ['admin'] }),
      ]),
  })
}

export const STATE_TEXT: Record<BetaState, string> = {
  waiting: 'Waiting',
  active: 'Active',
  ended: 'Ended',
  revoked: 'Revoked',
}

export const CODE_TEXT: Record<BetaPerson['code'], string> = {
  unused: 'Not used yet',
  used: 'Used',
  expired: 'Expired',
  withdrawn: 'Withdrawn',
}

const DAY_MS = 86_400_000

/** Whole days until the 30 days end, counting a part of a day as a day; never below 1 while the access still runs. */
export function daysLeft(endsAt: string, now: number): number {
  return Math.max(1, Math.ceil((new Date(endsAt).getTime() - now) / DAY_MS))
}

/** Where a person stands, in a few words for the table: "Active · 23 days left", "Waiting · code unused", and so on. */
export function accessText(person: BetaPerson, now: number): string {
  switch (person.state) {
    case 'waiting':
      return `Waiting · code ${person.code === 'unused' ? 'unused' : person.code}`
    case 'active': {
      if (!person.access_ends_at) return 'Active'
      const days = daysLeft(person.access_ends_at, now)
      return `Active · ${days} ${days === 1 ? 'day' : 'days'} left`
    }
    case 'ended':
      return person.access_ends_at ? `Ended · ${utcDay(person.access_ends_at)}` : 'Ended'
    case 'revoked':
      return person.revoked_at ? `Revoked · ${utcDay(person.revoked_at)}` : 'Revoked'
  }
}

/** Only someone with no account can be removed: for anyone else the account is what to delete. */
export function canRemove(person: BetaPerson): boolean {
  return person.state === 'waiting'
}

/** What revoking does to this person, or null when there is nothing left to end. */
export function revokeKind(person: BetaPerson): 'access' | 'code' | null {
  if (person.state === 'active') return 'access'
  if (person.state === 'waiting' && person.code === 'unused') return 'code'
  return null
}
