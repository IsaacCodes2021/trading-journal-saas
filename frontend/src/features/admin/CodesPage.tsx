import { useState } from 'react'
import { Card, Spinner } from '../../components/ui/primitives'
import { useGrantCodes, useInvites, useOverview } from '../../lib/adminCodes'
import { messageOf } from '../tags/messages'
import { AuditLog } from './AuditLog'
import { CreateGrantCode, CreateInvite, NewCode, RevokeDialog, type Dialog } from './CodeForms'
import { GrantCodesTable, InvitesTable } from './CodeTables'
import { auditQueryOf, searchWithAudit, type CodesSearch } from './codesSearch'

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Card className="min-w-0 px-4 py-3.5">
      <div className="text-xs text-muted">{label}</div>
      <div className="num mt-1 text-2xl font-semibold">{children}</div>
    </Card>
  )
}

// The spaces sit inside the small sans-serif spans: next to a number in the large monospace face they would be far too wide.
const dot = <span className="font-sans text-muted">{' · '}</span>
const unit = (word: string) => <span className="font-sans text-sm font-normal text-muted">{` ${word}`}</span>

/** The numbers an operator looks at first. */
function Overview() {
  const overview = useOverview()
  if (overview.isPending) return <Spinner label="Loading the overview" />
  if (overview.isError)
    return (
      <p role="alert" className="m-0 text-neg">
        {messageOf(overview.error)}
      </p>
    )
  const o = overview.data
  const n = (value: number) => value.toLocaleString('en-US')
  return (
    <section aria-label="Overview" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Tile label="Accounts">{n(o.accounts)}</Tile>
      <Tile label="Signups, last 7 days">{n(o.signups_7d)}</Tile>
      <Tile label="Signed in, last 7 days">{n(o.active_7d)}</Tile>
      <Tile label="Plans">
        {n(o.free)}
        {unit('Free')}
        {dot}
        {n(o.pro)}
        {unit('Pro')}
      </Tile>
      <Tile label="Suspended · being deleted">
        {n(o.suspended)}
        {dot}
        {n(o.pending_deletion)}
      </Tile>
      <Tile label="Invites · plan codes that work">
        {n(o.open_invites)}
        {dot}
        {n(o.open_grant_codes)}
      </Tile>
    </section>
  )
}

function Tabs({ tab, onTab }: { tab: 'invites' | 'codes'; onTab: (tab: 'invites' | 'codes') => void }) {
  const items = [
    { value: 'invites', label: 'Invites' },
    { value: 'codes', label: 'Plan codes' },
  ] as const
  return (
    <div role="tablist" aria-label="Codes" className="flex flex-wrap border-b border-line px-2.5">
      {items.map((item) => (
        <button
          key={item.value}
          role="tab"
          type="button"
          aria-selected={tab === item.value}
          onClick={() => onTab(item.value)}
          className="-mb-px h-11 cursor-pointer border-0 border-b-2 border-transparent bg-transparent px-3.5 font-[inherit] text-muted hover:text-ink aria-selected:border-warn aria-selected:text-ink"
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

/** Invites and plan codes (with a way to make each), and the audit log. State is in the address, so a view can be shared. */
export function CodesPage({ search, onSearch }: { search: CodesSearch; onSearch: (next: CodesSearch) => void }) {
  const tab = search.tab ?? 'invites'
  const page = search.page ?? 1
  const audit = auditQueryOf(search)
  const invites = useInvites(tab === 'invites' ? page : 1)
  const grants = useGrantCodes(tab === 'codes' ? page : 1)
  const working = useGrantCodes(1).data?.grant_codes.filter((g) => g.status === 'works') ?? []
  const [fresh, setFresh] = useState<{ code: string; what: string } | null>(null)
  const [revoking, setRevoking] = useState<Dialog | null>(null)

  const go = (next: { tab?: 'codes'; page?: number }) =>
    onSearch({
      ...searchWithAudit({}, audit),
      ...(next.tab ? { tab: next.tab } : {}),
      ...(next.page && next.page > 1 ? { page: next.page } : {}),
    })

  return (
    <>
      <h1 className="m-0 text-[22px] font-semibold">Overview</h1>
      <Overview />
      <div className="flex flex-wrap items-start gap-4">
        <Card
          className="min-w-0 flex-[3_1_560px] p-0"
          role="region"
          aria-label={tab === 'invites' ? 'Invites' : 'Plan codes'}
        >
          <Tabs tab={tab} onTab={(next) => go(next === 'codes' ? { tab: 'codes' } : {})} />
          {tab === 'invites' ? (
            <>
              {invites.isError ? (
                <p role="alert" className="m-0 px-4 py-4 text-neg">
                  {messageOf(invites.error)}
                </p>
              ) : null}
              <InvitesTable
                rows={invites.data?.invites}
                total={invites.data?.total ?? 0}
                page={page}
                loading={invites.isPending}
                onPage={(next) => go({ page: next })}
                onRevoke={(invite) => setRevoking({ kind: 'invite', id: invite.id, label: invite.label })}
              />
            </>
          ) : (
            <>
              {grants.isError ? (
                <p role="alert" className="m-0 px-4 py-4 text-neg">
                  {messageOf(grants.error)}
                </p>
              ) : null}
              <GrantCodesTable
                rows={grants.data?.grant_codes}
                total={grants.data?.total ?? 0}
                page={page}
                loading={grants.isPending}
                onPage={(next) => go({ tab: 'codes', page: next })}
                onRevoke={(code) => setRevoking({ kind: 'grant-code', id: code.id, label: code.label })}
              />
            </>
          )}
        </Card>
        <aside className="flex min-w-0 flex-[2_1_360px] flex-col gap-4" aria-label="Make a code">
          {fresh ? <NewCode code={fresh.code} what={fresh.what} onDone={() => setFresh(null)} /> : null}
          {tab === 'invites' ? (
            <CreateInvite working={working} onCreated={(code) => setFresh({ code, what: 'Your new invite' })} />
          ) : (
            <CreateGrantCode onCreated={(code) => setFresh({ code, what: 'Your new plan code' })} />
          )}
        </aside>
      </div>
      <AuditLog query={audit} onQuery={(next) => onSearch({ ...searchWithAudit(search, next) })} />
      {revoking ? <RevokeDialog {...revoking} onClose={() => setRevoking(null)} /> : null}
    </>
  )
}
