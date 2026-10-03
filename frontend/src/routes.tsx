import { lazy, Suspense, type ComponentType } from 'react'
import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useNavigate,
  type RouterHistory,
} from '@tanstack/react-router'
import { AppShell } from './components/AppShell'
import { Spinner } from './components/ui/primitives'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { TradeLogPage } from './features/trades/TradeLogPage'
import { parseTradeSearch, pickTradeSearch } from './features/trades/search'
import { parseFilters, pickFilters } from './lib/filters'
import { useSession } from './lib/session'

// Screens other than the landing ones load on demand, so the first page stays small (the budget is in check-bundle).
const lazyNamed = <T extends Record<string, ComponentType<never>>, K extends keyof T>(
  load: () => Promise<T>,
  name: K,
) => lazy(() => load().then((m) => ({ default: m[name] as unknown as ComponentType })))

const ImportPage = lazy(() => import('./features/imports/ImportPage').then((m) => ({ default: m.ImportPage })))
const TradeDetailPage = lazy(() =>
  import('./features/trade-detail/TradeDetailPage').then((m) => ({ default: m.TradeDetailPage })),
)

/*
 * The route tree. Each page's state lives in the URL (filters, search text, sort, page), so a view survives a reload and
 * can be shared. A route keeps only the valid keys it finds (`pick…`); the page applies defaults (`parse…`), so a link
 * can name just the keys it cares about.
 */

const rootRoute = createRootRoute({
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
})

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
  return (
    <Suspense fallback={<Spinner label="Loading the import page" />}>
      <ImportPage timeZone={session.data?.settings.timezone ?? 'UTC'} />
    </Suspense>
  )
}

const importRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/import',
  validateSearch: pickFilters,
  component: ImportRoute,
})

// ---- settings --------------------------------------------------------------------------------------------------

const SettingsLayout = lazyNamed(() => import('./features/settings/SettingsLayout'), 'SettingsLayout')
const PreferencesPage = lazyNamed(() => import('./features/settings/PreferencesPage'), 'PreferencesPage')
const PlanPage = lazyNamed(() => import('./features/settings/PlanPage'), 'PlanPage')
const AccountsSoon = lazyNamed(() => import('./features/settings/Placeholders'), 'AccountsSoon')
const TagsSoon = lazyNamed(() => import('./features/settings/Placeholders'), 'TagsSoon')
const PrivacySoon = lazyNamed(() => import('./features/settings/Placeholders'), 'PrivacySoon')

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
const accountsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'accounts',
  component: inSettings(AccountsSoon),
})
const tagsRoute = createRoute({ getParentRoute: () => settingsRoute, path: 'tags', component: inSettings(TagsSoon) })
const planRoute = createRoute({ getParentRoute: () => settingsRoute, path: 'plan', component: inSettings(PlanPage) })
const privacyRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: 'privacy',
  component: inSettings(PrivacySoon),
})

// ---- router ----------------------------------------------------------------------------------------------------

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  tradesRoute,
  tradeDetailRoute,
  importRoute,
  settingsRoute.addChildren([settingsIndexRoute, preferencesRoute, accountsRoute, tagsRoute, planRoute, privacyRoute]),
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
