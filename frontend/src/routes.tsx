import { lazy, Suspense, type ComponentType } from 'react'
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  redirect,
  useLocation,
  useNavigate,
  type RouterHistory,
} from '@tanstack/react-router'
import { AppShell } from './components/AppShell'
import { NotFound, RouteError } from './components/RouteError'
import { Spinner } from './components/ui/primitives'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { TradeLogPage } from './features/trades/TradeLogPage'
import { parseTradeSearch, pickTradeSearch } from './features/trades/search'
import { isTab, type Tab } from './features/accounts/tabs'
import { isDay, parseMonth } from './lib/calendar'
import type { ReportBy } from './lib/data/model'
import { parseBy } from './lib/reports'
import { parseFilters, pickFilters, type Filters } from './lib/filters'
import { parseStep, type StepNumber } from './features/onboarding/steps'
import { deletionDay, signInErrorOf, type SignInErrorCode } from './lib/auth'
import { safeNext } from './lib/nextPath'
import { useSession } from './lib/session'
import { pickAdminSearch, queryOf, searchOf } from './features/admin/search'
import { pickCodesSearch } from './features/admin/codesSearch'
import { betaQueryOf, betaSearchOf, pickBetaSearch } from './features/admin/betaSearch'
import { pickRulesSearch, rulesQueryOf, rulesSearchOf } from './features/admin/rulesSearch'

// Screens other than the landing ones load on demand, so the first page stays small (the budget is in check-bundle).
const lazyNamed = <T extends Record<string, ComponentType<never>>, K extends keyof T>(
  load: () => Promise<T>,
  name: K,
) => lazy(() => load().then((m) => ({ default: m[name] as unknown as ComponentType })))

const AuthPage = lazy(() => import('./features/auth/AuthPage').then((m) => ({ default: m.AuthPage })))
const JoinPage = lazy(() => import('./features/auth/JoinPage').then((m) => ({ default: m.JoinPage })))
const AdminShell = lazy(() => import('./features/admin/AdminShell').then((m) => ({ default: m.AdminShell })))
const CodesPage = lazy(() => import('./features/admin/CodesPage').then((m) => ({ default: m.CodesPage })))
const UsersPage = lazy(() => import('./features/admin/UsersPage').then((m) => ({ default: m.UsersPage })))
const BetaPage = lazy(() => import('./features/admin/BetaPage').then((m) => ({ default: m.BetaPage })))
const RuleTemplatesPage = lazy(() =>
  import('./features/admin/RuleTemplatesPage').then((m) => ({ default: m.RuleTemplatesPage })),
)
const OnboardingPage = lazy(() =>
  import('./features/onboarding/OnboardingPage').then((m) => ({ default: m.OnboardingPage })),
)
const ImportPage = lazy(() => import('./features/imports/ImportPage').then((m) => ({ default: m.ImportPage })))
const TradeDetailPage = lazy(() =>
  import('./features/trade-detail/TradeDetailPage').then((m) => ({ default: m.TradeDetailPage })),
)
const CalendarPage = lazy(() => import('./features/calendar/CalendarPage').then((m) => ({ default: m.CalendarPage })))
const DayPage = lazy(() => import('./features/calendar/DayPage').then((m) => ({ default: m.DayPage })))
const HelpPage = lazy(() => import('./features/help/HelpPage').then((m) => ({ default: m.HelpPage })))
const ReportsPage = lazy(() => import('./features/reports/ReportsPage').then((m) => ({ default: m.ReportsPage })))
const AccountsOverviewPage = lazy(() =>
  import('./features/overview/AccountsOverviewPage').then((m) => ({ default: m.AccountsOverviewPage })),
)

/*
 * The route tree. Each page's state lives in the URL (filters, search text, sort, page), so a view survives a reload and
 * can be shared. A route keeps only the valid keys it finds (`pick…`); the page applies defaults (`parse…`), so a link
 * can name just the keys it cares about.
 */

/** Pages that work before sign-in: no session is asked for, and the app's header and menu are not shown. */
const PUBLIC_PAGES = /^\/(?:signin|signup|invite|join)(?:\/|$)/

/** The first-run setup keeps the person's attention: a small header with one way out, and no navigation. */
const FOCUSED_PAGES = /^\/welcome(?:\/|$)/

/** The operator's tools have a frame of their own, so they are never mistaken for the journal. */
const ADMIN_PAGES = /^\/admin(?:\/|$)/

