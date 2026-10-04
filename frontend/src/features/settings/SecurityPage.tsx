import { Button, Card, Spinner } from '../../components/ui/primitives'
import { activityText, useActivity } from '../../lib/activity'
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

/** What has happened to the account lately: the server's own record, as sentences, in the person's timezone. */
function RecentActivity({ timeZone }: { timeZone: string }) {
  const activity = useActivity()
  return (
    <Card className="p-0" role="region" aria-label="Recent activity">
      <h2 className="m-0 border-b border-line px-5 py-3.5 text-base font-semibold">Recent activity</h2>
      {activity.isPending ? <Spinner label="Loading recent activity" /> : null}
      {activity.isError ? (
        <p className="m-0 px-5 py-4 text-neg" role="alert">
          {messageOf(activity.error)}
        </p>
      ) : null}
      {activity.data ? (
        activity.data.events.length === 0 ? (
          <p className="m-0 px-5 py-4 text-muted">Nothing yet.</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-line-soft p-0">
            {activity.data.events.map((event) => (
              <li
                key={`${event.at}-${event.kind}`}
                className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 py-3"
              >
                <span className={event.kind === 'sign_in_refused' ? 'text-warn' : undefined}>
                  {activityText(event)}
                </span>
                <time dateTime={event.at} className="text-[13px] text-muted">
                  {formatTradeTime(event.at, timeZone)}
                </time>
              </li>
            ))}
          </ul>
        )
      ) : null}
      <p className="m-0 border-t border-line px-5 py-3.5 text-[13px] text-muted">
        Only you can see this list. If something here was not you, sign out everywhere else, and change your password
        where you sign in.
      </p>
    </Card>
  )
}

/** Where the account is signed in, with a way to end any of them, or all but this one; and what has happened lately. */
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
      <SectionHeader title="Security">
        Where your account is signed in, and what has happened to it lately.
      </SectionHeader>
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
      <RecentActivity timeZone={timeZone} />
    </>
  )
}
