import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, setCsrfToken, unwrap } from './api/client'
import type { components } from './api/schema'
import type { Theme } from './theme'

type Session = components['schemas']['SessionOut']
export type SettingsPatch = components['schemas']['SettingsPatch']

/**
 * Save changes to the account's settings (time zones, the trading day, the theme). The saved settings go straight into
 * the session. Moving the trading day moves trades between days, so what is built from trades is read again.
 */
export function useSaveSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (changes: SettingsPatch) => unwrap(api.PATCH('/api/v1/settings', { body: changes })),
    onSuccess: async (settings) => {
      queryClient.setQueryData<Session>(['session'], (old) => (old ? { ...old, settings } : old))
      await Promise.all(
        ['trades', 'trade', 'dashboard'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
      )
    },
  })
}

/** Save the theme choice to the account, so it follows the user to another browser. */
export function useSaveTheme() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (theme: Theme) => unwrap(api.PATCH('/api/v1/settings', { body: { theme } })),
    onSuccess: (settings) =>
      queryClient.setQueryData<Session>(['session'], (old) => (old ? { ...old, settings } : old)),
  })
}

export function useSession() {
  return useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      const session = await unwrap(api.GET('/api/v1/session'))
      setCsrfToken(session.csrf_token)
      return session
    },
    staleTime: 60_000,
    retry: false,
  })
}
