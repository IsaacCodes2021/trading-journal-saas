import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, setCsrfToken, unwrap } from './api/client'
import type { components } from './api/schema'
import type { Theme } from './theme'

type Session = components['schemas']['SessionOut']

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
