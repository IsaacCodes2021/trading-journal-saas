import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button, Card } from '../../components/ui/primitives'
import { useScheduleDeletion } from '../../lib/account'
import { go, reauthUrl } from '../../lib/auth'
import { EXPORT_URL } from '../../lib/exportData'
import { useSession } from '../../lib/session'
import { messageOf } from '../tags/messages'

/** Where signing in again brings the person back to, with the dialog already open. */
export const DELETE_RETURN = '/settings/privacy?delete=1'

/** A button that ends or deletes something: red, so it is not pressed by habit. */
export const DANGER = 'border-neg bg-neg! font-semibold text-accent-on! hover:opacity-90 disabled:hover:opacity-50'

const sameEmail = (typed: string, email: string) => typed.trim().toLowerCase() === email.trim().toLowerCase()

function Dialog({ email, recent, onClose }: { email: string; recent: boolean; onClose: () => void }) {
  const schedule = useScheduleDeletion()
  const [typed, setTyped] = useState('')
  const ready = sameEmail(typed, email)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!ready || schedule.isPending) return
    // The session is gone once this succeeds, so the next page is a fresh load of one that needs no session.
    // What was typed goes to the server, which compares it with the account's own address: the check is not only ours.
    schedule.mutate(typed.trim(), {
      onSuccess: (done) => go.to(`/invite?deleted=${encodeURIComponent(done.scheduled_for)}`),
    })
  }

  return (
    <Modal
      label="Delete your account"
      onClose={schedule.isPending ? () => undefined : onClose}
      className="max-w-[520px]"
    >
      <div className="flex flex-col gap-3.5">
        <h2 className="m-0 text-base font-semibold">Delete your account?</h2>
        <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5">
          <li>
            Your account and everything in it (accounts, trades, notes, tags, screenshots and import history) is{' '}
            <strong>permanently deleted 7 days from now</strong>.
          </li>
          <li>You are signed out everywhere, straight away.</li>
          <li>
            If you change your mind, <strong>sign in again before then</strong> and nothing is lost.
          </li>
          <li>
            <a href={EXPORT_URL} download>
              Download your data
            </a>{' '}
            first if you want a copy: it is not available once the account is deleted.
          </li>
        </ul>

        {recent ? (
          <form onSubmit={submit} className="flex flex-col gap-3.5">
            <Field id="delete-confirm" label="Type your email address to confirm" hint={email}>
              <input
                id="delete-confirm"
                type="email"
                autoComplete="off"
                spellCheck={false}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                data-autofocus
                className={CONTROL}
              />
            </Field>
            {schedule.isError ? (
              <p role="alert" className="m-0 text-[13px] text-neg">
                {messageOf(schedule.error)}
              </p>
            ) : null}
            <div className="flex flex-wrap justify-end gap-2">
              <Button onClick={onClose} disabled={schedule.isPending}>
                Cancel
              </Button>
              <Button type="submit" className={DANGER} disabled={!ready || schedule.isPending}>
                {schedule.isPending ? 'Deleting…' : 'Delete my account'}
              </Button>
            </div>
          </form>
        ) : (
          <>
            <p className="m-0 rounded-lg border border-warn-line px-3 py-2 text-[13px] text-warn" role="note">
              For your safety we ask you to sign in again first, so we know it is you. You will come straight back here.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button onClick={onClose}>Cancel</Button>
              <Button variant="primary" onClick={() => go.to(reauthUrl(DELETE_RETURN))} data-autofocus>
                Sign in again
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}

/**
 * The "Delete your account" card, and the dialog it opens. Deleting is deliberate: the person must have signed in again a
 * moment ago (the server checks, and the dialog sends them through sign-in when they have not), and type their own email
 * address. `startOpen` is for coming back from that sign-in.
 */
export function DeleteAccount({ startOpen, onClosed }: { startOpen: boolean; onClosed: () => void }) {
  const session = useSession()
  const [open, setOpen] = useState(startOpen)
  const close = () => {
    setOpen(false)
    onClosed()
  }
  return (
    <Card className="flex flex-col gap-2 px-5 py-4" role="region" aria-label="Delete your account">
      <h2 className="m-0 text-base font-semibold">Delete your account</h2>
      <p className="m-0 text-muted">
        Permanently deletes your account and everything in it, a week after you ask. Download your data first if you
        want a copy. If you change your mind within the week, signing in again cancels it.
      </p>
      <div>
        <Button className="border-neg text-neg" onClick={() => setOpen(true)} disabled={!session.data}>
          Delete my account…
        </Button>
      </div>
      {open && session.data ? (
        <Dialog email={session.data.user.email} recent={session.data.flags.recently_authenticated} onClose={close} />
      ) : null}
    </Card>
  )
}
