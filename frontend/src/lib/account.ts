import { useMutation } from '@tanstack/react-query'
import { api, unwrap } from './api/client'

/**
 * Ask for the account to be deleted: the server schedules it a week away, ends every session, and answers with the date.
 * It needs the person to have signed in again within the last ten minutes (`flags.recently_authenticated`) and to type their
 * own email address; the server checks both, so a refusal arrives as a sentence to show.
 */
export function useScheduleDeletion() {
  return useMutation({
    mutationFn: (email: string) => unwrap(api.POST('/api/v1/account/deletion', { body: { email } })),
  })
}
