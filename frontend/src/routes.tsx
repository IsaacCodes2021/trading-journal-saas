import { lazy, Suspense } from 'react'
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  useNavigate,
  type RouterHistory,
} from '@tanstack/react-router'
import { AppShell } from './components/AppShell'
import { Card, Spinner } from './components/ui/primitives'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { TradeLogPage } from './features/trades/TradeLogPage'
import { parseTradeSearch, pickTradeSearch } from './features/trades/search'
import { parseFilters, pickFilters } from './lib/filters'
import { useSession } from './lib/session'

// Screens other than the landing ones load on demand, so the first page stays small (the budget is in check-bundle).
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

// ---- screens that are designed but not built yet ---------------------------------------------------------------

function NextUp({ title, children }: { title: string; children: string }) {
  return (
    <Card className="mx-auto mt-8 max-w-xl p-8 text-center" role="status">
      <h1 className="m-0 text-xl font-semibold">{title}</h1>
      <p className="mt-2 text-muted">{children}</p>
      <Link to="/" className="mt-4 inline-block">
        Back to the dashboard
      </Link>
    </Card>
  )
}

const importRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/import',
  validateSearch: pickFilters,
  component: () => (
    <NextUp title="Import">
      Uploading a Tradovate export, previewing it and undoing an import are built after the trade screens.
    </NextUp>
  ),
})

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  validateSearch: pickFilters,
  component: () => (
    <NextUp title="Settings">
      Accounts, fees, prop firm rules, tags and preferences are built after the import screen.
    </NextUp>
  ),
})

// ---- router ----------------------------------------------------------------------------------------------------

const routeTree = rootRoute.addChildren([dashboardRoute, tradesRoute, tradeDetailRoute, importRoute, settingsRoute])

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
