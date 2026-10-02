import { createRootRoute, createRoute, createRouter, Link, Outlet } from '@tanstack/react-router'
import { csrfHeader } from './lib/api/client'
import { useSession } from './lib/session'

// ---- search params: filters live in the URL (replaces the old global filter singleton, doc 03 section 4) ------------

const PRESETS = ['7d', '30d', 'mtd', 'all'] as const
export type Preset = (typeof PRESETS)[number]

function parsePreset(value: unknown): Preset {
  return PRESETS.find((p) => p === value) ?? 'all'
}

// ---- layout ------------------------------------------------------------------------------------------------------

function Layout() {
  const session = useSession()
  const signOut = async () => {
    await fetch('/auth/logout', { method: 'POST', headers: csrfHeader() })
    window.location.assign('/')
  }
  if (session.isPending) return <p className="p-6">Loading…</p>
  if (session.isError) return <p className="p-6">Redirecting to sign in…</p>
  return (
    <div className="mx-auto max-w-5xl p-6">
      <header className="mb-6 flex items-center gap-4 border-b pb-3">
        <nav className="flex gap-4" aria-label="Main">
          <Link to="/" search={{ preset: 'all' }} className="font-semibold">
            Dashboard
          </Link>
          <Link to="/trades" search={{ preset: 'all' }} className="font-semibold">
            Trade log
          </Link>
        </nav>
        <span className="ml-auto text-sm" data-testid="who">
          {session.data.user.email}
        </span>
        <button type="button" onClick={signOut} className="rounded border px-2 py-1 text-sm">
          Sign out
        </button>
      </header>
      <Outlet />
    </div>
  )
}

const rootRoute = createRootRoute({ component: Layout })

// ---- pages (placeholders: the real ones are rebuilt in M4, see doc 07 section 5) --------------------------------------

function PresetPicker({ to, current }: { to: '/' | '/trades'; current: Preset }) {
  return (
    <div className="mb-4 flex gap-2" role="group" aria-label="Date range">
      {PRESETS.map((p) => (
        <Link
          key={p}
          to={to}
          search={{ preset: p }}
          className={`rounded border px-2 py-1 text-sm ${p === current ? 'font-bold' : ''}`}
        >
          {p}
        </Link>
      ))}
    </div>
  )
}

function Dashboard() {
  const preset = dashboardRoute.useSearch().preset ?? 'all'
  return (
    <section>
      <h1 className="mb-3 text-xl font-bold">Dashboard</h1>
      <PresetPicker to="/" current={preset} />
      <p data-testid="preset">Range: {preset}</p>
    </section>
  )
}

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  validateSearch: (search: Record<string, unknown>): { preset?: Preset } => ({ preset: parsePreset(search['preset']) }),
  component: Dashboard,
})

function TradeLog() {
  const preset = tradesRoute.useSearch().preset ?? 'all'
  return (
    <section>
      <h1 className="mb-3 text-xl font-bold">Trade log</h1>
      <PresetPicker to="/trades" current={preset} />
      <p data-testid="preset">Range: {preset}</p>
    </section>
  )
}

const tradesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/trades',
  validateSearch: (search: Record<string, unknown>): { preset?: Preset } => ({ preset: parsePreset(search['preset']) }),
  component: TradeLog,
})

// ---- router ------------------------------------------------------------------------------------------------------

const routeTree = rootRoute.addChildren([dashboardRoute, tradesRoute])
export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