function Root() {
  const { pathname } = useLocation()
  if (PUBLIC_PAGES.test(pathname)) return <Outlet />
  if (ADMIN_PAGES.test(pathname)) {
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <AdminShell>
          <Outlet />
        </AdminShell>
      </Suspense>
    )
  }
  if (FOCUSED_PAGES.test(pathname)) {
    return (
      <AppShell
        focused
        action={
          <Link to="/" className="-my-2 inline-block py-2 text-muted">
            Skip setup for now
          </Link>
        }
      >
        <Outlet />
      </AppShell>
    )
  }
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}

const rootRoute = createRootRoute({ component: Root })

// ---- dashboard -------------------------------------------------------------------------------------------------

function DashboardRoute() {
  const navigate = useNavigate({ from: '/' })
  return (
    <DashboardPage
      filters={parseFilters(dashboardRoute.useSearch())}
      onFilters={(next) => void navigate({ search: next })}
    />
  )
}

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  validateSearch: pickFilters,
  component: DashboardRoute,
})

// ---- trade log -------------------------------------------------------------------------------------------------

function TradesRoute() {
  const navigate = useNavigate({ from: '/trades' })
  const session = useSession()
  return (
    <TradeLogPage
      search={parseTradeSearch(tradesRoute.useSearch())}
      timeZone={session.data?.settings.timezone ?? 'UTC'}
      onSearch={(next) => void navigate({ search: next })}
    />
  )
}

const tradesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/trades',
  validateSearch: pickTradeSearch,
  component: TradesRoute,
})

// ---- trade detail ----------------------------------------------------------------------------------------------

function TradeDetailRoute() {
  const { tradeId } = tradeDetailRoute.useParams()
  const session = useSession()
  return (
    <Suspense fallback={<Spinner label="Loading the trade" />}>
      <TradeDetailPage
        tradeId={/^\d+$/.test(tradeId) ? Number(tradeId) : null}
        search={parseTradeSearch(tradeDetailRoute.useSearch())}
        timeZone={session.data?.settings.timezone ?? 'UTC'}
      />
    </Suspense>
  )
}

const tradeDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/trades/$tradeId',
  validateSearch: pickTradeSearch,
  component: TradeDetailRoute,
})

// ---- calendar and day --------------------------------------------------------------------------------------------

function CalendarRoute() {
  const navigate = useNavigate({ from: '/calendar' })
  const { month: raw, ...rest } = calendarRoute.useSearch()
  const month = parseMonth(raw) // the router keeps what it could not validate too, so the month is checked again
  return (
    <Suspense fallback={<Spinner label="Loading the calendar" />}>
      <CalendarPage
        search={{ ...parseFilters(rest), ...(month ? { month } : {}) }}
        onSearch={(next) => void navigate({ search: next })}
      />
    </Suspense>
  )
}

const calendarRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/calendar',
  // A month that is not a month the app has is dropped, so the page shows the latest one rather than an error.
  validateSearch: (search: Record<string, unknown>): Partial<Filters> & { month?: string } => {
    const month = parseMonth(search['month'])
    return { ...pickFilters(search), ...(month ? { month } : {}) }
  },
  component: CalendarRoute,
})

function DayRoute() {
  const { day } = dayRoute.useParams()
  const navigate = useNavigate({ from: '/day/$day' })
  const session = useSession()
  return (
    <Suspense fallback={<Spinner label="Loading the day" />}>
      <DayPage
        day={isDay(day) ? day : null}
        filters={parseFilters(dayRoute.useSearch())}
        timeZone={session.data?.settings.timezone ?? 'UTC'}
        onFilters={(next) => void navigate({ search: next })}
      />
    </Suspense>
  )
}

const dayRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/day/$day',
  validateSearch: pickFilters,
  component: DayRoute,
})

// ---- reports -----------------------------------------------------------------------------------------------------

function ReportsRoute() {
  const navigate = useNavigate({ from: '/reports' })
  const session = useSession()
  const { by: raw, ...rest } = reportsRoute.useSearch()
  const by = parseBy(raw) // the router keeps what it could not validate too, so the choice is checked again
  return (
    <Suspense fallback={<Spinner label="Loading the reports" />}>
      <ReportsPage
        search={{ ...parseFilters(rest), ...(by ? { by } : {}) }}
        timeZone={session.data?.settings.timezone ?? 'UTC'}
        onSearch={(next) => void navigate({ search: next })}
      />
    </Suspense>
  )
}

