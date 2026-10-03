import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DataSourceProvider } from '../../lib/data/source'
import type { DataSource } from '../../lib/data/model'
import { demoSource } from '../../lib/demo/source'
import { formatAmount, formatPercent, formatPnl, formatRatio } from '../../lib/format'
import type { Filters } from '../../lib/filters'
import { DashboardPage } from './DashboardPage'

function show(filters: Filters, source: DataSource = demoSource, onFilters = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DataSourceProvider source={source}>
        <DashboardPage filters={filters} onFilters={onFilters} />
      </DataSourceProvider>
    </QueryClientProvider>,
  )
  return { onFilters, user: userEvent.setup() }
}

describe('the dashboard', () => {
  it('shows the figures the data source gave, formatted exactly', async () => {
    const data = await demoSource.dashboard({ preset: 'all' })
    const s = data.summary
    show({ preset: 'all' })

    const tiles = await screen.findByRole('region', { name: 'Key statistics' })
    expect(within(tiles).getByText('Net P&L').parentElement).toHaveTextContent(formatPnl(s.net_pnl))
    expect(within(tiles).getByText('Net P&L').parentElement).toHaveTextContent(`after ${formatAmount(s.fees)} fees`)
    expect(within(tiles).getByText('Win rate').parentElement).toHaveTextContent(formatPercent(s.win_rate ?? 0))
    expect(within(tiles).getByText('Win rate').parentElement).toHaveTextContent(`${s.win_count} W · ${s.loss_count} L`)
    expect(within(tiles).getByText('Profit factor').parentElement).toHaveTextContent(formatRatio(s.profit_factor))
    expect(within(tiles).getByText('Expectancy').parentElement).toHaveTextContent(formatPnl(s.expectancy ?? '0'))
    expect(within(tiles).getByText('Max drawdown').parentElement).toHaveTextContent(formatPnl(`-${s.max_drawdown}`))
  })

  it('summarizes the selection beside the filters', async () => {
    show({ preset: 'all' })
    expect(await screen.findByText('132 trades · 4 accounts · Sep 3 to Oct 2')).toBeInTheDocument()
  })

  it('describes every chart in words, for people who cannot see it', async () => {
    show({ preset: 'all' })
    const equity = await screen.findByRole('img', {
      name: /^Equity curve: [+−]\$[\d,.]+ cumulative net over 132 trades/,
    })
    expect(equity).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: /^Drawdown chart, deepest point \$[\d,.]+ below peak$/ }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: /^Radar chart of 6 score components: Win rate \d+, Profit factor \d+/ }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('img', {
        name: /^Bar chart of daily profit and loss over 22 trading days\. Best day .*Worst day /,
      }),
    ).toBeInTheDocument()
  })

  it('switches the equity curve between trades and days', async () => {
    const { user } = show({ preset: 'all' })
    await screen.findByRole('img', { name: /over 132 trades/ })
    await user.click(screen.getByRole('button', { name: 'By day' }))
    expect(screen.getByRole('button', { name: 'By day' })).toHaveAttribute('aria-pressed', 'true')
    expect(await screen.findByRole('img', { name: /over 22 days/ })).toBeInTheDocument()
  })

  it('explains how the score is worked out, one rule per component, on request', async () => {
    const { user } = show({ preset: 'all' })
    const card = await screen.findByRole('region', { name: 'Trader score' })
    const button = within(card).getByRole('button', { name: /How it.s scored/ })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(within(card).queryByRole('term')).not.toBeInTheDocument()

    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(
      within(card)
        .getAllByRole('term')
        .map((t) => t.textContent),
    ).toEqual(['Win rate', 'Profit factor', 'Avg win/loss', 'Consistency', 'Max drawdown', 'Recovery factor'])
    expect(within(card).getAllByRole('definition')[0]).toHaveTextContent('60% or higher scores 100')

    await user.click(button)
    expect(within(card).queryByRole('term')).not.toBeInTheDocument()
  })

  it('shows the daily results as a table, newest day first', async () => {
    const { user } = show({ preset: 'all' })
    const card = await screen.findByRole('region', { name: 'Daily P&L' })
    await user.click(within(card).getByRole('button', { name: 'Table' }))
    const rows = within(card).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(22)
    expect(rows[0]).toHaveTextContent('Oct 2')
    expect(rows[21]).toHaveTextContent('Sep 3')
  })

  it('lists the accounts, best first', async () => {
    const data = await demoSource.dashboard({ preset: 'all' })
    show({ preset: 'all' })
    const card = await screen.findByRole('region', { name: 'By account' })
    const rows = within(card).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(data.accounts.length)
    expect(rows[0]).toHaveTextContent(data.accounts[0]?.account.name ?? '?')
    expect(rows[0]).toHaveTextContent(formatPnl(data.accounts[0]?.net_pnl ?? '0'))
  })

  it('offers a way out when the filters match nothing', async () => {
    const { onFilters, user } = show({ preset: '30d', symbol_roots: ['NOPE'] })
    const empty = (await screen.findByText('No trades match these filters')).closest('[role="status"]') as HTMLElement
    expect(empty).toHaveTextContent('Widen the date range')
    expect(screen.queryByRole('region', { name: 'Key statistics' })).not.toBeInTheDocument()
    await user.click(within(empty).getByRole('button', { name: 'Show everything' }))
    expect(onFilters).toHaveBeenCalledWith({ preset: 'all' })
  })

  it('shows a loading message first, then the numbers', async () => {
    show({ preset: 'all' })
    expect(screen.getByRole('status')).toHaveTextContent('Loading the dashboard')
    await screen.findByRole('region', { name: 'Key statistics' })
    expect(screen.queryByText('Loading the dashboard…')).not.toBeInTheDocument()
  })

  it('says when it failed, and tries again on request', async () => {
    let calls = 0
    const flaky: DataSource = {
      ...demoSource,
      dashboard: (filters, signal) => {
        calls += 1
        return calls === 1 ? Promise.reject(new Error('down')) : demoSource.dashboard(filters, signal)
      },
    }
    const { user } = show({ preset: 'all' }, flaky)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The dashboard could not be loaded.')
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Key statistics' })).toBeInTheDocument())
    expect(calls).toBe(2)
  })
})
