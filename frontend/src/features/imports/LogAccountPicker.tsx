import type { ReactNode } from 'react'
import { Card } from '../../components/ui/primitives'
import type { LogAccount } from '../../lib/data/model'
import { pluralize } from '../../lib/format'

/** "LFE0506… · 18 round turns · practice · already in Lucid 12". */
function describe(account: LogAccount): string {
  const parts = [account.name, pluralize(account.round_turns, 'round turn')]
  if (account.practice) parts.push('practice')
  if (account.here > 0) parts.push('imported here before')
  else if (account.elsewhere) parts.push(`already in ${account.elsewhere}`)
  return parts.join(' · ')
}

/**
 * Which NinjaTrader account of the Round Turn Monitor's log goes into the Roundturn account picked on the page. The log
 * holds every account NinjaTrader tracks, so one import takes one of them (decision R2 of
 * docs/design/round-turn-monitor-import-plan.md). The server picks one when it can: the one this account already holds,
 * else the only real one (never Sim101 over a real account). Choosing another works the preview out again.
 *
 * `message` is the server's question when it could not pick ("This log has round turns from 3 accounts…"); `children`
 * are extra buttons beside the list.
 */
export function LogAccountPicker({
  accounts,
  chosen,
  accountName,
  message,
  onChoose,
  children,
}: {
  accounts: readonly LogAccount[]
  chosen: string | null
  accountName: string
  message?: string | null
  onChoose: (name: string) => void
  children?: ReactNode
}) {
  return (
    <Card className="flex flex-col gap-2 px-5 py-4" role="region" aria-label="NinjaTrader account">
      <p className="m-0 text-[13px]">
        {message ??
          `This log has round turns from ${accounts.length} NinjaTrader accounts. Choose the one that goes into ${accountName}.`}
      </p>
      <label htmlFor="log-account" className="text-[13px] font-medium">
        NinjaTrader account to import into {accountName}
      </label>
      <div className="flex flex-wrap gap-2">
        <select
          id="log-account"
          value={chosen ?? ''}
          onChange={(event) => onChoose(event.target.value)}
          className="h-[42px] min-w-0 flex-[1_1_260px] rounded-lg border border-line bg-raised px-3 font-[inherit] text-ink"
        >
          {chosen === null ? (
            <option value="" disabled>
              Choose an account
            </option>
          ) : null}
          {accounts.map((account) => (
            <option key={account.name} value={account.name}>
              {describe(account)}
            </option>
          ))}
        </select>
        {children}
      </div>
    </Card>
  )
}