const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reports',
  // A choice that is not one of the six is dropped, so the page shows the first (contract) rather than an error.
  validateSearch: (search: Record<string, unknown>): Partial<Filters> & { by?: ReportBy } => {
    const by = parseBy(search['by'])
    return { ...pickFilters(search), ...(by ? { by } : {}) }
  },
  component: ReportsRoute,
})

// ---- help --------------------------------------------------------------------------------------------------------

// ---- the Accounts tab (read-only: every change is made in settings) -------------------------------------------------

const accountsOverviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/accounts',
  component: function AccountsOverviewRoute() {
    return (
      <Suspense fallback={<Spinner label="Loading your accounts" />}>
        <AccountsOverviewPage />
      </Suspense>
    )
  },
})

const helpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/help',
  component: function HelpRoute() {
    return (
      <Suspense fallback={<Spinner label="Loading help" />}>
        <HelpPage />
      </Suspense>
    )
  },
})

// ---- import ----------------------------------------------------------------------------------------------------

function ImportRoute() {
  const session = useSession()
  const settings = session.data?.settings
  return (
    <Suspense fallback={<Spinner label="Loading the import page" />}>
      <ImportPage
        timeZone={settings?.timezone ?? 'UTC'}
        csvTimeZone={settings?.csv_timezone ?? settings?.timezone ?? 'UTC'}
      />
    </Suspense>
  )
}

const importRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/import',
  validateSearch: pickFilters,
  component: ImportRoute,
})

// ---- first-run setup -------------------------------------------------------------------------------------------

const welcomeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/welcome',
  // The step is in the address, so Back and reload work and a step can be linked to.
  validateSearch: (search: Record<string, unknown>): { step?: StepNumber } => {
    const step = parseStep(search['step'])
    return step === 1 ? {} : { step }
  },
  component: function WelcomeRoute() {
    const navigate = useNavigate({ from: '/welcome' })
    const step = parseStep(welcomeRoute.useSearch().step)
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <OnboardingPage step={step} onStep={(next) => void navigate({ search: next === 1 ? {} : { step: next } })} />
      </Suspense>
    )
  },
})

// ---- the way in (before sign-in) ---------------------------------------------------------------------------------

/** What the way-in pages keep from the address. Only values that pass their own checks are kept, so an edited address
 * shows nothing it should not: a code is a short string, an error is one of the server's own codes, a date is a plain ISO
 * time, and `next` is a path on this site. */
type EntrySearch = {
  code?: string
  promo?: string
  error?: SignInErrorCode
  deleted?: string
  next?: string
}

function pickEntry(search: Record<string, unknown>): EntrySearch {
  const code = typeof search['code'] === 'string' && search['code'] !== '' ? search['code'].slice(0, 64) : null
  const promo = typeof search['promo'] === 'string' && search['promo'] !== '' ? search['promo'].slice(0, 40) : null
  const error = signInErrorOf(search['error'])
  const deleted = typeof search['deleted'] === 'string' && deletionDay(search['deleted']) ? search['deleted'] : null
  const next = safeNext(search['next'])
  return {
    ...(code ? { code } : {}),
    ...(promo ? { promo } : {}),
    ...(error ? { error } : {}),
    ...(deleted ? { deleted } : {}),
    ...(next ? { next } : {}),
  }
}

function EntryRoute({ mode, search }: { mode: 'signin' | 'signup'; search: EntrySearch }) {
  const { code, promo, error, deleted, next } = pickEntry(search) // the router keeps what it could not validate too
  return (
    <Suspense fallback={<Spinner label="Loading" />}>
      <AuthPage
        mode={mode}
        {...(code ? { code } : {})}
        {...(promo ? { promo } : {})}
        {...(error ? { error } : {})}
        {...(deleted ? { deleted } : {})}
        {...(next ? { next } : {})}
      />
    </Suspense>
  )
}

const signinRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/signin',
  validateSearch: pickEntry,
  component: function SigninRoute() {
    return <EntryRoute mode="signin" search={signinRoute.useSearch()} />
  },
})

const signupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/signup',
  validateSearch: pickEntry,
  component: function SignupRoute() {
    return <EntryRoute mode="signup" search={signupRoute.useSearch()} />
  },
})

const joinRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/join',
  component: function JoinRoute() {
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <JoinPage />
      </Suspense>
    )
  },
})

// The address the server and the emails already use: an email links here with the code (/invite?code=TJ-7KQ2-M9XA), a
// sign-in that did not complete comes back as /invite?error=<code>, a deleted account lands on /invite?deleted=<time>,
// and signing out ends here. It opens on the sign-up form when there is a code to put in or a code (the sign-up code or
// the promo code) was refused, and on the sign-in form otherwise.
const inviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/invite',
  validateSearch: pickEntry,
  component: function InviteRoute() {
    const search = inviteRoute.useSearch()
    const picked = pickEntry(search)
    const signingUp = picked.code !== undefined || picked.error === 'invite_invalid' || picked.error === 'promo_invalid'
    return <EntryRoute mode={signingUp ? 'signup' : 'signin'} search={search} />
  },
})

// ---- operator ---------------------------------------------------------------------------------------------------

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin',
  validateSearch: pickAdminSearch,
  component: function AdminUsersRoute() {
    const search = pickAdminSearch(adminRoute.useSearch()) // the router keeps what it could not validate too
    const navigate = useNavigate({ from: '/admin' })
    const query = queryOf(search)
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <UsersPage
          query={query}
          selected={search.user ?? null}
          onQuery={(next) => void navigate({ search: searchOf(next, search.user ?? null), replace: true })}
          onSelect={(id) => void navigate({ search: searchOf(query, id) })}
        />
      </Suspense>
    )
  },
})

const adminBetaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin/beta',
  validateSearch: pickBetaSearch,
  component: function AdminBetaRoute() {
    const search = pickBetaSearch(adminBetaRoute.useSearch()) // the router keeps what it could not validate too
    const navigate = useNavigate({ from: '/admin/beta' })
    const query = betaQueryOf(search)
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <BetaPage
          query={query}
          selected={search.person ?? null}
          onQuery={(next) => void navigate({ search: betaSearchOf(next, null), replace: true })}
          onSelect={(id) => void navigate({ search: betaSearchOf(query, id) })}
        />
      </Suspense>
    )
  },
})

const adminCodesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin/codes',
  validateSearch: pickCodesSearch,
  component: function AdminCodesRoute() {
    const search = pickCodesSearch(adminCodesRoute.useSearch()) // the router keeps what it could not validate too
    const navigate = useNavigate({ from: '/admin/codes' })
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <CodesPage search={search} onSearch={(next) => void navigate({ search: next, replace: true })} />
      </Suspense>
    )
  },
})

const adminRulesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin/rules',
  validateSearch: pickRulesSearch,
  component: function AdminRulesRoute() {
    const search = pickRulesSearch(adminRulesRoute.useSearch()) // the router keeps what it could not validate too
    const navigate = useNavigate({ from: '/admin/rules' })
    const query = rulesQueryOf(search)
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <RuleTemplatesPage
          query={query}
          selected={search.template ?? null}
          onQuery={(next) => void navigate({ search: rulesSearchOf(next, search.template ?? null), replace: true })}
          onSelect={(id) => void navigate({ search: rulesSearchOf(query, id) })}
        />
      </Suspense>
    )
  },
})

// ---- settings --------------------------------------------------------------------------------------------------

const SettingsLayout = lazyNamed(() => import('./features/settings/SettingsLayout'), 'SettingsLayout')
const PreferencesPage = lazyNamed(() => import('./features/settings/PreferencesPage'), 'PreferencesPage')
const PlanPage = lazy(() => import('./features/settings/PlanPage').then((m) => ({ default: m.PlanPage })))
const AccountsPage = lazy(() => import('./features/accounts/AccountsPage').then((m) => ({ default: m.AccountsPage })))
const TagsPage = lazyNamed(() => import('./features/tags/TagsPage'), 'TagsPage')
const SecurityPage = lazyNamed(() => import('./features/settings/SecurityPage'), 'SecurityPage')
const PrivacyPage = lazy(() => import('./features/settings/PrivacyPage').then((m) => ({ default: m.PrivacyPage })))

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  validateSearch: pickFilters,
  component: () => (
    <Suspense fallback={<Spinner label="Loading settings" />}>
      <SettingsLayout />
    </Suspense>
  ),
})

