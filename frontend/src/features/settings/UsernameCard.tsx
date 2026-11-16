import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card } from '../../components/ui/primitives'
import { USERNAME_HINT } from '../../components/UsernamePrompt'
import { USERNAME_MAX, usernameProblem, usernameSaveError, useSaveUsername } from '../../lib/account'

/** Pick a username, or change it: any time, as often as the person likes. Someone else's name is refused by the server. */
export function UsernameCard({ saved }: { saved: string | null }) {
  const save = useSaveUsername()
  const [name, setName] = useState(saved ?? '')
  const [tried, setTried] = useState(false)
  const [justSaved, setJustSaved] = useState(false)
  const dirty = name.trim() !== (saved ?? '')
  const shape = usernameProblem(name)
  const error = (tried ? shape : null) ?? usernameSaveError(save.error)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty) return
    setTried(true)
    setJustSaved(false)
    if (shape) return
    save.mutate(name, {
      onSuccess: ({ username }) => {
        setName(username) // as saved: spaces around it dropped
        setJustSaved(true)
      },
    })
  }

  return (
    <Card className="flex flex-col gap-4 px-5 py-4" role="region" aria-label="Username">
      <h2 className="m-0 text-base font-semibold">Username</h2>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          id="username"
          label="Your username"
          hint={saved ? `${USERNAME_HINT} Change it any time.` : `You have not picked one yet. ${USERNAME_HINT}`}
          error={error ?? undefined}
        >
          <input
            id="username"
            value={name}
            placeholder="e.g. trader_joe"
            onChange={(e) => {
              setName(e.target.value)
              setTried(false)
              setJustSaved(false)
              save.reset()
            }}
            maxLength={USERNAME_MAX}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            className={`${CONTROL} max-w-[360px]`}
          />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save username'}
          </Button>
          {justSaved && !dirty ? (
            <span role="status" className="text-[13px] text-pos">
              Saved
            </span>
          ) : null}
        </div>
      </form>
    </Card>
  )
}
