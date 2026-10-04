import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { components } from './api/schema'

export type ActiveSessions = components['schemas']['ActiveSessionsOut']
export type ActiveSession = components['schemas']['ActiveSessionOut']

const KEY = ['sessions'] as const

/** Where the account is signed in now (the server's answer is never cached for long: it changes as people sign in). */
export function useActiveSessions() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => unwrap(api.GET('/api/v1/sessions')),
    retry: false,
  })
}

/** Sign one other place out. The answer is the list as it is afterwards, which replaces what was shown. */
export function useEndSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE('/api/v1/sessions/{session_id}', { params: { path: { session_id: id } } })),
    onSuccess: (list) => queryClient.setQueryData<ActiveSessions>(KEY, list),
  })
}

/** Sign out everywhere else: every other place ends, this one stays. */
export function useEndOtherSessions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/v1/sessions')),
    onSuccess: (list) => queryClient.setQueryData<ActiveSessions>(KEY, list),
  })
}
