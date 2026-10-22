import { Link, useLocation, useSearch } from '@tanstack/react-router'
import { useEffect, useState, type ReactNode } from 'react'
import type { components } from '../lib/api/schema'
import { go, signOut } from '../lib/auth'
import {
  bannerHiddenToday,
  bannerOf,
  billingDay,
  hideBannerToday,
  planTag,
  useBilling,
  type BannerKind,
  type Billing,
  type TagTone,
} from '../lib/billing'
import { exitDemo, isDemo } from '../lib/demoMode'
import { useDataSource } from '../lib/data/source'
import { filtersOf, parseFilters } from '../lib/filters'
import { daysLeft, pluralize } from '../lib/format'
import { useSaveTheme, useSession } from '../lib/session'
import { applyTheme, otherTheme, type Theme } from '../lib/theme'
import { BiteMark } from './BiteMark'
import { Popover } from './ui/Popover'
import { Button, Spinner } from './ui/primitives'
import { cx } from './ui/cx'

type Session = components['schemas']['SessionOut']

const NAV = [
  { to: '/', label: 'Dashboard', exact: true },
  { to: '/trades', label: 'Trades', exact: false },
  { to: '/calendar', label: 'Calendar', exact: false },
  { to: '/reports', label: 'Reports', exact: false },
  { to: '/accounts', label: 'Accounts', exact: false },
  { to: '/import', label: 'Import', exact: false },
  { to: '/settings', label: 'Settings', exact: false },
] as const

function titleCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

const TAG_TONE: Record<TagTone, string> = {
  quiet: 'border-line text-muted',
  warn: 'border-warn-line text-warn',
  bad: 'border-transparent bg-short-bg text-neg',
}

/**
 * The plan tag, which leads to Plan & billing. While billing is on it says where billing stands ("Trial · 5 days left",
 * "Payment failed", "Read-only"); while it is off, "Pro · 9 days left" for a plan that expires (amber), "Free" otherwise.
 */
function PlanChip({ plan, billing }: { plan: Session['plan']; billing: Billing | null }) {
  let tag: { text: string; tone: TagTone }
  if (billing) {
    tag = planTag(billing)
  } else {
    const left = daysLeft(plan.expires_at)
    tag =
      left === null
        ? { text: titleCase(plan.effective), tone: 'quiet' }
        : { text: `${titleCase(plan.effective)} · ${left} ${left === 1 ? 'day' : 'days'} left`, tone: 'warn' }
  }
  return (
    <Link
      to="/settings/plan"
      title="Plan & billing"
      className={cx(
        'inline-flex h-[22px] items-center rounded-full border px-2 text-xs whitespace-nowrap no-underline hover:border-line-strong',
        TAG_TONE[tag.tone],
      )}
    >
      {tag.text}
    </Link>
  )
}

const BANNER_TONE: Record<BannerKind, string> = {
  trial: 'border-warn-line bg-gold-bg text-warn',
  payment: 'border-warn-line bg-gold-bg text-warn',
  read_only: 'border-line bg-short-bg text-ink',
}

/**
 * One billing banner under the header, while billing is on: read-only (it stays), else a failed payment, else the
 * trial's last 3 days (those two can be closed until tomorrow). Each leads to Plan & billing.
 */
function BillingBanner({ billing }: { billing: Billing | null }) {
  const [, setHidden] = useState(0)
  const kind = bannerOf(billing)
  if (!billing || kind === null || bannerHiddenToday(kind)) return null
  const left = daysLeft(billing.ends_at) ?? 0
  const text = {
    trial: `Your free trial ends in ${pluralize(left, 'day')}. Subscribe to keep importing and editing.`,
    payment: `Your last payment did not go through. Full access continues until ${billingDay(billing.ends_at)}.`,
    read_only:
      'Your journal is read-only. You can look at everything and download it; importing and editing are paused.',
  }[kind]
  return (
    <div
      role="status"
      className={cx(
        'flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 border-b px-4 py-2.5 text-center sm:px-8',
        BANNER_TONE[kind],
      )}
    >
      <span>{text}</span>
      <Link to="/settings/plan" className="font-semibold text-ink">
        {kind === 'payment' ? 'Update payment method' : 'Subscribe'}
      </Link>
      {kind === 'read_only' ? null : (
        <button
          type="button"
          aria-label="Hide until tomorrow"
          title="Hide until tomorrow"
          onClick={() => {
            hideBannerToday(kind)
            setHidden((n) => n + 1)
          }}
          className="inline-flex size-7 cursor-pointer items-center justify-center rounded-md border-0 bg-transparent p-0 text-current hover:bg-raised"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            aria-hidden="true"
          >
            <path d="M2 2l8 8M10 2l-8 8" />
          </svg>
        </button>
      )}
    </div>
  )
}

