import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AccountRow, AccountsOverview } from '../../lib/data/model'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { resetDemoRules } from '../../lib/demo/rules'
import { demoSource } from '../../lib/demo/source'
import { openApp, stubApi } from '../../test-utils'
import { spectrumText } from './AccountsOverviewPage'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoAccounts()
  resetDemoRules()
})

async function open() {
  stubApi()
  const app = openApp('/accounts')
  await screen.findByRole('heading', { name: 'Accounts' })
  const table = await screen.findByRole('table', { name: 'Active accounts' })
  return { ...app, table, user: userEvent.setup() }
}

const rowOf = (table: HTMLElement, name: string) => {
  const header = within(table).getByRole('rowheader', { name: new RegExp(`^${name}`) })
  return header.closest('tr') as HTMLElement
}

describe('the Accounts tab', () => {
  it('is in the main navigation', async () => {
    stubApi()
    openApp('/')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Accounts' }).getAttribute('href')).toMatch(/^\/accounts(\?|$)/)
  })

  it('shows one row per active account, the archived ones left out', async () => {
    const { table } = await open()
    const names = within(table)
      .getAllByRole('rowheader')
      .map((h) => h.textContent?.replace('⋯', '').trim())
    expect(names).toEqual(['Apex 50K #1', 'Apex 150K', 'Lucid 100K', 'Apex 50K #2', 'Totals'])
  })

  it('highlights a payout-ready account in gold and says what it can withdraw', async () => {
    const { table } = await open()
    const ready = rowOf(table, 'Apex 150K')
    expect(ready).toHaveAttribute('data-payout-ready', 'true')
    expect(ready).toHaveClass('bg-gold-bg')
    expect(within(ready).getByText('Payout ready')).toBeInTheDocument() // the status badge
    expect(ready).toHaveTextContent('Payout ready: $3,000.00 gross / $3,000.00 net')
    expect(rowOf(table, 'Lucid 100K')).not.toHaveAttribute('data-payout-ready')
  })

  it('shows each status, and "No rules set" with a way to set them', async () => {
    const { table } = await open()
    expect(within(rowOf(table, 'Apex 50K #1')).getByText('Breached')).toBeInTheDocument()
    expect(within(rowOf(table, 'Lucid 100K')).getByText('OK')).toBeInTheDocument()
    const unruled = rowOf(table, 'Apex 50K #2')
    const links = within(unruled).getAllByRole('link', { name: 'No rules set' })
    expect(links[0]).toHaveAttribute('href', '/settings/accounts?account=4&tab=firm-rules')
  })

  it('draws the breach-to-goal bar with the distances in words', async () => {
    const { table } = await open()
    const bar = within(rowOf(table, 'Lucid 100K')).getByRole('img', { name: /^Breach to goal/ })
    expect(bar).toHaveAccessibleName(
      "Breach to goal: $1,800.00 above the day's loss limit · $3,435.90 to the profit target",
    )
    expect(
      spectrumText({
        breach_level: '48000.00',
        breach_kind: 'drawdown',
        target_level: '53000.00',
        target_kind: 'profit_target',
        balance: '53200.00',
        to_breach: '5200.00',
        to_target: '0.00',
        position: 1,
      }),
    ).toBe('$5,200.00 above the drawdown floor · at the profit target')
  })

  it('adds up the balances, the withdrawable payouts and what was withdrawn', async () => {
    const { table } = await open()
    const totals = within(table).getByRole('rowheader', { name: 'Totals' }).closest('tr') as HTMLElement
    expect(totals).toHaveTextContent('$362,945.60')
    expect(totals).toHaveTextContent('Withdrawable now: $3,000.00 gross / $3,000.00 net')
    expect(totals).toHaveTextContent('Withdrawn so far: $3,150.00 gross / $3,150.00 net')
  })

  it('opens every rule of an account, read-only, with "Not verifiable from imports" where it applies', async () => {
    const { table, user } = await open()
    await user.click(within(rowOf(table, 'Apex 150K')).getByRole('button', { name: 'Rules of Apex 150K' }))
    const dialog = await screen.findByRole('dialog', { name: 'Apex 150K rules' })
    const rules = within(dialog).getByRole('table', { name: 'Rules' })
    expect(
      within(rules)
        .getByRole('rowheader', { name: /Max loss \(drawdown\)/ })
        .closest('tr'),
    ).toHaveTextContent('Not verifiable from imports')
    expect(
      within(rules)
        .getByRole('rowheader', { name: /Withdrawable now/ })
        .closest('tr'),
    ).toHaveTextContent('Pass')
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument() // nothing to edit here
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('leads to each account’s settings from its ⋯ menu, and nowhere else', async () => {
    const { table, user, router } = await open()
    await user.click(within(rowOf(table, 'Lucid 100K')).getByRole('button', { name: 'More for Lucid 100K' }))
    const menu = await screen.findByRole('group', { name: 'Lucid 100K menu' })
    expect(
      within(menu)
        .getAllByRole('link')
        .map((l) => l.textContent),
    ).toEqual(['Account settings'])
    await user.click(within(menu).getByRole('link', { name: 'Account settings' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/accounts'))
    expect(router.state.location.search).toMatchObject({ account: 3, tab: 'details' })
  })
})

describe('an evaluation that passed', () => {
  async function withPassed() {
    const real = await demoSource.accountsOverview()
    const passed: AccountRow = {
      ...(real.accounts.find((r) => r.account.id === 3) as AccountRow),
      status: 'passed',
      pass_prompt: true,
    }
    const overview: AccountsOverview = {
      ...real,
      accounts: real.accounts.map((r) => (r.account.id === 3 ? passed : r)),
    }
    return vi.spyOn(demoSource, 'accountsOverview').mockResolvedValue(overview)
  }

  it('offers to set up the funded account, which leads to the settings that archive it as passed', async () => {
    await withPassed()
    const { user, router } = await open()
    const prompt = screen.getByText(/Evaluation passed: set up your funded account/).closest('[role="status"]')
    expect(prompt).toHaveTextContent('Lucid 100K')
    await user.click(within(prompt as HTMLElement).getByRole('link', { name: 'Set up' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/accounts'))
    expect(router.state.location.search).toMatchObject({ account: 3, setup: 'funded' })
    const dialog = await screen.findByRole('dialog', { name: 'Archive Lucid 100K' })
    expect(within(dialog).getByRole('button', { name: 'Passed' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('can be dismissed', async () => {
    await withPassed()
    const dismiss = vi.spyOn(demoSource, 'dismissPassPrompt')
    const { user } = await open()
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith(3))
  })
})
