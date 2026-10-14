import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Decimal from 'decimal.js'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoImports } from '../../lib/demo/imports'
import { demoSource } from '../../lib/demo/source'
import { formatPnl } from '../../lib/format'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoAccounts()
  resetDemoImports()
  document.documentElement.removeAttribute('data-theme')
})

async function open(search: Record<string, string | number> = {}) {
  stubApi()
  const app = openApp('/settings/accounts', search)
  await screen.findByRole('heading', { name: 'Prop accounts' })
  return { ...app, user: userEvent.setup() }
}

const netOf = (accountId: number) =>
  SAMPLE_TRADES.filter((t) => t.account.id === accountId)
    .reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0))
    .toFixed(2)

const rowOf = async (name: string) => {
  const list = await screen.findByRole('region', { name: 'Accounts' })
  return within(list).getByRole('button', { name }).closest('tr') as HTMLElement
}

describe('the account list', () => {
  it('lists the active accounts with stage, size, target progress and net result', async () => {
    await open()
    const list = await screen.findByRole('region', { name: 'Accounts' })
    await waitFor(() => expect(within(list).getAllByRole('row')).toHaveLength(5)) // header and four accounts

    const funded = await rowOf('Apex 50K #1')
    expect(funded).toHaveTextContent('Apex')
    expect(funded).toHaveTextContent('Funded')
    expect(funded).toHaveTextContent('$50,000')
    expect(funded).toHaveTextContent('—') // a funded account has no target
    expect(funded).toHaveTextContent(formatPnl(netOf(1)))

    const evaluation = await rowOf('Apex 50K #2')
    expect(evaluation).toHaveTextContent('Evaluation')
    expect(within(evaluation).getByRole('progressbar', { name: 'Apex 50K #2 profit target' })).toBeInTheDocument()
    expect(evaluation).toHaveTextContent(formatPnl(netOf(4)))
  })

  it('tucks archived accounts away, shows how they ended, and can restore one', async () => {
    const { user } = await open()
    const list = await screen.findByRole('region', { name: 'Accounts' })
    expect(await within(list).findByText('Archived (5)')).toBeInTheDocument()
    expect(within(list).queryByRole('list', { name: 'Archived accounts' })).not.toBeInTheDocument()

    await user.click(within(list).getByRole('button', { name: 'Show archived' }))
    const archived = within(list).getByRole('list', { name: 'Archived accounts' })
    expect(within(archived).getAllByRole('listitem')).toHaveLength(5)
    expect(archived).toHaveTextContent('Apex 50K (old)')
    expect(archived).toHaveTextContent('Failed')
    expect(archived).toHaveTextContent('Hit the trailing drawdown on CPI day.')
    expect(archived).toHaveTextContent('Passed')

    await user.click(within(archived).getByRole('button', { name: 'Restore Apex 50K (old)' }))
    await waitFor(() => expect(within(list).getByText('Archived (4)')).toBeInTheDocument())
    expect(within(list).getByRole('button', { name: 'Apex 50K (old)' })).toBeInTheDocument()
  })

  it('shows the prop business: pass rate, costs, payouts and the net', async () => {
    await open()
    const card = await screen.findByRole('region', { name: 'Prop business' })
    await waitFor(() => expect(card).toHaveTextContent('60%'))
    expect(card).toHaveTextContent('3 of 5 evaluations completed')
    expect(card).toHaveTextContent('+$3,150.00')
    const totals = await demoSource.businessTotals()
    expect(card).toHaveTextContent(formatPnl(`-${totals.costs}`))
    expect(card).toHaveTextContent(formatPnl(totals.net))
  })

  it('names accounts with no cost recorded, so the net is not mistaken for the truth', async () => {
    for (const e of await demoSource.ledger(6)) await demoSource.deleteLedgerEntry(6, e.id)
    await open()
    expect(await screen.findByText(/1 account has no cost recorded \(Lucid 50K\)/)).toBeInTheDocument()
  })

  it('asks you to choose an account before showing one', async () => {
    await open()
    expect(await screen.findByText(/Choose an account to see and change/)).toBeInTheDocument()
  })
})

