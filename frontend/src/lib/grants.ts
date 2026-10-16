import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './api/client'

/**
 * Use a plan code. The server tidies it (case, spaces) and answers one sentence for every code that does not work, so the
 * page shows that sentence and nothing more. On success the session (and billing, when it is on) is read again, which
 * carries the new plan everywhere.
 */
export function useRedeemCode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => unwrap(api.POST('/api/v1/grants/redeem', { body: { code } })),
    onSuccess: () =>
      Promise.all(['session', 'billing'].map((key) => queryClient.invalidateQueries({ queryKey: [key] }))),
  })
}
