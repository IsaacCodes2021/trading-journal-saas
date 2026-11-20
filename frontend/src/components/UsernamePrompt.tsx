import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { USERNAME_MAX, usernameProblem, usernameSaveError, useSaveUsername, useSkipUsername } from '../lib/account'
import { CONTROL, Field } from './ui/Field'
import { Modal } from './ui/Modal'
import { Button } from './ui/primitives'

export const USERNAME_HINT = '3 to 20 letters, numbers or underscores.'

// The box put away without an answer (Escape, a click outside) during this visit. It is kept with the app's other state,
// never garbage-collected, so it lasts until the app is next opened, holds wherever the box is drawn, and lets the Share
// buttons tell that the box is no longer showing.
const PUT_AWAY = ['username-prompt-put-away']

export function usernamePromptPutAway(queryClient: QueryClient): boolean {
  return queryClient.getQueryData<boolean>(PUT_AWAY) === true
}

function putAway(queryClient: QueryClient) {
  queryClient.setQueryDefaults(PUT_AWAY, { gcTime: Infinity })
  queryClient.setQueryData(PUT_AWAY, true)
}

/**
 * Asked after sign-in of anyone with no username who has not said "not now" (`flags.ask_username`): accounts made before
 * usernames existed, and new ones. "Not now" is remembered by the server, so it is not asked again on any device; closing
 * the box (Escape, a click outside) only puts it away until the app is next opened. Settings, Preferences picks or changes
 * the name at any time.
 */
export function UsernamePrompt() {
  const save = useSaveUsername()
  const skip = useSkipUsername()
  const [name, setName] = useState('')
  const [tried, setTried] = useState(false)
  const queryClient = useQueryClient()
  const [closed, setClosed] = useState(() => usernamePromptPutAway(queryClient))
  if (closed) return null

  const shape = usernameProblem(name)
  const busy = save.isPending || skip.isPending
  const error =
    (tried ? shape : null) ??
    usernameSaveError(save.error) ??
    (skip.isError ? 'Could not save that. Check your connection and try again.' : null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setTried(true)
    if (!shape) save.mutate(name)
  }

  return (
    <Modal
      label="Pick a username"
      onClose={
        busy
          ? () => undefined
          : () => {
              putAway(queryClient)
              setClosed(true)
            }
      }
      className="max-w-[440px]"
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-3.5">
        <h2 className="m-0 text-base font-semibold">Pick a username</h2>
        <p className="m-0 text-muted">
          Choose the name you go by in Roundturn. No one else can have it, and you can change it any time in Settings.
        </p>
        <Field id="username-new" label="Username" hint={USERNAME_HINT} error={error ?? undefined}>
          <input
            id="username-new"
            data-autofocus
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              save.reset()
            }}
            maxLength={USERNAME_MAX}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            className={CONTROL}
          />
        </Field>
        <div className="flex flex-wrap justify-end gap-2">
          <Button onClick={() => skip.mutate()} disabled={busy}>
            {skip.isPending ? 'One moment…' : 'Not now'}
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {save.isPending ? 'Saving…' : 'Save username'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