/** A settings section as a route component: its page loads on demand, with a short message meanwhile. */
const inSettings = (Page: ComponentType) =>
  function SettingsSection() {
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <Page />
      </Suspense>
    )
  }

const settingsIndexRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/settings/preferences' })
  },
})

const preferencesRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'preferences',
  component: inSettings(PreferencesPage),
})
function AccountsRoute() {
  const { account, tab, setup } = accountsRoute.useSearch()
  const navigate = useNavigate({ from: '/settings/accounts' })
  return (
    <Suspense fallback={<Spinner label="Loading" />}>
      <AccountsPage
        selected={account ?? null}
        tab={tab ?? 'details'}
        setup={setup === 'funded'}
        onSetupDone={() =>
          void navigate({
            search: (prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => key !== 'setup')),
            replace: true,
          })
        }
        onSelect={(id, next) =>
          void navigate({
            search: (prev) => {
              const rest = Object.fromEntries(
                Object.entries(prev).filter(([key]) => key !== 'account' && key !== 'tab' && key !== 'setup'),
              )
              return id === null ? rest : { ...rest, account: id, tab: next ?? prev.tab ?? 'details' }
            },
          })
        }
      />
    </Suspense>
  )
}

const accountsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'accounts',
  // The picked account and its tab live in the address, so a view can be shared and survives a reload. `setup=funded`
  // comes from the Accounts tab: archive the passed evaluation and set up its funded account.
  validateSearch: (search: Record<string, unknown>): { account?: number; tab?: Tab; setup?: 'funded' } => {
    const id = Number(search['account'])
    return {
      ...(Number.isInteger(id) && id > 0 ? { account: id } : {}),
      ...(isTab(search['tab']) ? { tab: search['tab'] } : {}),
      ...(search['setup'] === 'funded' ? { setup: 'funded' as const } : {}),
    }
  },
  component: AccountsRoute,
})
const tagsRoute = createRoute({ getParentRoute: () => settingsRoute, path: 'tags', component: inSettings(TagsPage) })
const planRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'plan',
  // `promo=not_applied`: a new account whose promo code could not be applied at sign-up; the page says so once.
  validateSearch: (search: Record<string, unknown>): { promo?: 'not_applied' } =>
    search['promo'] === 'not_applied' ? { promo: 'not_applied' as const } : {},
  component: function PlanRoute() {
    const search = planRoute.useSearch()
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <PlanPage promoMissed={search.promo === 'not_applied'} />
      </Suspense>
    )
  },
})
const securityRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'security',
  component: inSettings(SecurityPage),
})
const privacyRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'privacy',
  // `delete=1`: open the deletion dialog (where signing in again for it comes back to). `restored=1`: signing in has just
  // cancelled a scheduled deletion, and the page says so.
  validateSearch: (search: Record<string, unknown>): { delete?: true; restored?: true } => ({
    ...(String(search['delete']) === '1' ? { delete: true as const } : {}),
    ...(String(search['restored']) === '1' ? { restored: true as const } : {}),
  }),
  component: function PrivacyRoute() {
    const search = privacyRoute.useSearch()
    const navigate = useNavigate({ from: '/settings/privacy' })
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <PrivacyPage
          deleteOpen={search.delete === true}
          restored={search.restored === true}
          onDeleteClosed={() => void navigate({ search: {}, replace: true })}
        />
      </Suspense>
    )
  },
})

// ---- router ----------------------------------------------------------------------------------------------------

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  tradesRoute,
  tradeDetailRoute,
  calendarRoute,
  reportsRoute,
  accountsOverviewRoute,
  helpRoute,
  dayRoute,
  importRoute,
  welcomeRoute,
  signinRoute,
  signupRoute,
  joinRoute,
  inviteRoute,
  adminRoute,
  adminBetaRoute,
  adminCodesRoute,
  adminRulesRoute,
  settingsRoute.addChildren([
    settingsIndexRoute,
    preferencesRoute,
    accountsRoute,
    tagsRoute,
    planRoute,
    securityRoute,
    privacyRoute,
  ]),
])

/** The app's router. Tests make their own, each with its own history, so no test sees another's location. */
export function createAppRouter(history?: RouterHistory) {
  return createRouter({
    routeTree,
    defaultErrorComponent: RouteError,
    defaultNotFoundComponent: NotFound,
    ...(history ? { history } : {}),
  })
}

export const router = createAppRouter()

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
