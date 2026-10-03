import { Link, useSearch } from '@tanstack/react-router'
import { useEffect, type ReactNode } from 'react'
import { csrfHeader } from '../lib/api/client'
import type { components } from '../lib/api/schema'
import { useDataSource } from '../lib/data/source'
import { filtersOf, parseFilters } from '../lib/filters'
import { daysLeft } from '../lib/format'
import { useSaveTheme, useSession } from '../lib/session'
import { applyTheme, otherTheme, type Theme } from '../lib/theme'
import { Popover } from './ui/Popover'
import { Button, Spinner } from './ui/primitives'
import { cx } from './ui/cx'

type Session = components['schemas']['SessionOut']

const NAV = [
  { to: '/', label: 'Dashboard', exact: true },
  { to: '/trades', label: 'Trades', exact: false },
  { to: '/import', label: 'Import', exact: false },
  { to: '/settings', label: 'Settings', exact: false },
] as const

function titleCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** "Pro · 9 days left" for a plan that expires, "Free" otherwise. Amber while a plan is running out. */
function PlanChip({ plan }: { plan: Session['plan'] }) {
  const left = daysLeft(plan.expires_at)
  const expiring = left !== null
  return (
    <span
      className={cx(
        'inline-flex h-[22px] items-center rounded-full border px-2 text-xs',
        expiring ? 'border-warn-line text-warn' : 'border-line text-muted',
      )}
    >
      {titleCase(plan.effective)}
      {expiring ? ` · ${left} ${left === 1 ? 'day' : 'days'} left` : ''}
    </span>
  )
}

function ThemeButton({ theme }: { theme: Theme }) {
  const save = useSaveTheme()
  const target = otherTheme(theme)
  const onClick = () => {
    applyTheme(target) // instant; the server copy follows
    save.mutate(target, { onError: () => applyTheme(theme) })
  }
  return (
    <Button icon onClick={onClick} aria-label={`Switch to ${target} theme`}>
      {target === 'light' ? (
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="3" />
          <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
        </svg>
      ) : (
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <path d="M13.5 9.5A5.5 5.5 0 1 1 6.5 2.5a4.5 4.5 0 0 0 7 7z" />
        </svg>
      )}
    </Button>
  )
}

function initialsOf(email: string): string {
  return (
    (email.split('@')[0] ?? '?')
      .replace(/[^a-z0-9]/gi, '')
      .slice(0, 2)
      .toUpperCase() || '?'
  )
}

function AccountMenu({ email }: { email: string }) {
  const signOut = async () => {
    await fetch('/auth/logout', { method: 'POST', headers: csrfHeader() })
    window.location.assign('/')
  }
  return (
    <Popover
      label="Account"
      align="right"
      buttonClassName="size-9 justify-center rounded-full bg-selected p-0 text-xs font-semibold"
      button={<span aria-label="Account menu">{initialsOf(email)}</span>}
    >
      {() => (
        <div className="flex min-w-[220px] flex-col gap-2 p-1">
          <div className="px-2 pt-1 text-xs text-muted">Signed in as</div>
          <div data-testid="who" className="px-2 text-[13px] break-all">
            {email}
          </div>
          <Button onClick={signOut}>Sign out</Button>
        </div>
      )}
    </Popover>
  )
}

/** Says out loud that the numbers are made up, on every page, for as long as a sample source is in use. */
function SampleDataBanner() {
  const label = useDataSource().label
  if (label === null) return null
  return (
    <div role="note" className="border-b border-warn-line bg-panel px-4 py-1.5 text-center text-xs text-warn sm:px-8">
      <strong>{label}.</strong> These numbers are made up so the screens can be tried, and changes you make are not
      kept. Your own data arrives with the next releases.
    </div>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const session = useSession()
  const search = useSearch({ strict: false })
  const theme = session.data?.settings.theme
  useEffect(() => {
    if (theme) applyTheme(theme) // the account's choice wins over what this browser remembered
  }, [theme])

  if (session.isPending) return <Spinner label="Loading" />
  if (session.isError) return <p className="p-6">Redirecting to sign in…</p>
  const filters = filtersOf(parseFilters(search as Record<string, unknown>))
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex min-h-14 max-w-[1360px] flex-wrap items-center gap-x-6 gap-y-1 px-4 sm:px-8">
          <div className="flex items-center gap-2.5 text-base font-semibold">
            <span aria-hidden="true" className="inline-block size-[22px] rounded-md bg-accent" />
            Roundturn
          </div>
          <nav
            aria-label="Main"
            className="order-last -mx-2 flex w-full grow overflow-x-auto sm:order-none sm:mx-0 sm:w-auto"
          >
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                search={filters}
                activeOptions={{ exact: item.exact }}
                className="flex h-12 items-center border-b-2 border-transparent px-3.5 whitespace-nowrap text-muted no-underline hover:text-ink data-[status=active]:border-accent data-[status=active]:text-ink sm:h-14"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 sm:ml-0">
            <PlanChip plan={session.data.plan} />
            <ThemeButton theme={session.data.settings.theme} />
            <AccountMenu email={session.data.user.email} />
          </div>
        </div>
      </header>
      <SampleDataBanner />
      <main className="mx-auto flex max-w-[1360px] flex-col gap-4 px-4 pt-6 pb-12 sm:px-8">{children}</main>
    </div>
  )
}
