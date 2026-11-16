import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, unwrap } from './api/client'
import type { components } from './api/schema'

type Session = components['schemas']['SessionOut']

export const USERNAME_MIN = 3
export const USERNAME_MAX = 20

/**
 * What is wrong with a username as typed, in the words the page shows, or null when it can be sent. The same rules as
 * the server's (3 to 20 letters, numbers or underscores, spaces around it dropped); whether someone else holds it only the
 * server can say.
 */
export function usernameProblem(typed: string): string | null {
  const name = typed.trim()
  if (name.length < USERNAME_MIN) return `Use at least ${USERNAME_MIN} characters.`
  if (name.length > USERNAME_MAX) return `Use at most ${USERNAME_MAX} characters.`
  if (!/^[A-Za-z0-9_]+$/.test(name)) return 'Use only letters, numbers and underscores.'
  return null
}

/** Why saving a username failed, as a sentence for under the box: taken, the wrong shape, or the connection. */
export function usernameSaveError(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError) return error.fields['username'] ?? error.message
  return 'Could not save. Check your connection and try again.'
}

/** Pick a username or change it. The saved name goes straight into the session, and the prompt is not shown again. */
export function useSaveUsername() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (typed: string) => unwrap(api.PUT('/api/v1/account/username', { body: { username: typed.trim() } })),
    onSuccess: ({ username }) =>
      queryClient.setQueryData<Session>(['session'], (old) =>
        old ? { ...old, user: { ...old.user, username }, flags: { ...old.flags, ask_username: false } } : old,
      ),
  })
}

/** "Not now" to the username prompt: the server remembers, so it is not shown again on any device. */
export function useSkipUsername() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/account/username/skip')),
    onSuccess: ({ ask_username }) =>
      queryClient.setQueryData<Session>(['session'], (old) =>
        old ? { ...old, flags: { ...old.flags, ask_username } } : old,
      ),
  })
}

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
