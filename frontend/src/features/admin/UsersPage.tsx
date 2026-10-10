import { CONTROL } from '../../components/ui/Field'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { PAGE_SIZE, useAdminUsers, utcDay, type UserQuery, type UserStatus } from '../../lib/admin'
import { messageOf } from '../tags/messages'
import { PlanCell, StatusBadge } from './parts'
import { SearchBox } from './SearchBox'
import { UserPanel } from './UserPanel'

const STATUSES: { value: UserStatus | ''; label: string }[] = [
  { value: '', label: 'Status: all' },
  { value: 'active', label: 'Active' },
  { value: 'suspended', label: 'Suspended' },
  { value: 'pending_deletion', label: 'Pending deletion' },
]

/** The operator's account list, with one account open beside it. State is in the address, so a view can be shared. */
export function UsersPage({
  query,
  selected,
  onQuery,
  onSelect,
}: {
  query: UserQuery
  selected: string | null
  onQuery: (next: UserQuery) => void
  onSelect: (id: string | null) => void
}) {
  const users = useAdminUsers(query)
  const total = users.data?.total ?? 0
  const first = total === 0 ? 0 : (query.page - 1) * PAGE_SIZE + 1
  const last = Math.min(query.page * PAGE_SIZE, total)

  return (
    <>
      <div role="note" className="rounded-lg border border-line-strong bg-chip-selected-bg px-3.5 py-2.5 text-[13px]">
        Operator views show account facts and counters only. Trades, notes and screenshots are never shown, and opening
        an account is recorded in the audit log.
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <Card className="min-w-0 flex-[3_1_560px] p-0" role="region" aria-label="Accounts">
          <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-4 py-3.5">
            <SearchBox
              value={query.q}
              onSearch={(q) => onQuery({ ...query, q, page: 1 })}
              label="Search accounts by email or id"
              placeholder="Search by email or id"
            />
            <select
              aria-label="Filter by status"
              value={query.status}
              onChange={(e) => onQuery({ ...query, status: e.target.value as UserStatus | '', page: 1 })}
              className={`${CONTROL} h-9! w-auto!`}
            >
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <span className="grow" />
            <span className="text-[13px] text-muted" aria-live="polite">
              {users.data ? `${total.toLocaleString('en-US')} ${total === 1 ? 'account' : 'accounts'}` : ''}
            </span>
          </div>
          {users.isPending ? <Spinner label="Loading accounts" /> : null}
          {users.isError ? (
            <p role="alert" className="m-0 px-4 py-4 text-neg">
              {messageOf(users.error)}
            </p>
          ) : null}
          {users.data ? (
            users.data.users.length === 0 ? (
              <p className="m-0 px-4 py-4 text-muted">No accounts match.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th className="border-b border-line px-3.5 py-2.5 font-medium">Email</th>
                      <th className="border-b border-line px-3.5 py-2.5 font-medium">Status</th>
                      <th className="border-b border-line px-3.5 py-2.5 font-medium">Plan</th>
                      <th className="border-b border-line px-3.5 py-2.5 font-medium">Last sign-in</th>
                      <th className="border-b border-line px-3.5 py-2.5 text-right font-medium">Accounts</th>
                      <th className="border-b border-line px-3.5 py-2.5 text-right font-medium">Trades</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.data.users.map((user) => (
                      <tr
                        key={user.id}
                        aria-selected={user.id === selected}
                        className={user.id === selected ? 'bg-chip-selected-bg' : undefined}
                      >
                        <td className="border-b border-line-soft px-3.5 py-2.5">
                          <button
                            type="button"
                            onClick={() => onSelect(user.id === selected ? null : user.id)}
                            className="cursor-pointer border-0 bg-transparent p-0 text-left font-[inherit] text-ink underline-offset-2 hover:underline"
                          >
                            {user.email}
                          </button>
                        </td>
                        <td className="border-b border-line-soft px-3.5 py-2.5">
                          <StatusBadge status={user.status} />
                        </td>
                        <td className="border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                          <PlanCell plan={user.plan} />
                        </td>
                        <td className="num border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                          {user.last_login_at ? utcDay(user.last_login_at) : '—'}
                        </td>
                        <td className="num border-b border-line-soft px-3.5 py-2.5 text-right">
                          {user.usage.accounts}
                        </td>
                        <td className="num border-b border-line-soft px-3.5 py-2.5 text-right">
                          {user.usage.trades.toLocaleString('en-US')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : null}
          {users.data && total > PAGE_SIZE ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-[13px]">
              <span className="text-muted">
                {first}–{last} of {total.toLocaleString('en-US')}
              </span>
              <span className="flex gap-2">
                <Button onClick={() => onQuery({ ...query, page: query.page - 1 })} disabled={query.page <= 1}>
                  Previous
                </Button>
                <Button onClick={() => onQuery({ ...query, page: query.page + 1 })} disabled={last >= total}>
                  Next
                </Button>
              </span>
            </div>
          ) : null}
        </Card>
        <aside className="flex min-w-0 flex-[2_1_380px] flex-col gap-4" aria-label="Selected account">
          {selected ? (
            <UserPanel key={selected} id={selected} />
          ) : (
            <Card className="px-5 py-4 text-muted">Choose an account to see its plan, counters and history.</Card>
          )}
        </aside>
      </div>
    </>
  )
}
