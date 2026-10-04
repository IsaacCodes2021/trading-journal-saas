import { Button, Card, Spinner } from '../../components/ui/primitives'
import { formatTradeTime, pluralize } from '../../lib/format'
import { useSession } from '../../lib/session'
import { useActiveSessions, useEndOtherSessions, useEndSession, type ActiveSession } from '../../lib/sessions'
import { messageOf } from '../tags/messages'
import { SectionHeader } from './SettingsLayout'

function Row({
  session,
  timeZone,
  ending,
  onEnd,
}: {
  session: ActiveSession
  timeZone: string
  ending: boolean
  onEnd: () => void
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
      <div className="min-w-0 flex-1 basis-[240px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{session.device}</span>
          {session.current ? (
            <span className="inline-flex h-[22px] items-center rounded-full bg-chip-selected-bg px-2.5 text-xs font-medium text-accent">
              This device
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 text-[13px] text-muted">
          Signed in {formatTradeTime(session.signed_in_at, timeZone)} &middot; Last active{' '}
          {formatTradeTime(session.last_active_at, timeZone)}
          {session.mfa ? ' · Two-step verification' : ''}
        </div>
      </div>
      {session.current ? null : (
        <Button onClick={onEnd} disabled={ending} aria-label={`Sign out ${session.device}`}>
          {ending ? 'Signing out…' : 'Sign out'}
        </Button>
      )}
    </li>
  )
}

/** Where the account is signed in, with a way to end any of them, or all but this one. */
export function SecurityPage() {
  const sessions = useActiveSessions()
  const account = useSession()
  const endOne = useEndSession()
  const endOthers = useEndOtherSessions()
  const timeZone = account.data?.settings.timezone ?? 'UTC'

  const others = sessions.data?.sessions.filter((row) => !row.current).length ?? 0
  const failure = endOne.isError ? endOne.error : endOthers.isError ? endOthers.error : null
  const done = endOthers.isSuccess
    ? endOthers.data.ended === 0
      ? 'There was nowhere else to sign out of.'
      : `Signed out of ${pluralize(endOthers.data.ended, 'other place')}.`
    : endOne.isSuccess
      ? 'Signed out of that device.'
      : null

  return (
    <>
      <SectionHeader title="Security">Where your account is signed in right now.</SectionHeader>
      {sessions.isPending ? <Spinner label="Loading your sign-ins" /> : null}
      {sessions.isError ? (
        <Card className="px-5 py-4 text-neg" role="alert">
          {messageOf(sessions.error)}
        </Card>
      ) : null}
      {sessions.data ? (
        <Card className="p-0" role="region" aria-label="Where you are signed in">
          <ul className="m-0 list-none divide-y divide-line-soft p-0">
            {sessions.data.sessions.map((row) => (
              <Row
                key={row.id}
                session={row}
                timeZone={timeZone}
                ending={endOne.isPending && endOne.variables === row.id}
                onEnd={() => {
                  endOthers.reset()
                  endOne.mutate(row.id)
                }}
              />
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line px-5 py-3.5">
            <p className="m-0 min-w-0 flex-1 basis-[240px] text-[13px] text-muted">
              To sign out of this device, use Sign out in the account menu at the top right.
            </p>
            <Button
              onClick={() => {
                endOne.reset()
                endOthers.mutate()
              }}
              disabled={others === 0 || endOthers.isPending}
            >
              {endOthers.isPending ? 'Signing out…' : 'Sign out everywhere else'}
            </Button>
          </div>
        </Card>
      ) : null}
      {failure ? (
        <div role="alert" className="text-[13px] text-neg">
          {messageOf(failure)}
        </div>
      ) : null}
      {done && !failure ? (
        <div role="status" className="text-[13px] text-muted">
          {done}
        </div>
      ) : null}
    </>
  )
}
