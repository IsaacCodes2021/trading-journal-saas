import { useState } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { cx } from '../../components/ui/cx'
import { Button, Card, Pnl } from '../../components/ui/primitives'
import { FILLS_DISCLAIMER } from '../../lib/copyTrading'
import type { AccountRef, CopyGroup, CopyGroupMember } from '../../lib/data/model'
import { useAccounts, useChangeCopyFollower, useDeleteCopyGroup, useRenameCopyGroup } from '../../lib/data/queries'
import { formatDay } from '../../lib/format'

/*
 * The Accounts tab's Groups view: each copy-trading group with its leader and followers, each member's net P&L (a
 * follower's is derived from the leader's trades: an estimate) and the group's total. A follower in more than one
 * group is highlighted. Membership changes are dated today or later, never edited (decisions 9 and 22 in
 * docs/new-feature-copy-trading/decisions.csv).
 */

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The change was refused.'
}

/** The other groups of an account that is in more than one: on hover (title) and on click. */
function OtherGroups({ member }: { member: CopyGroupMember }) {
  const [open, setOpen] = useState(false)
  if (member.other_groups.length === 0) return null
  const names = member.other_groups.map((g) => g.name).join(', ')
  return (
    <span className="ml-2 inline-flex flex-col">
      <button
        type="button"
        className="cursor-pointer rounded border border-line bg-transparent px-1.5 text-xs text-muted"
        title={`Also in ${names}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {member.other_groups.length + 1} groups
      </button>
      {open ? <span className="mt-1 text-xs text-muted">Also in {names}</span> : null}
    </span>
  )
}

type Change = { kind: 'add' } | { kind: 'remove'; account: AccountRef } | { kind: 'rename' } | { kind: 'delete' }

function ChangeDialog({ group, change, onClose }: { group: CopyGroup; change: Change; onClose: () => void }) {
  const accounts = useAccounts()
  const changeFollower = useChangeCopyFollower()
  const rename = useRenameCopyGroup()
  const remove = useDeleteCopyGroup()
  const [accountId, setAccountId] = useState('')
  const [day, setDay] = useState('')
  const [note, setNote] = useState('')
  const [name, setName] = useState(group.name)
  const busy = changeFollower.isPending || rename.isPending || remove.isPending
  const error = changeFollower.error ?? rename.error ?? remove.error
  const inGroup = new Set(group.members.filter((m) => m.active).map((m) => m.account.id))
  const candidates = (accounts.data ?? []).filter((a) => !inGroup.has(a.id))

  const submit = () => {
    if (change.kind === 'rename') rename.mutate({ id: group.id, name }, { onSuccess: onClose })
    else if (change.kind === 'delete') remove.mutate(group.id, { onSuccess: onClose })
    else {
      const id = change.kind === 'add' ? Number(accountId) : change.account.id
      changeFollower.mutate(
        { id: group.id, kind: change.kind, change: { account_id: id, effective_date: day || null, note } },
        { onSuccess: onClose },
      )
    }
  }

  const title =
    change.kind === 'add'
      ? `Add a follower to ${group.name}`
      : change.kind === 'remove'
        ? `Remove ${change.account.name} from ${group.name}`
        : change.kind === 'rename'
          ? `Rename ${group.name}`
          : `End ${group.name}`

  return (
    <Modal label={title} onClose={() => (busy ? undefined : onClose())} className="w-[min(480px,calc(100vw-32px))]">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <h2 className="m-0 text-base font-semibold">{title}</h2>
        {change.kind === 'add' ? (
          <Field id="follower" label="Account">
            <select
              id="follower"
              className={CONTROL}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              data-autofocus
            >
              <option value="">Choose an account</option>
              {candidates.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {change.kind === 'add' || change.kind === 'remove' ? (
          <>
            <Field
              id="effective"
              label="Takes effect on"
              hint={
                change.kind === 'add'
                  ? 'The first trading day it copies. Empty means today. An earlier day is refused: past trades keep the followers they had.'
                  : 'The first trading day it no longer copies. Empty means today. Its earlier copies stay counted.'
              }
            >
              <input
                id="effective"
                type="date"
                className={CONTROL}
                value={day}
                onChange={(e) => setDay(e.target.value)}
              />
            </Field>
            <Field id="note" label="Note (optional)" hint="Why, for your own records.">
              <input
                id="note"
                className={CONTROL}
                value={note}
                maxLength={500}
                onChange={(e) => setNote(e.target.value)}
              />
            </Field>
          </>
        ) : null}
        {change.kind === 'rename' ? (
          <Field id="group-name" label="Group name">
            <input
              id="group-name"
              className={CONTROL}
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              data-autofocus
            />
          </Field>
        ) : null}
        {change.kind === 'delete' ? (
          <p className="m-0 text-[13px]">
            The group and its membership history are removed. Every trade stays on {group.leader.name}; the followers
            stop getting copies, including past ones, so their totals drop to what was imported into them.
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {errorText(error)}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant={change.kind === 'delete' ? 'default' : 'primary'}
            disabled={busy || (change.kind === 'add' && accountId === '') || (change.kind === 'rename' && !name.trim())}
          >
            {change.kind === 'add'
              ? 'Add follower'
              : change.kind === 'remove'
                ? 'Remove follower'
                : change.kind === 'rename'
                  ? 'Rename'
                  : 'End the group'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function History({ group }: { group: CopyGroup }) {
  const names = new Map(group.members.map((m) => [m.account.id, m.account.name]))
  return (
    <details className="mt-3 text-[13px]">
      <summary className="cursor-pointer text-muted">Membership history</summary>
      <ul className="m-0 mt-2 flex list-none flex-col gap-1 p-0">
        {group.events.map((e) => (
          <li key={e.id}>
            {names.get(e.account_id) ?? `Account ${e.account_id}`} {e.event_type}{' '}
            {e.effective_date ? `from ${formatDay(e.effective_date)}` : 'from the start'}
            {e.note ? <span className="text-muted"> &middot; {e.note}</span> : null}
          </li>
        ))}
      </ul>
    </details>
  )
}

function GroupCard({ group }: { group: CopyGroup }) {
  const [change, setChange] = useState<Change | null>(null)
  const cell = 'border-b border-line-soft px-3 py-2 text-left'
  return (
    <Card className="px-5 py-4" role="region" aria-label={`Group ${group.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="m-0 text-base font-semibold">{group.name}</h2>
        <span className="text-[13px] text-muted">led by {group.leader.name}</span>
        <span className="grow" />
        <Button onClick={() => setChange({ kind: 'add' })}>Add follower</Button>
        <Button onClick={() => setChange({ kind: 'rename' })}>Rename</Button>
        <Button onClick={() => setChange({ kind: 'delete' })}>End group</Button>
      </div>
      {group.leader_archived ? (
        <p role="status" className="m-0 mt-3 rounded-md border border-warn-line px-3 py-2 text-[13px] text-warn">
          The leader, {group.leader.name}, is archived. Its trades still count for the followers, but a file imported
          from now on has nowhere to go: restore the leader in settings, or end this group.
        </p>
      ) : null}
      <div className="scroll-x mt-3">
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr className="text-xs text-muted">
              <th className={cell}>Group</th>
              <th className={cell}>Account</th>
              <th className={cell}>Role</th>
              <th className={cx(cell, 'text-right')}>Trades</th>
              <th className={cx(cell, 'text-right')}>Account P&amp;L</th>
              <th className={cell}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {group.members.map((member, at) => (
              <tr
                key={member.account.id}
                className={cx(member.other_groups.length > 0 && 'bg-multi-group-bg', !member.active && 'text-muted')}
                data-multi-group={member.other_groups.length > 0 || undefined}
              >
                <td className={cell}>{at === 0 ? group.name : null}</td>
                <td className={cell}>
                  {member.account.name}
                  {member.archived ? <span className="ml-2 text-xs text-muted">archived</span> : null}
                  <OtherGroups member={member} />
                </td>
                <td className={cell}>{member.role === 'leader' ? 'Leader' : member.active ? 'Follower' : 'Removed'}</td>
                <td className={cx(cell, 'num text-right')}>{member.trade_count}</td>
                <td className={cx(cell, 'text-right')}>
                  <Pnl value={member.net_pnl} />
                  {member.role === 'follower' ? <span className="ml-1 text-xs text-muted">est.</span> : null}
                </td>
                <td className={cx(cell, 'text-right')}>
                  {member.role === 'follower' && member.active ? (
                    <Button onClick={() => setChange({ kind: 'remove', account: member.account })}>Remove</Button>
                  ) : null}
                </td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td className={cell} colSpan={4}>
                Cumulative group P&amp;L
              </td>
              <td className={cx(cell, 'text-right')}>
                <Pnl value={group.net_pnl} />
              </td>
              <td className={cell} />
            </tr>
          </tbody>
        </table>
      </div>
      <History group={group} />
      {change ? <ChangeDialog group={group} change={change} onClose={() => setChange(null)} /> : null}
    </Card>
  )
}

export function CopyGroupsView({ groups }: { groups: CopyGroup[] }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-[13px] text-muted">
        Trades are imported into each group&rsquo;s leader only; a follower&rsquo;s P&amp;L is the leader&rsquo;s on the
        days it was in the group. {FILLS_DISCLAIMER} An account highlighted in yellow is in more than one group.
      </p>
      {groups.map((group) => (
        <GroupCard key={group.id} group={group} />
      ))}
    </div>
  )
}
