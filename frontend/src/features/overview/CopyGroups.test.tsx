import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TradeAccount } from '../../components/TradeAccount'
import { allAccountRefs, resetDemoAccounts } from '../../lib/demo/accounts'
import { create, resetDemoCopyGroups } from '../../lib/demo/copyGroups'
import { resetDemoRules } from '../../lib/demo/rules'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoAccounts()
  resetDemoRules()
  resetDemoCopyGroups()
})

const id = (name: string) => {
  const found = allAccountRefs().find((a) => a.name === name)
  if (!found) throw new Error(`no sample account ${name}`)
  return found.id
}

function twoGroups() {
  create({
    name: 'Main copy',
    leader_account_id: id('Apex 50K #1'),
    follower_account_ids: [id('Apex 150K'), id('Lucid 100K')],
    copying_since: null,
    acknowledge_fills: true,
  })
  create({
    name: 'Side copy',
    leader_account_id: id('Apex 50K #2'),
    follower_account_ids: [id('Lucid 100K')],
    copying_since: null,
    acknowledge_fills: true,
  })
}

describe('the Accounts tab with copy-trading groups', () => {
  it('shows no Individual and Groups tabs until there is a group', async () => {
    stubApi()
    openApp('/accounts')
    await screen.findByRole('table', { name: 'Active accounts' })
    expect(screen.queryByRole('button', { name: 'Groups' })).not.toBeInTheDocument()
  })

  it('lists each group with the leader first, the cumulative P&L, and an account in two groups in yellow', async () => {
    twoGroups()
    stubApi()
    openApp('/accounts')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Groups' }))
    const main = await screen.findByRole('region', { name: 'Group Main copy' })
    const rows = within(main).getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Apex 50K #1')
    expect(rows[1]).toHaveTextContent('Leader')
    expect(rows.at(-1)).toHaveTextContent('Cumulative group P&L')
    const lucid = rows.find((row) => row.textContent?.includes('Lucid 100K')) as HTMLElement
    const apex150 = rows.find((row) => row.textContent?.includes('Apex 150K')) as HTMLElement
    expect(lucid).toHaveClass('bg-multi-group-bg')
    expect(apex150).not.toHaveClass('bg-multi-group-bg')
    const badge = within(lucid).getByRole('button', { name: '2 groups' })
    expect(badge).toHaveAttribute('title', 'Also in Side copy')
    await user.click(badge)
    expect(within(lucid).getByText('Also in Side copy')).toBeInTheDocument()
  })

  it('refuses a removal dated before today and says why', async () => {
    twoGroups()
    stubApi()
    openApp('/accounts')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Groups' }))
    const main = await screen.findByRole('region', { name: 'Group Main copy' })
    const apex150 = within(main)
      .getAllByRole('row')
      .find((row) => row.textContent?.includes('Apex 150K')) as HTMLElement
    await user.click(within(apex150).getByRole('button', { name: 'Remove' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Takes effect on'), '2020-01-01')
    await user.click(within(dialog).getByRole('button', { name: 'Remove follower' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('today or later')
  })
})

describe('a copied trade in a list', () => {
  const account = { id: 1, name: 'APEX-123456-07', firm: 'Apex', account_type: 'funded' as const }

  it('shows the group name, then the leader shortened to its first and last three characters', () => {
    render(<TradeAccount trade={{ account, copy_group: { id: 4, name: 'Main copy' } }} />)
    expect(screen.getByText('Main copy')).toBeInTheDocument()
    expect(screen.getByText('APE...-07')).toBeInTheDocument()
  })

  it('shows a trade that was not copied under its own account', () => {
    render(<TradeAccount trade={{ account, copy_group: null }} />)
    expect(screen.getByText('APEX-123456-07')).toBeInTheDocument()
  })
})

describe('the import form’s copy-trading part', () => {
  it('saves a group only with followers, a name and the fills acknowledgment', async () => {
    stubApi()
    openApp('/import')
    const user = userEvent.setup()
    const section = await screen.findByRole('region', { name: 'Copy trading' })
    await user.click(within(section).getByRole('radio', { name: 'Yes' }))
    const save = within(section).getByRole('button', { name: 'Save the group' })
    await user.selectOptions(within(section).getByLabelText('Follower accounts'), String(id('Lucid 100K')))
    await user.type(within(section).getByLabelText('Group name'), 'Main copy')
    expect(save).toBeDisabled() // not acknowledged yet
    expect(within(section).getByRole('list', { name: 'Chosen followers' })).toHaveTextContent('Lucid 100K')
    await user.click(within(section).getByRole('checkbox'))
    await user.click(save)
    expect(
      await within(await screen.findByRole('region', { name: 'Copy trading' })).findByText(/leads Main copy/),
    ).toBeInTheDocument()
  })
})
