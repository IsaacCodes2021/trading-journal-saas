import { useQuery } from '@tanstack/react-query'
import { createRootRoute, createRoute, createRouter, Link, Outlet } from '@tanstack/react-router'
import { type ChangeEvent, lazy, Suspense, useState } from 'react'
import { SafeHtml } from './components/SafeHtml'
import { api, csrfHeader, unwrap } from './lib/api/client'
import { formatMoney, toChartNumber } from './lib/money'
import { useSession } from './lib/session'

// The chart (ECharts) and the editor (Tiptap) are the two heavy dependencies. They are loaded only on the pages
// that use them, so they stay out of the initial bundle.
const DailyChart = lazy(() => import('./components/Chart'))
const NotesEditor = lazy(() => import('./components/NotesEditor'))

// ---- search params: filters live in the URL (replaces the global filter singleton) ----------------------------

const PRESETS = ['7d', '30d', 'mtd', 'all'] as const
export type Preset = (typeof PRESETS)[number]
const DIRECTIONS = ['all', 'long', 'short'] as const
export type Direction = (typeof DIRECTIONS)[number]

function parsePreset(value: unknown): Preset {
  return PRESETS.find((p) => p === value) ?? 'all'
}
function parseDirection(value: unknown): Direction {
  return DIRECTIONS.find((d) => d === value) ?? 'all'
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
    <div className="mx-auto max-w-4xl p-6">
      <header className="mb-6 flex items-center gap-4 border-b pb-3">
        <nav className="flex gap-4" aria-label="Main">
          <Link to="/" search={{ preset: 'all' }} className="font-semibold">
            Dashboard
          </Link>
          <Link to="/trades" search={{ preset: 'all', direction: 'all' }} className="font-semibold">
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

// ---- dashboard -------------------------------------------------------------------------------------------------

function PresetPicker({ current }: { current: Preset }) {
  return (
    <div className="mb-4 flex gap-2" role="group" aria-label="Date range">
      {PRESETS.map((p) => (
        <Link
          key={p}
          to="/"
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
  const summary = useQuery({
    queryKey: ['summary', preset],
    queryFn: () => unwrap(api.GET('/api/v1/stats/summary', { params: { query: { preset } } })),
  })
  const daily = useQuery({
    queryKey: ['daily', preset],
    queryFn: () => unwrap(api.GET('/api/v1/stats/daily', { params: { query: { preset } } })),
  })
  return (
    <section>
      <h1 className="mb-3 text-xl font-bold">Dashboard</h1>
      <PresetPicker current={preset} />
      {summary.data && (
        <dl className="mb-4 grid grid-cols-3 gap-3">
          <div>
            <dt className="text-sm">Net P&amp;L</dt>
            <dd className="text-lg font-bold" data-testid="net-pnl">
              {formatMoney(summary.data.net_pnl)}
            </dd>
          </div>
          <div>
            <dt className="text-sm">Trades</dt>
            <dd className="text-lg font-bold">{summary.data.trades}</dd>
          </div>
          <div>
            <dt className="text-sm">Win rate</dt>
            <dd className="text-lg font-bold">{Math.round(summary.data.win_rate * 100)}%</dd>
          </div>
        </dl>
      )}
      {daily.data && (
        <Suspense fallback={<p>Loading chart…</p>}>
          <DailyChart
            points={daily.data.map((d) => ({
              label: d.trade_date,
              value: toChartNumber(d.net_pnl),
              display: formatMoney(d.net_pnl),
            }))}
          />
        </Suspense>
      )}
    </section>
  )
}

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  validateSearch: (search: Record<string, unknown>): { preset?: Preset } => ({ preset: parsePreset(search['preset']) }),
  component: Dashboard,
})

// ---- trade log -----------------------------------------------------------------------------------------------

function TradeLog() {
  const search = tradesRoute.useSearch()
  const preset = search.preset ?? 'all'
  const direction = search.direction ?? 'all'
  const trades = useQuery({
    queryKey: ['trades', preset, direction],
    queryFn: () => unwrap(api.GET('/api/v1/trades', { params: { query: { preset, direction } } })),
  })
  return (
    <section>
      <h1 className="mb-3 text-xl font-bold">Trade log</h1>
      <div className="mb-4 flex gap-2" role="group" aria-label="Direction">
        {DIRECTIONS.map((d) => (
          <Link key={d} to="/trades" search={{ preset, direction: d }} className="rounded border px-2 py-1 text-sm">
            {d}
          </Link>
        ))}
      </div>
      {trades.data && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Trades</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Symbol</th>
              <th scope="col">Direction</th>
              <th scope="col">Net P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {trades.data.items.map((t) => (
              <tr key={t.id}>
                <td>{t.trade_date}</td>
                <td>
                  <Link to="/trades/$tradeId" params={{ tradeId: String(t.id) }}>
                    {t.symbol}
                  </Link>
                </td>
                <td>{t.direction}</td>
                <td>{formatMoney(t.net_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

const tradesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/trades',
  validateSearch: (search: Record<string, unknown>): { preset?: Preset; direction?: Direction } => ({
    preset: parsePreset(search['preset']),
    direction: parseDirection(search['direction']),
  }),
  component: TradeLog,
})

// ---- trade detail: notes editor + screenshot upload --------------------------------------------------------------

function Screenshots({ tradeId }: { tradeId: number }) {
  const [message, setMessage] = useState('')
  const onPick = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    const { data } = await api.POST('/api/v1/trades/{trade_id}/screenshots', {
      params: { path: { trade_id: tradeId } },
      body: { file: '' }, // placeholder: the real file is attached by the serializer below
      bodySerializer: () => {
        const form = new FormData()
        form.append('file', file)
        return form
      },
    })
    setMessage(data ? `Uploaded ${data.filename} (${data.size} bytes)` : 'Upload failed')
  }
  return (
    <div className="mt-4">
      <label className="text-sm">
        Screenshot <input type="file" accept="image/*" onChange={onPick} data-testid="file-input" />
      </label>
      <p className="text-sm" data-testid="upload-status">
        {message}
      </p>
    </div>
  )
}

function TradeDetail() {
  const { tradeId } = tradeRoute.useParams()
  const id = Number(tradeId)
  const trade = useQuery({
    queryKey: ['trade', id],
    queryFn: () => unwrap(api.GET('/api/v1/trades/{trade_id}', { params: { path: { trade_id: id } } })),
  })
  if (trade.isPending) return <p>Loading…</p>
  if (trade.isError) return <p role="alert">Trade not found.</p>
  return (
    <section>
      <h1 className="mb-1 text-xl font-bold">
        {trade.data.symbol} {trade.data.direction}
      </h1>
      <p className="mb-3 text-sm">
        {trade.data.trade_date} · {formatMoney(trade.data.net_pnl)}
      </p>
      <Suspense fallback={<p>Loading editor…</p>}>
        <NotesEditor tradeId={id} initial={trade.data.notes} />
      </Suspense>
      <h2 className="mt-4 text-sm font-semibold">Saved notes (rendered through SafeHtml)</h2>
      <SafeHtml html={trade.data.notes} className="rounded border p-2 text-sm" />
      <Screenshots tradeId={id} />
    </section>
  )
}

const tradeRoute = createRoute({ getParentRoute: () => rootRoute, path: '/trades/$tradeId', component: TradeDetail })

// ---- router ------------------------------------------------------------------------------------------------------

const routeTree = rootRoute.addChildren([dashboardRoute, tradesRoute, tradeRoute])
export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