export function ThemeButton({ theme }: { theme: Theme }) {
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

export function AccountMenu({ email, operator = false }: { email: string; operator?: boolean }) {
  const [leaving, setLeaving] = useState(false)
  const [failed, setFailed] = useState(false)
  const leave = async () => {
    setLeaving(true)
    setFailed(false)
    try {
      go.to(await signOut()) // the identity provider's logout page, so its own session ends too
    } catch {
      setFailed(true) // still signed in: say so rather than pretend
      setLeaving(false)
    }
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
          <Link to="/help" className="px-2 py-1 text-[13px]">
            Help
          </Link>
          {operator ? (
            <Link to="/admin" className="px-2 py-1 text-[13px]">
              Operator tools
            </Link>
          ) : null}
          <Button onClick={leave} disabled={leaving}>
            Sign out
          </Button>
          {failed ? (
            <div role="alert" className="px-2 text-xs text-neg">
              Could not sign you out. Try again.
            </div>
          ) : null}
        </div>
      )}
    </Popover>
  )
}

/** Settings that are real (saved to the account through the API) and so need no sample-data warning. */
const REAL_PAGES = /^\/settings\/(?:preferences|plan|security|privacy)(?:\/|$)/

/** Says out loud that the numbers are made up, on every page that shows them, while a sample source is in use. */
function SampleDataBanner() {
  const label = useDataSource().label
  const { pathname } = useLocation()
  if (label === null || REAL_PAGES.test(pathname)) return null
  if (isDemo())
    return (
      <div
        role="note"
        className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-warn-line bg-panel px-4 py-1.5 text-center text-xs text-warn sm:px-8"
      >
        <span>
          <strong>Demo.</strong> You are looking at sample data: none of it is yours, and nothing you change is kept.
        </span>
        <button
          type="button"
          onClick={exitDemo}
          className="cursor-pointer border-0 bg-transparent p-0 font-[inherit] text-xs font-semibold text-warn underline"
        >
          Back to my own data
        </button>
      </div>
    )
  return (
    <div role="note" className="border-b border-warn-line bg-panel px-4 py-1.5 text-center text-xs text-warn sm:px-8">
      <strong>{label}.</strong> These numbers are made up so the screens can be tried, and changes you make are not
      kept. Your own data arrives with the next releases.
    </div>
  )
}

/**
 * The frame of every signed-in page. `focused` is for a flow that should hold the person's attention (the first-run
 * setup): just the name, one link (`action`) and the account menu, with no navigation and no sample-data banner.
 */
export function AppShell({
  children,
  focused = false,
  action,
}: {
  children: ReactNode
  focused?: boolean
  action?: ReactNode
}) {
  const session = useSession()
  const billing = useBilling().data ?? null // null while billing is off, or could not be read: the shell as before
  const search = useSearch({ strict: false })
  const theme = session.data?.settings.theme
  useEffect(() => {
    if (theme) applyTheme(theme) // the account's choice wins over what this browser remembered
  }, [theme])

  if (session.isPending) return <Spinner label="Loading" />
  if (session.isError) return <p className="p-6">Redirecting to sign in…</p>
  if (focused) {
    return (
      <div className="min-h-screen bg-bg text-ink">
        <header className="border-b border-line bg-panel">
          <div className="mx-auto flex min-h-14 max-w-[1100px] items-center gap-4 px-4 sm:px-8">
            <div className="flex grow items-center gap-2.5 text-base font-semibold">
              <BiteMark size={26} />
              Roundturn
            </div>
            {action}
            <AccountMenu email={session.data.user.email} />
          </div>
        </header>
        <main className="mx-auto flex max-w-[1100px] flex-col gap-4 px-4 pt-8 pb-14 sm:px-8 sm:pt-10">{children}</main>
      </div>
    )
  }
  const filters = filtersOf(parseFilters(search as Record<string, unknown>))
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex min-h-14 max-w-[1360px] flex-wrap items-center gap-x-6 gap-y-1 px-4 sm:px-8">
          <div className="flex items-center gap-2.5 text-base font-semibold">
            <BiteMark size={26} />
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
                activeOptions={{ exact: item.exact, includeSearch: false }}
                className="flex h-12 items-center border-b-2 border-transparent px-3.5 whitespace-nowrap text-muted no-underline hover:text-ink data-[status=active]:border-accent data-[status=active]:text-ink sm:h-14"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 sm:ml-0">
            <PlanChip plan={session.data.plan} billing={billing} />
            <ThemeButton theme={session.data.settings.theme} />
            <AccountMenu email={session.data.user.email} operator={session.data.flags.is_admin} />
          </div>
        </div>
      </header>
      <BillingBanner billing={billing} />
      <SampleDataBanner />
      <main className="mx-auto flex max-w-[1360px] flex-col gap-4 px-4 pt-6 pb-12 sm:px-8">{children}</main>
    </div>
  )
}
