import { useState } from 'react'
import { Button, Spinner } from '../../components/ui/primitives'
import { CODES_PAGE, STATUS_TEXT, type CodeStatus, type GrantCode, type Invite } from '../../lib/adminCodes'
import { utcDay } from '../../lib/admin'

const STATUS_CLASS: Record<CodeStatus, string> = {
  works: 'bg-long-bg text-pos',
  used_up: 'bg-raised text-muted',
  expired: 'bg-raised text-muted',
  revoked: 'bg-short-bg text-neg',
}

function Status({ status }: { status: CodeStatus }) {
  return (
    <span
      className={`inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium whitespace-nowrap ${STATUS_CLASS[status]}`}
    >
      {STATUS_TEXT[status]}
    </span>
  )
}

const TH = 'border-b border-line px-3.5 py-2.5 font-medium'
const TD = 'border-b border-line-soft px-3.5 py-2.5'

function Footer({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) {
  if (total <= CODES_PAGE) return null
  const first = (page - 1) * CODES_PAGE + 1
  const last = Math.min(page * CODES_PAGE, total)
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-[13px]">
      <span className="text-muted">
        {first}–{last} of {total}
      </span>
      <span className="flex gap-2">
        <Button onClick={() => onPage(page - 1)} disabled={page <= 1}>
          Previous
        </Button>
        <Button onClick={() => onPage(page + 1)} disabled={last >= total}>
          Next
        </Button>
      </span>
    </div>
  )
}

