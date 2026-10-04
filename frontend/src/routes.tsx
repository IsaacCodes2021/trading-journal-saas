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
import { Spinner } from './components/ui/primitives'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { TradeLogPage } from './features/trades/TradeLogPage'
import { parseTradeSearch, pickTradeSearch } from './features/trades/search'
import { isTab, type Tab } from './features/accounts/tabs'
import { parseFilters, pickFilters } from './lib/filters'
import { parseStep, type StepNumber } from './features/onboarding/steps'
import { signInErrorOf, type SignInErrorCode } from './lib/auth'
import { useSession } from './lib/session'

// Screens other than the landing ones load on demand, so the first page stays small (the budget is in check-bundle).
const lazyNamed = <T extends Record<string, ComponentType<never>>, K extends keyof T>(
  load: () => Promise<T>,
  name: K,
) => lazy(() => load().then((m) => ({ default: m[name] as unknown as ComponentType })))

const InvitePage = lazy(() => import('./features/invite/InvitePage').then((m) => ({ default: m.InvitePage })))
const OnboardingPage = lazy(() =>
  import('./features/onboarding/OnboardingPage').then((m) => ({ default: m.OnboardingPage })),
)
const ImportPage = lazy(() => import('./features/imports/ImportPage').then((m) => ({ default: m.ImportPage })))
const TradeDetailPage = lazy(() =>
  import('./features/trade-detail/TradeDetailPage').then((m) => ({ default: m.TradeDetailPage })),
)

/*
 * The route tree. Each page's state lives in the URL (filters, search text, sort, page), so a view survives a reload and
 * can be shared. A route keeps only the valid keys it finds (`pick…`); the page applies defaults (`parse…`), so a link
 * can name just the keys it cares about.
 */

/** Pages that work before sign-in: no session is asked for, and the app's header and menu are not shown. */
const PUBLIC_PAGES = /^\/invite(?:\/|$)/

/** The first-run setup keeps the person's attention: a small header with one way out, and no navigation. */
const FOCUSED_PAGES = /^\/welcome(?:\/|$)/

function Root() {
  const { pathname } = useLocation()
  if (PUBLIC_PAGES.test(pathname)) return <Outlet />
  if (FOCUSED_PAGES.test(pathname)) {
    return (
      <AppShell
        focused
        action={
          <Link to="/" className="text-muted">
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

// ---- invite (before sign-in) -------------------------------------------------------------------------------------

const inviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/invite',
  // An invite email links here with the code in the address: /invite?code=TJ-7KQ2-M9XA. A sign-in that did not complete
  // comes back as /invite?error=<code>; only the server's own codes are kept, so an edited address shows nothing.
  validateSearch: (search: Record<string, unknown>): { code?: string; error?: SignInErrorCode } => {
    const code = typeof search['code'] === 'string' && search['code'] !== '' ? search['code'].slice(0, 64) : null
    const error = signInErrorOf(search['error'])
    return { ...(code ? { code } : {}), ...(error ? { error } : {}) }
  },
  component: function InviteRoute() {
    const { code, error } = inviteRoute.useSearch()
    return (
      <Suspense fallback={<Spinner label="Loading" />}>
        <InvitePage {...(code ? { code } : {})} {...(error ? { error } : {})} />
      </Suspense>
    )
  },
})

// ---- settings --------------------------------------------------------------------------------------------------

const SettingsLayout = lazyNamed(() => import('./features/settings/SettingsLayout'), 'SettingsLayout')
const PreferencesPage = lazyNamed(() => import('./features/settings/PreferencesPage'), 'PreferencesPage')
const PlanPage = lazyNamed(() => import('./features/settings/PlanPage'), 'PlanPage')
const AccountsPage = lazy(() => import('./features/accounts/AccountsPage').then((m) => ({ default: m.AccountsPage })))
const TagsPage = lazyNamed(() => import('./features/tags/TagsPage'), 'TagsPage')
const SecurityPage = lazyNamed(() => import('./features/settings/SecurityPage'), 'SecurityPage')
const PrivacyPage = lazyNamed(() => import('./features/settings/PrivacyPage'), 'PrivacyPage')

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
  const { account, tab } = accountsRoute.useSearch()
  const navigate = useNavigate({ from: '/settings/accounts' })
  return (
    <Suspense fallback={<Spinner label="Loading" />}>
      <AccountsPage
        selected={account ?? null}
        tab={tab ?? 'details'}
        onSelect={(id, next) =>
          void navigate({
            search: (prev) => {
              const rest = Object.fromEntries(
                Object.entries(prev).filter(([key]) => key !== 'account' && key !== 'tab'),
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
  // The picked account and its tab live in the address, so a view can be shared and survives a reload.
  validateSearch: (search: Record<string, unknown>): { account?: number; tab?: Tab } => {
    const id = Number(search['account'])
    return {
      ...(Number.isInteger(id) && id > 0 ? { account: id } : {}),
      ...(isTab(search['tab']) ? { tab: search['tab'] } : {}),
    }
  },
  component: AccountsRoute,
})
const tagsRoute = createRoute({ getParentRoute: () => settingsRoute, path: 'tags', component: inSettings(TagsPage) })
const planRoute = createRoute({ getParentRoute: () => settingsRoute, path: 'plan', component: inSettings(PlanPage) })
const securityRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'security',
  component: inSettings(SecurityPage),
})
const privacyRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'privacy',
  component: inSettings(PrivacyPage),
})

// ---- router ----------------------------------------------------------------------------------------------------

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  tradesRoute,
  tradeDetailRoute,
  importRoute,
  welcomeRoute,
  inviteRoute,
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
  return createRouter({ routeTree, ...(history ? { history } : {}) })
}

export const router = createAppRouter()

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