describe('the picked account', () => {
  it('opens when chosen, with its tab and the account in the address', async () => {
    const { user, router } = await open()
    await user.click(
      await within(await screen.findByRole('region', { name: 'Accounts' })).findByRole('button', {
        name: 'Apex 50K #2',
      }),
    )
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    expect(within(panel).getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true')
    expect(router.state.location.search).toMatchObject({ account: 4 })
    expect(screen.getByRole('button', { name: 'Apex 50K #2' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('opens straight from an address, on the tab it names', async () => {
    await open({ account: 4, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    expect(within(panel).getByRole('tab', { name: 'Rules' })).toHaveAttribute('aria-selected', 'true')
    expect(within(panel).getByLabelText('Profit target')).toHaveValue('3000')
  })

  it('ignores an address that names no account, or a tab that does not exist', async () => {
    await open({ account: 999, tab: 'secrets' })
    expect(await screen.findByText(/Choose an account to see and change/)).toBeInTheDocument()
  })

  it('moves between tabs by click and by arrow keys, one tab stop at a time', async () => {
    const { user, router } = await open({ account: 4 })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    const tabs = within(panel).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Details', 'Fees', 'Rules', 'Firm rules', 'Payouts', 'Ledger'])
    expect(tabs.filter((t) => t.getAttribute('tabindex') === '0')).toHaveLength(1)

    await user.click(within(panel).getByRole('tab', { name: 'Fees' }))
    expect(within(panel).getByRole('tab', { name: 'Fees' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'fees' }))

    await user.keyboard('{ArrowRight}')
    await waitFor(() =>
      expect(within(panel).getByRole('tab', { name: 'Rules' })).toHaveAttribute('aria-selected', 'true'),
    )
    expect(within(panel).getByRole('tab', { name: 'Rules' })).toHaveFocus()
    await user.keyboard('{End}')
    await waitFor(() =>
      expect(within(panel).getByRole('tab', { name: 'Ledger' })).toHaveAttribute('aria-selected', 'true'),
    )
    await user.keyboard('{ArrowRight}') // wraps to the first
    await waitFor(() =>
      expect(within(panel).getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true'),
    )
    await user.keyboard('{ArrowLeft}') // and back round to the last
    await waitFor(() =>
      expect(within(panel).getByRole('tab', { name: 'Ledger' })).toHaveAttribute('aria-selected', 'true'),
    )
    expect(within(panel).getByRole('tabpanel')).toHaveAccessibleName('Ledger')
  })
})

describe('details', () => {
  it('shows what is stored, and saves a change to it', async () => {
    const { user } = await open({ account: 4 })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    expect(within(panel).getByLabelText('Account name')).toHaveValue('Apex 50K #2')
    expect(within(panel).getByLabelText('Firm')).toHaveValue('Apex')
    expect(within(panel).getByLabelText('Account size')).toHaveValue('50000')

    await user.clear(within(panel).getByLabelText('Account name'))
    await user.type(within(panel).getByLabelText('Account name'), 'Apex 50K #2 (second go)')
    await user.click(within(panel).getByRole('button', { name: 'Save details' }))
    expect(await screen.findByRole('region', { name: 'Apex 50K #2 (second go) settings' })).toBeInTheDocument()
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(
      within(screen.getByRole('region', { name: 'Accounts' })).getByRole('button', { name: 'Apex 50K #2 (second go)' }),
    ).toBeInTheDocument()
  })

  it('says why a name cannot be used, and an amount that is not an amount', async () => {
    const { user } = await open({ account: 4 })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    await user.clear(within(panel).getByLabelText('Account name'))
    await user.type(within(panel).getByLabelText('Account name'), 'Apex 150K')
    await user.click(within(panel).getByRole('button', { name: 'Save details' }))
    expect(await within(panel).findByRole('alert')).toHaveTextContent('You already have an account called Apex 150K.')

    await user.clear(within(panel).getByLabelText('Account size'))
    await user.type(within(panel).getByLabelText('Account size'), 'lots')
    await user.click(within(panel).getByRole('button', { name: 'Save details' }))
    expect(await within(panel).findByText('Type an amount like 2500 or 2,500.50.')).toBeInTheDocument()
  })

  it('offers to restore an archived account', async () => {
    const { user } = await open({ account: 5 })
    const panel = await screen.findByRole('region', { name: 'Apex 50K (old) settings' })
    expect(within(panel).getByRole('note')).toHaveTextContent('Hit the trailing drawdown on CPI day.')
    expect(within(panel).queryByRole('button', { name: 'Archive…' })).not.toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Restore account' }))
    await waitFor(() =>
      expect(
        within(screen.getByRole('region', { name: 'Apex 50K (old) settings' })).queryByRole('note'),
      ).not.toBeInTheDocument(),
    )
    expect((await demoSource.accountOverviews()).find((o) => o.account.id === 5)?.account.outcome).toBeNull()
  })
})

describe('rules', () => {
  it('shows progress against the profit target and the drawdown left, with the caveat', async () => {
    await open({ account: 4, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    expect(within(panel).getByLabelText('Profit target')).toHaveValue('3000')
    expect(within(panel).getByLabelText('Max drawdown')).toHaveValue('2500')
    expect(within(panel).getByLabelText('Trailing type')).toHaveValue('eod')
    expect(within(panel).getByRole('progressbar', { name: 'Progress to the profit target' })).toBeInTheDocument()
    expect(within(panel).getByRole('progressbar', { name: 'Drawdown buffer left' })).toBeInTheDocument()
    expect(panel).toHaveTextContent('Informational only.')
    expect(panel).toHaveTextContent('May differ from the firm', { normalizeWhitespace: true })
  })

  it('has no progress to show when no rule is set', async () => {
    await open({ account: 5, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K (old) settings' })
    expect(within(panel).queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('saves new rules, and the progress follows them', async () => {
    const { user } = await open({ account: 4, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    const target = within(panel).getByLabelText('Profit target')
    await user.clear(target)
    await user.type(target, '1,500')
    await user.click(within(panel).getByRole('button', { name: 'Save rules' }))
    expect(await within(panel).findByText('Saved')).toBeInTheDocument()
    const saved = (await demoSource.accountOverviews()).find((o) => o.account.id === 4)
    expect(saved?.account.profit_target).toBe('1500.00')
    await waitFor(() =>
      expect(
        within(panel).getByRole('progressbar', { name: 'Progress to the profit target' }).parentElement?.parentElement,
      ).toHaveTextContent('/ $1,500'),
    )
  })

  it('shows the trailing-stop level only when the trailing stops at one', async () => {
    const { user } = await open({ account: 4, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    expect(within(panel).queryByLabelText('Stops trailing at')).not.toBeInTheDocument()
    await user.click(within(panel).getByLabelText('The trailing stops at a level'))
    const level = within(panel).getByLabelText('Stops trailing at')
    await user.click(within(panel).getByRole('button', { name: 'Save rules' }))
    expect(
      await within(panel).findByText('Give the level at which the trailing stops, or switch the lock off.'),
    ).toBeInTheDocument()
    await user.type(level, '52600')
    await user.click(within(panel).getByRole('button', { name: 'Save rules' }))
    expect(await within(panel).findByText('Saved')).toBeInTheDocument()
    expect((await demoSource.accountOverviews()).find((o) => o.account.id === 4)?.account).toMatchObject({
      trail_lock_enabled: true,
      trail_lock_level: '52600.00',
    })
    await user.selectOptions(within(panel).getByLabelText('Trailing type'), 'None')
    expect(within(panel).queryByLabelText('The trailing stops at a level')).not.toBeInTheDocument()
  })

  it('says which box holds something that is not an amount, and saves nothing', async () => {
    const { user } = await open({ account: 4, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    await user.clear(within(panel).getByLabelText('Max drawdown'))
    await user.type(within(panel).getByLabelText('Max drawdown'), 'twenty-five hundred')
    await user.click(within(panel).getByRole('button', { name: 'Save rules' }))
    expect(await within(panel).findByText('Type an amount like 2500 or 2,500.50.')).toBeInTheDocument()
    expect((await demoSource.accountOverviews()).find((o) => o.account.id === 4)?.account.max_drawdown).toBe('2500.00')
  })

  it('treats an emptied box as "no limit"', async () => {
    const { user } = await open({ account: 4, tab: 'rules' })
    const panel = await screen.findByRole('region', { name: 'Apex 50K #2 settings' })
    await user.clear(within(panel).getByLabelText('Profit target'))
    await user.click(within(panel).getByRole('button', { name: 'Save rules' }))
    await within(panel).findByText('Saved')
    expect((await demoSource.accountOverviews()).find((o) => o.account.id === 4)?.account.profit_target).toBeNull()
  })
})

describe('fees', () => {
  it('lists the fee for each contract, with the default last', async () => {
    await open({ account: 1, tab: 'fees' })
    const list = await screen.findByRole('list', { name: 'Fees by contract' })
    expect(
      within(list)
        .getAllByRole('listitem')
        .map((li) => li.textContent?.split('$')[0]?.trim()),
    ).toEqual(['CL', 'ES', 'GC', 'MNQ', 'NQ', 'Any other'])
    expect(within(list).getByLabelText('Fee for NQ')).toHaveValue('2.20')
    expect(within(list).getByLabelText('Fee for any other contract')).toHaveValue('2.50')
  })

  it('saves a changed fee when you leave the box', async () => {
    const { user } = await open({ account: 1, tab: 'fees' })
    const nq = await screen.findByLabelText('Fee for NQ')
    await user.clear(nq)
    await user.type(nq, '2.4')
    await user.tab()
    await waitFor(async () => expect((await demoSource.fees(1))['NQ']).toBe('2.40'))
  })

  it('refuses a fee that is not an amount, and keeps the old one', async () => {
    const { user } = await open({ account: 1, tab: 'fees' })
    const nq = await screen.findByLabelText('Fee for NQ')
    await user.clear(nq)
    await user.type(nq, 'cheap')
    await user.tab()
    expect(await screen.findByText('Type an amount like 2500 or 2,500.50.')).toBeInTheDocument()
    expect((await demoSource.fees(1))['NQ']).toBe('2.20')
  })

  it('removes a fee', async () => {
    const { user } = await open({ account: 1, tab: 'fees' })
    await user.click(await screen.findByRole('button', { name: 'Remove the fee for CL' }))
    await waitFor(() => expect(screen.queryByLabelText('Fee for CL')).not.toBeInTheDocument())
    expect(await demoSource.fees(1)).not.toHaveProperty('CL')
  })

  it('adds a fee for a contract, in capitals', async () => {
    const { user } = await open({ account: 1, tab: 'fees' })
    await screen.findByRole('list', { name: 'Fees by contract' })
    const form = screen.getByRole('form', { name: 'Add a fee' })
    await user.type(within(form).getByLabelText('Contract'), 'rty')
    await user.type(within(form).getByLabelText('Fee ($)'), '2.1')
    await user.click(within(form).getByRole('button', { name: 'Add fee' }))
    expect(await screen.findByLabelText('Fee for RTY')).toHaveValue('2.10')
    expect(within(form).getByLabelText('Contract')).toHaveValue('') // the form is ready for the next
  })

  it('asks for a fee before adding one', async () => {
    const { user } = await open({ account: 1, tab: 'fees' })
    await screen.findByRole('list', { name: 'Fees by contract' })
    await user.click(screen.getByRole('button', { name: 'Add fee' }))
    expect(await screen.findByText('Type the fee for one contract.')).toBeInTheDocument()
  })

  it('says when an account has no fees', async () => {
    await open({ account: 5, tab: 'fees' })
    expect(await screen.findByText(/No fees set/)).toBeInTheDocument()
  })
})

describe('ledger', () => {
  it('lists the money in and out, newest first, with totals', async () => {
    await open({ account: 2, tab: 'ledger' })
    const table = await screen.findByRole('table', { name: 'Ledger entries' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Payout')
    expect(rows[0]).toHaveTextContent('+$1,650.00')
    expect(rows[2]).toHaveTextContent('Evaluation fee')
    expect(rows[2]).toHaveTextContent('−$167.00')
    const totals = screen.getByRole('group', { name: 'Totals for this account' })
    expect(totals).toHaveTextContent('−$297.00')
    expect(totals).toHaveTextContent('+$1,650.00')
  })

  it('suggests the likeliest next entry: a reset for an evaluation, a payout for a funded account', async () => {
    await open({ account: 4, tab: 'ledger' })
    await waitFor(() => expect(screen.getByLabelText('What for')).toHaveValue('reset_fee')) // once the entries have loaded
  })

  it('suggests the purchase fee for an account with no cost yet', async () => {
    await demoSource.createAccount({
      name: 'Fresh',
      firm: 'Apex',
      account_type: 'funded',
      account_size: null,
      starting_balance: '0.00',
      purchase_date: '2026-09-01',
      phase: null,
    })
    await open({ account: 10, tab: 'ledger' })
    await screen.findByText('Nothing recorded yet.')
    expect(screen.getByLabelText('What for')).toHaveValue('activation_fee')
  })

  it('adds an entry, and the totals and the business follow', async () => {
    const { user } = await open({ account: 4, tab: 'ledger' })
    await screen.findByRole('table', { name: 'Ledger entries' })
    await user.type(screen.getByLabelText('Amount ($)'), '80')
    await user.type(screen.getByLabelText('Note (optional)'), 'second reset')
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    await waitFor(() =>
      expect(within(screen.getByRole('table', { name: 'Ledger entries' })).getAllByRole('row')).toHaveLength(4),
    )
    expect(screen.getByText('second reset')).toBeInTheDocument()
    expect(screen.getByLabelText('Amount ($)')).toHaveValue('') // ready for the next
    expect(screen.getByRole('group', { name: 'Totals for this account' })).toHaveTextContent('−$327.00')
  })

  it('asks for an amount, and refuses one that is not', async () => {
    const { user } = await open({ account: 4, tab: 'ledger' })
    await screen.findByRole('table', { name: 'Ledger entries' })
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    expect(await screen.findByText('Type the amount.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Amount ($)'), '-5')
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    expect(await screen.findByText('Type an amount like 2500 or 2,500.50.')).toBeInTheDocument()
  })

  it('asks before deleting an entry, and can be told to keep it', async () => {
    const { user } = await open({ account: 2, tab: 'ledger' })
    const table = await screen.findByRole('table', { name: 'Ledger entries' })
    await user.click(within(table).getByRole('button', { name: 'Delete Payout 2026-09-22' }))
    await user.click(within(table).getByRole('button', { name: 'Keep' }))
    expect(within(table).getAllByRole('row')).toHaveLength(4)
    await user.click(within(table).getByRole('button', { name: 'Delete Payout 2026-09-22' }))
    await user.click(within(table).getByRole('button', { name: 'Confirm delete Payout 2026-09-22' }))
    await waitFor(() =>
      expect(within(screen.getByRole('table', { name: 'Ledger entries' })).getAllByRole('row')).toHaveLength(3),
    )
    expect(await demoSource.ledger(2)).toHaveLength(2)
  })
})

describe('adding an account', () => {
  it('opens a dialog, adds the account, and opens it', async () => {
    const { user, router } = await open()
    await user.click(await screen.findByRole('button', { name: '+ Add account' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add account' })
    await user.type(within(dialog).getByLabelText('Account name'), 'Tradeify 100K')
    await user.selectOptions(within(dialog).getByLabelText('Firm'), 'Tradeify')
    await user.type(within(dialog).getByLabelText('Account size'), '100,000')
    await user.type(within(dialog).getByLabelText('Starting balance'), '100000')
    await user.type(within(dialog).getByLabelText('Purchase date'), '2026-09-01')
    await user.click(within(dialog).getByRole('button', { name: 'Add account' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByRole('region', { name: 'Tradeify 100K settings' })).toBeInTheDocument()
    expect(router.state.location.search).toMatchObject({ account: 10, tab: 'details' })
    expect(await demoSource.accounts()).toContainEqual(expect.objectContaining({ name: 'Tradeify 100K' }))
  })

  it('says why an account cannot be added, and keeps the dialog open', async () => {
    const { user } = await open()
    await user.click(await screen.findByRole('button', { name: '+ Add account' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add account' })
    await user.click(within(dialog).getByRole('button', { name: 'Add account' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Choose the day you bought the account.')
    await user.type(within(dialog).getByLabelText('Purchase date'), '2026-09-01')
    await user.click(within(dialog).getByRole('button', { name: 'Add account' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Give the account a name.')
    await user.type(within(dialog).getByLabelText('Account name'), 'Apex 150K')
    await user.click(within(dialog).getByRole('button', { name: 'Add account' }))
    expect(await within(dialog).findByText('You already have an account called Apex 150K.')).toBeInTheDocument()
  })

  it('closes on Cancel and on Escape without adding anything', async () => {
    const { user } = await open()
    const add = await screen.findByRole('button', { name: '+ Add account' })
    await user.click(add)
    await user.click(
      within(await screen.findByRole('dialog', { name: 'Add account' })).getByRole('button', { name: 'Cancel' }),
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(add)
    await screen.findByRole('dialog', { name: 'Add account' })
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(add).toHaveFocus()
    expect(await demoSource.accountOverviews()).toHaveLength(9)
  })

  it('shows a new account to the import page too', async () => {
    const { user } = await open()
    await user.click(await screen.findByRole('button', { name: '+ Add account' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add account' })
    await user.type(within(dialog).getByLabelText('Account name'), 'Tradeify 100K')
    await user.type(within(dialog).getByLabelText('Purchase date'), '2026-09-01')
    await user.click(within(dialog).getByRole('button', { name: 'Add account' }))
    await screen.findByRole('region', { name: 'Tradeify 100K settings' })
    await user.click(screen.getByRole('link', { name: 'Import' }))
    const select = await screen.findByLabelText('Import into account')
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Tradeify 100K' })).toBeInTheDocument())
  })
})

describe('archiving an account', () => {
  it('asks how it ended, and an evaluation can pass, fail or be closed', async () => {
    const { user } = await open({ account: 4 })
    await user.click(await screen.findByRole('button', { name: 'Archive…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Archive Apex 50K #2' })
    const how = within(dialog).getByRole('group', { name: 'How did it end?' })
    expect(
      within(how)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Passed', 'Failed', 'Closed'])
    expect(within(dialog).getByRole('button', { name: 'Archive account' })).toBeDisabled() // until it is chosen
    expect(within(dialog).getByLabelText('Ended on')).toHaveValue(
      (await demoSource.accountOverviews()).find((o) => o.account.id === 4)?.progress.last_trading_day,
    )
  })

  it('lets a funded account end only as failed or closed', async () => {
    const { user } = await open({ account: 1 })
    await user.click(await screen.findByRole('button', { name: 'Archive…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Archive Apex 50K #1' })
    expect(
      within(within(dialog).getByRole('group', { name: 'How did it end?' }))
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Failed', 'Closed'])
  })

  it('archives with the outcome, the date and a note, and the account moves to the archived list', async () => {
    const { user } = await open({ account: 1 })
    await user.click(await screen.findByRole('button', { name: 'Archive…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Archive Apex 50K #1' })
    await user.click(within(dialog).getByRole('button', { name: 'Failed' }))
    await user.type(within(dialog).getByLabelText('Note (optional)'), 'Blew the buffer')
    await user.click(within(dialog).getByRole('button', { name: 'Archive account' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    const archived = (await demoSource.accountOverviews()).find((o) => o.account.id === 1)?.account
    expect(archived).toMatchObject({ outcome: 'failed', archive_note: 'Blew the buffer' })
    const list = screen.getByRole('region', { name: 'Accounts' })
    await waitFor(() => expect(within(list).getByText('Archived (6)')).toBeInTheDocument())
    expect(screen.queryByRole('region', { name: 'Apex 50K #1 settings' })).not.toBeInTheDocument() // the panel closed
  })

  it('goes on to set up the funded account after a passed evaluation, with its details filled in', async () => {
    const { user } = await open({ account: 4 })
    await user.click(await screen.findByRole('button', { name: 'Archive…' }))
    const archive = await screen.findByRole('dialog', { name: 'Archive Apex 50K #2' })
    await user.click(within(archive).getByRole('button', { name: 'Passed' }))
    expect(within(archive).getByLabelText('Set up the funded account next')).toBeChecked()
    await user.click(within(archive).getByRole('button', { name: 'Archive account' }))

    const add = await screen.findByRole('dialog', { name: 'Add account' })
    expect(within(add).getByLabelText('Account name')).toHaveValue('Apex 50K #2 funded')
    expect(within(add).getByLabelText('Firm')).toHaveValue('Apex')
    expect(within(add).getByLabelText('Account size')).toHaveValue('50000')
    expect(within(add).getByRole('button', { name: 'Funded' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(add).getByRole('button', { name: 'Add account' }))
    expect(await screen.findByRole('region', { name: 'Apex 50K #2 funded settings' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Accounts' })).getByText('Archived (6)')).toBeInTheDocument()
  })

  it('does not set up a funded account when told not to', async () => {
    const { user } = await open({ account: 4 })
    await user.click(await screen.findByRole('button', { name: 'Archive…' }))
    const archive = await screen.findByRole('dialog', { name: 'Archive Apex 50K #2' })
    await user.click(within(archive).getByRole('button', { name: 'Passed' }))
    await user.click(within(archive).getByLabelText('Set up the funded account next'))
    await user.click(within(archive).getByRole('button', { name: 'Archive account' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await demoSource.accountOverviews()).toHaveLength(9)
  })

  it('says so, and keeps the dialog, when the archive is refused', async () => {
    vi.spyOn(demoSource, 'archiveAccount').mockRejectedValueOnce(new Error('The server said no.'))
    const { user } = await open({ account: 1 })
    await user.click(await screen.findByRole('button', { name: 'Archive…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Archive Apex 50K #1' })
    await user.click(within(dialog).getByRole('button', { name: 'Closed' }))
    await user.click(within(dialog).getByRole('button', { name: 'Archive account' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('The server said no.')
  })
})