/** Invites: what each is for, how many have used it, until when, and whether it still works. */
export function InvitesTable({
  rows,
  total,
  page,
  loading,
  onPage,
  onRevoke,
}: {
  rows: Invite[] | undefined
  total: number
  page: number
  loading: boolean
  onPage: (page: number) => void
  onRevoke: (invite: Invite) => void
}) {
  if (loading) return <Spinner label="Loading invites" />
  if (!rows || rows.length === 0) return <p className="m-0 px-4 py-4 text-muted">No invites yet.</p>
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className={TH}>For</th>
              <th className={TH}>Plan</th>
              <th className={`${TH} text-right`}>Used</th>
              <th className={TH}>Ends</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`}>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((invite) => (
              <tr key={invite.id}>
                <td className={`${TD} min-w-[150px]`}>
                  <div>{invite.label || <span className="text-muted">(no label)</span>}</div>
                  {invite.email ? <div className="text-xs text-muted">{invite.email}</div> : null}
                </td>
                <td className={`${TD} min-w-[150px]`}>
                  {invite.grant_label ?? <span className="text-muted">None</span>}
                </td>
                <td className={`${TD} num text-right whitespace-nowrap`}>
                  {invite.used} / {invite.max_uses}
                </td>
                <td className={`${TD} num whitespace-nowrap`}>{invite.expires_at ? utcDay(invite.expires_at) : '—'}</td>
                <td className={TD}>
                  <Status status={invite.status} />
                </td>
                <td className={`${TD} text-right`}>
                  {invite.status === 'revoked' ? (
                    <span className="text-muted">—</span>
                  ) : (
                    <Button
                      className="h-[30px]"
                      onClick={() => onRevoke(invite)}
                      aria-label={`Revoke the invite ${invite.label || invite.id}`}
                    >
                      Revoke
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Footer page={page} total={total} onPage={onPage} />
    </>
  )
}

/** A typed code with a Copy button beside it (a random code has nothing to copy: it was shown once). */
function CopyCode({ code }: { code: string }) {
  const [copied, setCopied] = useState<'yes' | 'no' | null>(null)
  return (
    <span className="inline-flex items-center gap-2">
      <span className="num font-mono font-semibold tracking-wide">{code}</span>
      <Button
        className="h-[26px] px-2 text-xs"
        aria-label={`Copy ${code}`}
        onClick={() =>
          navigator.clipboard.writeText(code).then(
            () => setCopied('yes'),
            () => setCopied('no'),
          )
        }
      >
        {copied === 'yes' ? 'Copied' : 'Copy'}
      </Button>
      {copied === 'no' ? (
        <span role="alert" className="text-xs text-neg">
          Could not copy
        </span>
      ) : null}
    </span>
  )
}

/** Where a discount code's copy at the payment provider stands. Every other kind has no copy, and nothing is shown. */
function SyncPill({ code, live }: { code: GrantCode; live: boolean }) {
  if (code.kind !== 'discount' || code.sync_status === null) return null
  const [text, tone] =
    code.sync_status === 'failed'
      ? [`Sync failed${code.sync_error ? ` (${code.sync_error})` : ''}`, 'text-neg']
      : code.sync_status === 'pending'
        ? ['Not at the payment provider yet', 'text-warn']
        : live
          ? ['At the payment provider', 'text-muted']
          : ['At the payment provider · not live while billing is off', 'text-muted']
  return <div className={`text-xs ${tone}`}>{text}</div>
}

/** Promo codes: the code (or that it was random), what it gives, how many people used it, until when, and where it stands. */
export function GrantCodesTable({
  rows,
  total,
  page,
  loading,
  onPage,
  onRevoke,
  discountsLive = false,
  onSync,
  syncing = null,
}: {
  rows: GrantCode[] | undefined
  total: number
  page: number
  loading: boolean
  onPage: (page: number) => void
  onRevoke: (code: GrantCode) => void
  /** Whether people can use discount codes now (billing on, a provider set). */
  discountsLive?: boolean
  /** Try a discount code's copy at the provider again. */
  onSync?: (code: GrantCode) => void
  /** The code being synced now, if any. */
  syncing?: number | null
}) {
  if (loading) return <Spinner label="Loading promo codes" />
  if (!rows || rows.length === 0) return <p className="m-0 px-4 py-4 text-muted">No codes here yet.</p>
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className={TH}>Code</th>
              <th className={TH}>Gives</th>
              <th className={`${TH} text-right`}>Used</th>
              <th className={TH}>Stops working</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`}>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((code) => (
              <tr key={code.id}>
                <td className={`${TD} min-w-[170px]`}>
                  {code.code ? (
                    <CopyCode code={code.code} />
                  ) : (
                    <span className="num font-mono text-muted" title="A random code is shown once and never kept">
                      GR-····-····
                    </span>
                  )}
                  <div className="text-xs text-muted">
                    {code.label || (code.code ? '' : 'Random code')}
                    {!code.code && code.label ? ' · random code, shown once' : ''}
                  </div>
                </td>
                <td className={TD}>
                  <div className="whitespace-nowrap">
                    {code.kind === 'discount' ? (code.words ?? 'A discount') : `${code.days} days of Pro`}
                  </div>
                  <SyncPill code={code} live={discountsLive} />
                </td>
                <td className={`${TD} num text-right whitespace-nowrap`}>
                  {code.used} / {code.max_redemptions === null ? 'any' : code.max_redemptions}
                </td>
                <td className={`${TD} num whitespace-nowrap`}>{code.expires_at ? utcDay(code.expires_at) : '—'}</td>
                <td className={TD}>
                  <Status status={code.status} />
                </td>
                <td className={`${TD} text-right`}>
                  <div className="flex flex-wrap justify-end gap-2">
                    {code.kind === 'discount' && code.sync_status === 'failed' && onSync ? (
                      <Button
                        className="h-[30px]"
                        onClick={() => onSync(code)}
                        disabled={syncing === code.id}
                        aria-label={`Retry the code ${code.code ?? code.label} at the payment provider`}
                      >
                        {syncing === code.id ? 'Retrying…' : 'Retry'}
                      </Button>
                    ) : null}
                    {code.status === 'revoked' ? (
                      code.sync_status === 'failed' ? null : (
                        <span className="text-muted">—</span>
                      )
                    ) : (
                      <Button
                        className="h-[30px]"
                        onClick={() => onRevoke(code)}
                        aria-label={`Revoke the code ${code.code ?? code.label}`}
                      >
                        Revoke
                      </Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Footer page={page} total={total} onPage={onPage} />
    </>
  )
}
