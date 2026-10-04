import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/primitives'
import { useChangeUser } from '../../lib/admin'
import { messageOf } from '../tags/messages'

/** The changes that need nothing but a reason. */
export type ReasonOnly = 'suspend' | 'unsuspend' | 'revoke-sessions' | 'schedule-deletion' | 'cancel-deletion'

/** What such a change is called, what it does, and whether it is the kind that is hard to undo. */
export interface Ask {
  kind: ReasonOnly
  title: string
  body: string
  confirm: string
  danger?: boolean
}

export const ASKS: Record<ReasonOnly, Ask> = {
  suspend: {
    kind: 'suspend',
    title: 'Suspend this account?',
    body: 'They are signed out everywhere now and cannot sign in until you restore them. Their data is untouched.',
    confirm: 'Suspend',
    danger: true,
  },
  unsuspend: {
    kind: 'unsuspend',
    title: 'Restore this account?',
    body: 'They can sign in again.',
    confirm: 'Restore',
  },
  'revoke-sessions': {
    kind: 'revoke-sessions',
    title: 'End all their sessions?',
    body: 'They are signed out everywhere now. They can sign in again straight away.',
    confirm: 'End sessions',
  },
  'schedule-deletion': {
    kind: 'schedule-deletion',
    title: 'Schedule this account for deletion?',
    body: 'It is deleted for good a week from now, with everything in it, and they are signed out now. They can stop it by signing in before then.',
    confirm: 'Schedule deletion',
    danger: true,
  },
  'cancel-deletion': {
    kind: 'cancel-deletion',
    title: 'Cancel the deletion?',
    body: 'The account goes back to active and nothing is deleted.',
    confirm: 'Cancel the deletion',
  },
}

const DANGER = 'border-neg bg-neg! font-semibold text-accent-on! hover:opacity-90 disabled:hover:opacity-50'

/** Ask why, then make the change. The reason is required and kept in the audit log with who made the change. */
export function ReasonDialog({
  ask,
  userId,
  email,
  onClose,
}: {
  ask: Ask
  userId: string
  email: string
  onClose: () => void
}) {
  const change = useChangeUser()
  const [reason, setReason] = useState('')
  const typed = reason.trim()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!typed || change.isPending) return
    change.mutate({ id: userId, change: { kind: ask.kind, reason: typed } }, { onSuccess: onClose })
  }

  return (
    <Modal label={ask.title} onClose={change.isPending ? () => undefined : onClose} className="max-w-[480px]">
      <form onSubmit={submit} className="flex flex-col gap-3.5">
        <h2 className="m-0 text-base font-semibold">{ask.title}</h2>
        <p className="m-0 text-muted">
          <strong className="text-ink">{email}</strong>. {ask.body}
        </p>
        <Field id="reason" label="Why? (kept in the audit log)">
          <textarea
            id="reason"
            rows={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-autofocus
            className={`${CONTROL} h-auto py-2`}
          />
        </Field>
        {change.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {messageOf(change.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button onClick={onClose} disabled={change.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant={ask.danger ? 'default' : 'primary'}
            className={ask.danger ? DANGER : undefined}
            disabled={!typed || change.isPending}
          >
            {change.isPending ? 'Working…' : ask.confirm}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
