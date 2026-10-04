import { Link } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { AccountMenu, ThemeButton } from '../../components/AppShell'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { stepUpUrl } from '../../lib/admin'
import { go } from '../../lib/auth'
import { useSession } from '../../lib/session'

/** How often the page asks again whether the second factor is still fresh (it lasts 15 minutes, then the server refuses). */
const RECHECK_MS = 60_000

const NAV = [{ to: '/admin', label: 'Users' }] as const

/**
 * The operator's frame: its own header (so it is never mistaken for the journal), and a gate. Only an operator gets past
 * it, and only with two-step verification confirmed in the last 15 minutes; otherwise the page says what to do. The server
 * enforces both on every call, so this only decides what to show.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const session = useSession()
  const queryClient = useQueryClient()

  useEffect(() => {
    // The flag is computed when the session is read; read it again so a verification that has run out is noticed.
    const timer = setInterval(() => void queryClient.invalidateQueries({ queryKey: ['session'] }), RECHECK_MS)
    return () => clearInterval(timer)
  }, [queryClient])

  if (session.isPending) return <Spinner label="Loading" />
  if (session.isError) return <p className="p-6">Redirecting to sign in…</p>
  const { user, settings, flags } = session.data

  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex min-h-14 max-w-[1360px] flex-wrap items-center gap-x-6 gap-y-1 px-4 sm:px-8">
          <div className="flex items-center gap-2.5 text-base font-semibold">
            <span aria-hidden="true" className="inline-block size-[22px] rounded-md bg-warn" />
            Roundturn
            <span className="inline-flex h-[22px] items-center rounded-full border border-warn-line px-2 text-xs font-medium text-warn">
              Operator
            </span>
          </div>
          <nav aria-label="Operator" className="order-last flex w-full grow sm:order-none sm:w-auto">
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="flex h-12 items-center border-b-2 border-transparent px-3.5 whitespace-nowrap text-muted no-underline hover:text-ink data-[status=active]:border-warn data-[status=active]:text-ink sm:h-14"
              >
                {item.label}
              </Link>
            ))}
            <Link
              to="/"
              className="flex h-12 items-center px-3.5 whitespace-nowrap text-muted no-underline hover:text-ink sm:h-14"
            >
              Back to the app
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-3 sm:ml-0">
            {flags.is_admin ? (
              <span
                className={
                  flags.mfa_fresh
                    ? 'inline-flex h-[22px] items-center rounded-full border border-line bg-long-bg px-2 text-xs text-pos'
                    : 'inline-flex h-[22px] items-center rounded-full border border-warn-line px-2 text-xs text-warn'
                }
              >
                {flags.mfa_fresh ? 'Two-step verification current' : 'Not verified'}
              </span>
            ) : null}
            <ThemeButton theme={settings.theme} />
            <AccountMenu email={user.email} />
          </div>
        </div>
      </header>
      <main className="mx-auto flex max-w-[1360px] flex-col gap-4 px-4 pt-6 pb-12 sm:px-8">
        {!flags.is_admin ? (
          <Card className="flex flex-col gap-2 px-5 py-4" role="region" aria-label="Operators only">
            <h1 className="m-0 text-lg font-semibold">This area is for operators.</h1>
            <p className="m-0 text-muted">Your account is not an operator, so there is nothing for you here.</p>
            <div>
              <Link to="/">Back to the app</Link>
            </div>
          </Card>
        ) : !flags.mfa_fresh ? (
          <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Confirm it is you">
            <h1 className="m-0 text-lg font-semibold">Confirm it is you</h1>
            <p className="m-0 text-muted">
              The operator tools need two-step verification from the last 15 minutes. You will be asked for your second
              factor, then brought back here.
            </p>
            <div>
              <Button variant="primary" onClick={() => go.to(stepUpUrl('/admin'))}>
                Verify with two-step verification
              </Button>
            </div>
          </Card>
        ) : (
          children
        )}
      </main>
    </div>
  )
}
