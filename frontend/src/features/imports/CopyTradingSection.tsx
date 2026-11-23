import { Link } from '@tanstack/react-router'
import { useId, useState } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card } from '../../components/ui/primitives'
import { FILLS_DISCLAIMER } from '../../lib/copyTrading'
import type { AccountRef, CopyGroup } from '../../lib/data/model'
import { useCopyGroups, useCreateCopyGroup } from '../../lib/data/queries'

/*
 * The import form's copy-trading part (docs/new-feature-copy-trading/decisions.csv). Trades are imported into a group's
 * leader only; the followers' numbers are worked out from them. Here the account being imported into can be made the
 * leader of a new group: followers, an optional first day, the fills acknowledgment and a name. Saving the group
 * writes no trade; the import then goes ahead as usual, into the leader.
 */

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The group could not be saved.'
}

function names(group: CopyGroup, accounts: (m: CopyGroup['members'][number]) => boolean): string {
  return group.members
    .filter(accounts)
    .map((m) => m.account.name)
    .join(', ')
}

function NewGroupForm({ account, accounts }: { account: AccountRef; accounts: AccountRef[] }) {
  const create = useCreateCopyGroup()
  const [followers, setFollowers] = useState<number[]>([])
  const [since, setSince] = useState('')
  const [name, setName] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const choices = accounts.filter((a) => a.id !== account.id && !followers.includes(a.id))
  const chosen = followers.map((id) => accounts.find((a) => a.id === id)).filter((a): a is AccountRef => !!a)
  const ready = followers.length > 0 && name.trim() !== '' && acknowledged

  return (
    <form
      className="mt-3 flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ready) return
        create.mutate({
          name: name.trim(),
          leader_account_id: account.id,
          follower_account_ids: followers,
          copying_since: since || null,
          acknowledge_fills: true,
        })
      }}
    >
      <Field id="copy-followers" label="Follower accounts" hint="The accounts the copier repeats these trades on.">
        <select
          id="copy-followers"
          className={CONTROL}
          value=""
          onChange={(e) => {
            const id = Number(e.target.value)
            if (id) setFollowers((list) => [...list, id])
          }}
        >
          <option value="">{choices.length ? 'Add an account' : 'No other accounts to add'}</option>
          {choices.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </Field>
      {chosen.length > 0 ? (
        <ul className="m-0 -mt-2 flex list-none flex-wrap gap-1.5 p-0" aria-label="Chosen followers">
          {chosen.map((a) => (
            <li
              key={a.id}
              className="inline-flex h-7 items-center gap-1 rounded-full border border-line bg-raised pr-1 pl-2.5 text-[13px]"
            >
              {a.name}
              <button
                type="button"
                aria-label={`Remove ${a.name}`}
                className="cursor-pointer rounded-full border-0 bg-transparent px-1.5 text-muted hover:text-ink"
                onClick={() => setFollowers((list) => list.filter((id) => id !== a.id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <Field
        id="copy-since"
        label="Copying since (optional)"
        hint={`The first trading day the followers copied ${account.name}. Empty: every trade of ${account.name} counts for them.`}
      >
        <input
          id="copy-since"
          type="date"
          className={CONTROL}
          value={since}
          onChange={(e) => setSince(e.target.value)}
        />
      </Field>
      <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-warn-line px-3 py-2.5 text-[13px]">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        <span>I understand. {FILLS_DISCLAIMER}</span>
      </label>
      <Field id="copy-name" label="Group name" hint="Whatever you call this copy setup, like “Apex 50K x5”.">
        <input
          id="copy-name"
          className={CONTROL}
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      {create.isError ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {errorText(create.error)}
        </p>
      ) : null}
      <div>
        <Button type="submit" variant="primary" disabled={!ready || create.isPending}>
          Save the group
        </Button>
      </div>
    </form>
  )
}

export function CopyTradingSection({ account, accounts }: { account: AccountRef | undefined; accounts: AccountRef[] }) {
  const groups = useCopyGroups()
  const [leader, setLeader] = useState(false)
  const question = useId()
  if (!account || !groups.data) return null

  const led = groups.data.find((g) => g.leader.id === account.id)
  if (led) {
    return (
      <Card className="px-5 py-4" role="region" aria-label="Copy trading">
        <p className="m-0 text-[13px]">
          <span className="font-semibold">
            {account.name} leads {led.name}.
          </span>{' '}
          Its trades also count for {names(led, (m) => m.role === 'follower' && m.active) || 'no follower right now'}.
          Import into {account.name} only.{' '}
          <Link to="/accounts" search={{}}>
            Manage the group
          </Link>
        </p>
      </Card>
    )
  }

  const following = groups.data.filter((g) =>
    g.members.some((m) => m.role === 'follower' && m.active && m.account.id === account.id),
  )
  if (following.length > 0) {
    return (
      <Card className="px-5 py-4" role="region" aria-label="Copy trading">
        <p role="status" className="m-0 text-[13px] text-warn">
          {account.name} follows {following.map((g) => `${g.name} (led by ${g.leader.name})`).join(' and ')}: its trades
          already come from the leader. Importing it too would count them twice. Import the leader instead, unless these
          are trades the copier did not repeat.
        </p>
      </Card>
    )
  }

  return (
    <Card className="px-5 py-4" role="region" aria-label="Copy trading">
      <fieldset className="m-0 border-0 p-0">
        <legend id={question} className="text-[13px] font-medium">
          Copy trading: is {account.name} the leader of a copy-trading group?
        </legend>
        <div className="mt-2 flex gap-4 text-[13px]" role="radiogroup" aria-labelledby={question}>
          <label className="flex cursor-pointer items-center gap-2">
            <input type="radio" name="copy-leader" checked={!leader} onChange={() => setLeader(false)} />
            No
          </label>
          <label className="flex cursor-pointer items-center gap-2">
            <input type="radio" name="copy-leader" checked={leader} onChange={() => setLeader(true)} />
            Yes
          </label>
        </div>
      </fieldset>
      {leader ? <NewGroupForm key={account.id} account={account} accounts={accounts} /> : null}
    </Card>
  )
}
