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

/** Plan codes: what each gives, how many people used it, until when, and whether it still works. */
export function GrantCodesTable({
  rows,
  total,
  page,
  loading,
  onPage,
  onRevoke,
}: {
  rows: GrantCode[] | undefined
  total: number
  page: number
  loading: boolean
  onPage: (page: number) => void
  onRevoke: (code: GrantCode) => void
}) {
  if (loading) return <Spinner label="Loading plan codes" />
  if (!rows || rows.length === 0) return <p className="m-0 px-4 py-4 text-muted">No plan codes yet.</p>
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className={TH}>Label</th>
              <th className={TH}>Gives</th>
              <th className={`${TH} text-right`}>Used</th>
              <th className={TH}>Ends</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`}>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((code) => (
              <tr key={code.id}>
                <td className={`${TD} min-w-[150px]`}>{code.label}</td>
                <td className={`${TD} whitespace-nowrap`}>{code.days} days of Pro</td>
                <td className={`${TD} num text-right whitespace-nowrap`}>
                  {code.used}
                  {code.max_redemptions === null ? '' : ` / ${code.max_redemptions}`}
                </td>
                <td className={`${TD} num whitespace-nowrap`}>{code.expires_at ? utcDay(code.expires_at) : '—'}</td>
                <td className={TD}>
                  <Status status={code.status} />
                </td>
                <td className={`${TD} text-right`}>
                  {code.status === 'revoked' ? (
                    <span className="text-muted">—</span>
                  ) : (
                    <Button
                      className="h-[30px]"
                      onClick={() => onRevoke(code)}
                      aria-label={`Revoke the plan code ${code.label}`}
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
