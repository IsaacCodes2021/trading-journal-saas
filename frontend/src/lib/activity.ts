import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { components } from './api/schema'
import { pluralize } from './format'

export type Activity = components['schemas']['ActivityOut']
export type ActivityKind = Activity['kind']

/** The key to refetch under when something the list would show has just happened. */
export const ACTIVITY_KEY = ['activity'] as const

/** What has happened to this account lately, newest first (the server decides what a person may see of the log). */
export function useActivity() {
  return useQuery({
    queryKey: ACTIVITY_KEY,
    queryFn: () => unwrap(api.GET('/api/v1/activity')),
    retry: false,
  })
}

/** One sentence per kind of event. A `Record` over every kind, so a kind the server adds fails the type check here. */
const TEXT: Record<ActivityKind, (event: Activity) => string> = {
  sign_up: () => 'Account created',
  sign_in: (event) => (event.mfa ? 'Signed in with two-step verification' : 'Signed in'),
  sign_in_refused: () => 'A sign-in to your account was refused',
  sign_out: () => 'Signed out',
  other_sign_out: () => 'Signed another place out',
  sign_out_everywhere: ({ ended }) =>
    ended === null || ended === undefined
      ? 'Signed out everywhere else'
      : ended === 0
        ? 'Asked to sign out everywhere else (there was nowhere else)'
        : `Signed out of ${pluralize(ended, 'other place')}`,
  export: () => 'Downloaded your data',
  deletion_requested: () => 'Asked to delete the account',
  deletion_cancelled: () => 'Signing in cancelled the account deletion',
}

export function activityText(event: Activity): string {
  return TEXT[event.kind](event)
}
