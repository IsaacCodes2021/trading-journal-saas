import { useQuery } from '@tanstack/react-query'
import { api, setCsrfToken, unwrap } from './api/client'

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
