import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { resetDemoRules } from '../../lib/demo/rules'
import { demoSource } from '../../lib/demo/source'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoAccounts()
  resetDemoRules()
})

async function open(search: Record<string, string | number>) {
  stubApi()
  const app = openApp('/settings/accounts', search)
  await screen.findByRole('heading', { name: 'Prop accounts' })
  return { ...app, user: userEvent.setup() }
}

describe('firm rules', () => {
  it('offers the versions in force on the purchase date, suggests the latest, and fills the form', async () => {
    const { user } = await open({ account: 4, tab: 'firm-rules' }) // Apex 50K #2, bought 2026-08-24, no rules
    expect(await screen.findByText(/No rules yet/)).toBeInTheDocument()
    const list = await screen.findByRole('list', { name: 'Rule sets' })
    const offered = within(list)
      .getAllByRole('button')
      .map((b) => b.textContent ?? '')
    expect(offered.some((t) => t.includes('Select Flex'))).toBe(false) // in force from 2026-09-01, after the purchase
    expect(offered.some((t) => t.includes('LucidPro · 50K · Funded') && t.includes('legacy'))).toBe(true) // undated

    await user.type(screen.getByLabelText(/Find your account/), 'apex 50K evaluation')
    const [only, ...rest] = within(list).getAllByRole('button')
    expect(rest).toHaveLength(0)
    expect(only).toHaveTextContent('Apex Trader Funding · EOD Trailing (Apex 4.0) · 50K · Evaluation')
    expect(only).toHaveTextContent('Suggested')
    await user.click(only as HTMLElement)

    const form = await screen.findByRole('form', { name: 'Firm rules' })
    expect(within(form).getByLabelText('Max loss ($)')).toHaveValue('2000.00')
    expect(within(form).getByLabelText('Profit target ($)')).toHaveValue('3000.00')
    expect(within(form).getByText(/could not be confirmed by the research/)).toBeInTheDocument()
  })

  it('shows a flagged field in red with the research’s reason, as a warning that does not block saving', async () => {
    const { user } = await open({ account: 4, tab: 'firm-rules' })
    await user.type(await screen.findByLabelText(/Find your account/), 'apex 50K evaluation')
    await user.click(
      within(await screen.findByRole('list', { name: 'Rule sets' })).getAllByRole('button')[0] as HTMLElement,
    )
    const form = await screen.findByRole('form', { name: 'Firm rules' })
    const flagged = within(form)
      .getAllByRole('textbox')
      .concat(within(form).getAllByRole('combobox'))
      .filter((control) => control.getAttribute('title'))
    expect(flagged.length).toBeGreaterThan(0)
    for (const control of flagged) {
      expect(control).toHaveClass('border-neg')
      expect(control.getAttribute('aria-describedby')).toMatch(/-warning$/)
    }
    await user.click(within(form).getByRole('button', { name: 'Verify and save rules' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Verified and saved')
    const saved = await demoSource.ruleSet(4)
    expect(saved?.values['max_loss_usd']).toBe('2000.00')
    expect(saved?.flags.length).toBeGreaterThan(0) // the account keeps what the research could not confirm
  })

  it('refuses a rule set that does not make sense, in red, until it is fixed', async () => {
    const { user } = await open({ account: 4, tab: 'firm-rules' })
    await user.type(await screen.findByLabelText(/Find your account/), 'apex 50K evaluation')
    await user.click(
      within(await screen.findByRole('list', { name: 'Rule sets' })).getAllByRole('button')[0] as HTMLElement,
    )
    const form = await screen.findByRole('form', { name: 'Firm rules' })
    const pct = within(form).getByLabelText('Consistency % (%)')
    await user.type(pct, '40') // with "measured against: none"
    const loss = within(form).getByLabelText('Max loss ($)')
    await user.clear(loss)
    await user.type(loss, '-5')
    await user.click(within(form).getByRole('button', { name: 'Verify and save rules' }))
    expect(await within(form).findByText('Max loss cannot be negative.')).toBeInTheDocument()
    expect(
      within(form).getByText('Choose what the consistency percentage is measured against, or clear it.'),
    ).toBeInTheDocument()
    expect(within(form).getByLabelText('Measured against')).toHaveAttribute('aria-invalid', 'true')
    expect(await demoSource.ruleSet(4)).toBeNull()

    await user.clear(pct)
    await user.clear(loss)
    await user.type(loss, '2000')
    await user.click(within(form).getByRole('button', { name: 'Verify and save rules' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Verified and saved')
  })

  it('asks for the purchase date before offering templates, and rules can be typed in by hand', async () => {
    const current = (await demoSource.accountOverviews()).find((o) => o.account.id === 4)?.account
    await demoSource.updateAccount(4, {
      name: current?.name ?? '',
      firm: current?.firm ?? 'Apex',
      account_type: 'evaluation',
      account_size: current?.account_size ?? null,
      starting_balance: current?.starting_balance ?? '0.00',
      purchase_date: null,
      phase: null,
    })
    const { user } = await open({ account: 4, tab: 'firm-rules' })
    expect(await screen.findByText(/Add the purchase date on the Details tab first/)).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Rule sets' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start blank' }))
    const form = await screen.findByRole('form', { name: 'Firm rules' })
    expect(within(form).getByLabelText('Phase')).toHaveValue('evaluation')
    for (const [label, value] of [
      ['Firm', 'My Firm'],
      ['Program', 'Standard'],
      ['Account size', '50K'],
      ['Rule version', 'from their site, October'],
    ]) {
      await user.type(within(form).getByLabelText(label as string), value as string)
    }
    await user.click(within(form).getByRole('button', { name: 'Verify and save rules' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Verified and saved')
    expect((await demoSource.ruleSet(4))?.source_template_id).toBeNull()
  })

  it('after a passed evaluation, adds the funded account and searches its rules for it', async () => {
    const { user, router } = await open({ account: 3, setup: 'funded' }) // Lucid 100K, an evaluation with rules
    const archive = await screen.findByRole('dialog', { name: 'Archive Lucid 100K' })
    await user.click(within(archive).getByRole('button', { name: 'Archive account' }))
    const add = await screen.findByRole('dialog', { name: 'Add account' })
    expect(within(add).getByLabelText('Account name')).toHaveValue('Lucid 100K funded')
    expect(within(add).getByRole('button', { name: 'Funded' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(add).getByLabelText('Purchase date')).not.toHaveValue('')
    await user.click(within(add).getByRole('button', { name: 'Add account' }))
    await waitFor(() => expect(router.state.location.search).toMatchObject({ account: 10, tab: 'firm-rules' }))
    expect(router.state.location.search).not.toHaveProperty('setup')
    expect(await screen.findByLabelText(/Find your account/)).toHaveValue('Lucid Trading LucidPro 100K funded')
  })
})

describe('payouts', () => {
  it('lists the payouts with their number, gross, split and net, and the lifetime totals', async () => {
    await open({ account: 2, tab: 'payouts' }) // Apex 150K
    const table = await screen.findByRole('table', { name: 'Payouts' })
    const [, first] = within(table).getAllByRole('row')
    expect(first).toHaveTextContent('1')
    expect(first).toHaveTextContent('$1,650.00')
    expect(first).toHaveTextContent('100%')
    expect(screen.getByRole('group', { name: 'Lifetime payouts' })).toHaveTextContent('$1,650.00')
  })

  it('logs a payout with the split from the rules, showing the net as it is typed', async () => {
    const { user } = await open({ account: 2, tab: 'payouts' })
    const form = await screen.findByRole('form', { name: 'Log a payout' })
    expect(await within(form).findByRole('heading', { name: 'Log payout #2' })).toBeInTheDocument()
    expect(within(form).getByLabelText('Your split (%)')).toHaveValue('100') // Apex pays 100/0
    await user.type(within(form).getByLabelText('Gross ($)'), '3,000')
    expect(within(form).getByText(/You receive/)).toHaveTextContent('You receive $3,000.00 before fees.')
    const split = within(form).getByLabelText('Your split (%)')
    await user.clear(split)
    await user.type(split, '90')
    expect(within(form).getByText(/You receive/)).toHaveTextContent('You receive $2,700.00 before fees.')
    await user.click(within(form).getByRole('button', { name: 'Log payout' }))
    await waitFor(async () => expect((await demoSource.payouts(2)).payouts).toHaveLength(2))
    const logged = (await demoSource.payouts(2)).payouts[1]
    expect([logged?.payout_number, logged?.gross_amount, logged?.profit_split_pct, logged?.amount]).toEqual([
      2,
      '3000.00',
      '90.00',
      '2700.00',
    ])
  })

  it('asks for the split when the rules give none', async () => {
    const { user } = await open({ account: 4, tab: 'payouts' }) // no rules
    const form = await screen.findByRole('form', { name: 'Log a payout' })
    expect(within(form).getByLabelText('Your split (%)')).toHaveValue('')
    await user.type(within(form).getByLabelText('Gross ($)'), '500')
    await user.click(within(form).getByRole('button', { name: 'Log payout' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent("Type your split: this account's rules give none.")
  })
})
