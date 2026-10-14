import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { go } from '../../lib/auth'
import { openApp } from '../../test-utils'
import { changes, stubAdmin, template, type AdminStubOptions, type Call } from './admin-stub'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const APEX = template(
  7,
  {
    firm: 'Apex Trader Funding',
    program: 'EOD Trailing (Apex 4.0)',
    account_size: '50K',
    phase: 'funded',
    rule_version: 'Apex 4.0 (new accounts)',
    effective_from: '2026-03-01',
    max_loss_usd: '2000.00',
    drawdown_type: 'eod_trailing_realtime_enforced',
    min_trading_days: 5,
  },
  {
    flags: [
      { field: 'activation_fee_usd', reason: 'conflict: ETF says $99; PTV says $129', kind: 'conflict' },
      { field: 'drawdown_after_payout', reason: 'unknown: not stated', kind: 'unknown' },
    ],
    source_notes: { max_payout_usd: 'up to 1500' },
  },
)
const LUCID = template(9, {
  firm: 'LucidTrading',
  program: 'LucidPro',
  account_size: '50K',
  phase: 'funded',
  rule_version: 'current',
  max_loss_usd: '2000.00',
})
const TOPSTEP = template(11, {
  firm: 'Topstep',
  program: 'Trading Combine',
  account_size: '150K',
  phase: 'evaluation',
  rule_version: '2026',
})
const TEMPLATES = [APEX, LUCID, TOPSTEP]

async function open(options: AdminStubOptions = {}, search: Record<string, string | number> = {}) {
  const calls = stubAdmin({ templates: TEMPLATES, ...options })
  vi.spyOn(go, 'to').mockImplementation(() => undefined)
  const app = openApp('/admin/rules', search)
  return { ...app, calls, user: userEvent.setup() }
}

const listing = () => screen.findByRole('region', { name: 'Rule templates' })
const editor = () => screen.findByRole('region', { name: 'Template editor' })
const lists = (calls: Call[]) => calls.filter((c) => c.path === '/api/admin/v1/rule-templates')
const field = async (name: string) => within(await editor()).findByLabelText(name)

describe('the list', () => {
  it('is in the operator navigation and lists every template with how many fields to check', async () => {
    await open()
    expect(await screen.findByRole('link', { name: 'Firm rules' })).toHaveAttribute('data-status', 'active')
    const table = await listing()
    expect(await within(table).findByText('3 templates')).toBeInTheDocument()
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.textContent)).toEqual([
      'Apex Trader Funding · EOD Trailing (Apex 4.0)50KFundedApex 4.0 (new accounts)from Mar 1, 20262',
      'LucidTrading · LucidPro50KFundedcurrentstart not known0',
      'Topstep · Trading Combine150KEvaluation2026start not known0',
    ])
    expect(screen.getByText('Choose a template to check or edit its values and flags.')).toBeInTheDocument()
  })

  it('searches on the server, and the search is kept in the address', async () => {
    const { calls, user, router } = await open()
    const box = await screen.findByRole('searchbox', { name: /Search templates/ })
    await user.type(box, 'lucid{Enter}')
    await waitFor(() => expect(lists(calls).at(-1)?.query).toEqual({ q: 'lucid', limit: '50', offset: '0' }))
    const table = await listing()
    await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(2))
    expect(within(table).getByText('1 template')).toBeInTheDocument()
    expect(router.state.location.search).toEqual({ q: 'lucid' })
  })

  it('says so when nothing matches', async () => {
    await open({}, { q: 'tradeify' })
    expect(await screen.findByText('No template matches. Try fewer words.')).toBeInTheDocument()
  })

  it('says so when there is nothing yet, and where the templates come from', async () => {
    await open({ templates: [] })
    expect(await screen.findByText('No templates yet. Import the research CSV.')).toBeInTheDocument()
  })
})

describe('a template', () => {
  it('opens beside the list with every value, its flags in red and the source’s own words', async () => {
    const { user, router } = await open()
    await user.click(await within(await listing()).findByRole('button', { name: /Apex Trader Funding/ }))
    const panel = await editor()
    expect(router.state.location.search).toEqual({ template: 7 })
    expect(
      within(panel).getByRole('heading', { name: 'Apex Trader Funding · EOD Trailing (Apex 4.0) · 50K · Funded' }),
    ).toBeInTheDocument()
    expect(panel).toHaveTextContent(
      'Version Apex 4.0 (new accounts) · last changed Oct 14, 2026 by the owner’s command line',
    )
    expect(await field('Max loss ($)')).toHaveValue('2000.00')
    expect(await field('Minimum trading days')).toHaveValue('5')
    const fee = await field('Activation fee ($)')
    expect(fee).toHaveValue('')
    expect(fee).toHaveAttribute('title', 'conflict: ETF says $99; PTV says $129')
    expect(fee).toHaveClass('border-neg')
    expect(await field('Maximum payout ($)')).toHaveAttribute('title', 'The source says: up to 1500')
    expect(within(panel).getByRole('note')).toHaveTextContent('3 fields in red could not be confirmed by the research.')
    const flagged = within(panel).getByRole('list', { name: 'Flagged fields' })
    expect(
      within(flagged)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual([
      'Activation fee · conflict: ETF says $99; PTV says $129Clear',
      'After a payout · unknown: not statedClear',
    ])
    expect(within(panel).getByRole('button', { name: 'Save template' })).toBeDisabled() // nothing to save yet
  })

  it('saves an edited value and flags, and the panel shows what was stored', async () => {
    const { calls, user } = await open({}, { template: 7 })
    const loss = await field('Max loss ($)')
    await user.clear(loss)
    await user.type(loss, '2,500')
    const panel = await editor()
    await user.click(within(panel).getByRole('button', { name: 'Clear the flag on Activation fee' }))
    await user.selectOptions(within(panel).getByRole('combobox', { name: 'Field to flag' }), 'flat_by_time')
    await user.selectOptions(within(panel).getByRole('combobox', { name: 'Why' }), 'third-party only')
    await user.type(within(panel).getByRole('textbox', { name: 'What the sources say' }), 'Tanto')
    await user.click(within(panel).getByRole('button', { name: 'Add flag' }))
    expect(within(panel).getByRole('status')).toHaveTextContent('Unsaved changes')
    await user.click(within(panel).getByRole('button', { name: 'Save template' }))

    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    const [put] = changes(calls)
    expect(put?.method).toBe('PUT')
    expect(put?.path).toBe('/api/admin/v1/rule-templates/7')
    expect(put?.csrf).not.toBeNull()
    const sent = put?.body as { values: Record<string, unknown>; flags: unknown[] }
    expect(sent.values).toMatchObject({ firm: 'Apex Trader Funding', max_loss_usd: '2500.00', min_trading_days: 5 })
    expect(sent.values['activation_fee_usd']).toBeNull()
    expect(sent.flags).toEqual([
      { field: 'drawdown_after_payout', reason: 'unknown: not stated' },
      { field: 'flat_by_time', reason: 'third-party only (Tanto)' },
    ])
    const saved = await editor()
    await waitFor(() => expect(within(saved).getByRole('status')).toHaveTextContent('Saved'))
    expect(saved).toHaveTextContent('last changed Oct 14, 2026 by you')
    expect(await field('Max loss ($)')).toHaveValue('2500.00')
    expect(await field('Flat by')).toHaveAttribute('title', 'third-party only (Tanto)')
    expect(await field('Activation fee ($)')).not.toHaveAttribute('title')
  })

  it('keeps the research’s gaps but refuses a value of the wrong kind or a missing name, before sending', async () => {
    const { calls, user } = await open({}, { template: 7 })
    await user.clear(await field('Max loss ($)')) // a drawdown type with no max loss is a gap, not an error, here
    const days = await field('Minimum trading days')
    await user.clear(days)
    await user.type(days, 'five')
    await user.clear(await field('Firm'))
    const panel = await editor()
    await user.click(within(panel).getByRole('button', { name: 'Save template' }))
    expect(await within(panel).findByText('Firm is required.')).toBeInTheDocument()
    expect(within(panel).getByText('Minimum trading days must be a whole number, 0 or more.')).toBeInTheDocument()
    expect(within(panel).queryByText(/Give the max loss/)).toBeNull()
    expect(changes(calls)).toEqual([])

    await user.type(await field('Firm'), 'Apex Trader Funding')
    await user.clear(days)
    await user.type(days, '5')
    await user.click(within(panel).getByRole('button', { name: 'Save template' }))
    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    expect((changes(calls)[0]?.body as { values: Record<string, unknown> }).values['max_loss_usd']).toBeNull()
  })

  it('shows the server’s refusal next to the field, or in words, and keeps what was typed', async () => {
    const { user } = await open(
      {
        templateEditFails: {
          status: 422,
          detail: 'Some fields are not valid.',
          errors: [{ field: 'values.max_loss_usd', message: 'Max loss must be a number.' }],
        },
      },
      { template: 7 },
    )
    const loss = await field('Max loss ($)')
    await user.clear(loss)
    await user.type(loss, '2400')
    const panel = await editor()
    await user.click(within(panel).getByRole('button', { name: 'Save template' }))
    expect(await within(panel).findByText('Max loss must be a number.')).toBeInTheDocument()
    expect(within(panel).getByText('Some fields are not valid.')).toBeInTheDocument()
    expect(loss).toHaveValue('2400')
  })

  it('says when another template already has that name', async () => {
    const detail = 'Another rule template already has that firm, program, size, phase and version.'
    const { user } = await open({ templateEditFails: { status: 409, detail } }, { template: 9 })
    const version = await field('Rule version')
    await user.clear(version)
    await user.type(version, 'Apex 4.0 (new accounts)')
    await user.click(within(await editor()).getByRole('button', { name: 'Save template' }))
    expect(await within(await editor()).findByText(detail)).toBeInTheDocument()
  })

  it('does not throw away changes when another template is clicked, until they are discarded', async () => {
    const { user, router } = await open({}, { template: 7 })
    const loss = await field('Max loss ($)')
    await user.clear(loss)
    await user.type(loss, '1')
    const table = await listing()
    await user.click(within(table).getByRole('button', { name: /LucidTrading/ }))
    expect(await within(table).findByRole('alert')).toHaveTextContent('The open template has unsaved changes.')
    expect(router.state.location.search).toEqual({ template: 7 })

    await user.click(within(await editor()).getByRole('button', { name: 'Discard changes' }))
    expect(await field('Max loss ($)')).toHaveValue('2000.00')
    expect(within(table).queryByRole('alert')).toBeNull()
    await user.click(within(table).getByRole('button', { name: /LucidTrading/ }))
    await waitFor(() => expect(router.state.location.search).toEqual({ template: 9 }))
    expect(await within(await editor()).findByRole('heading', { name: /LucidTrading/ })).toBeInTheDocument()
  })

  it('says so when a template cannot be found', async () => {
    await open({}, { template: 999 })
    expect(await within(await editor()).findByRole('alert')).toHaveTextContent('There is no such rule template.')
  })
})

describe('the import', () => {
  const csv = () => new File(['firm,program\nApex,EOD\n'], 'rules.csv', { type: 'text/csv' })
  const NAME = 'Apex Trader Funding · EOD Trailing (Apex 4.0) · 50K · funded · Apex 4.0 (new accounts)'

  it('checks the file first, names the edits it would replace, then imports on a second click', async () => {
    const { calls, user } = await open({
      importAnswer: (dryRun) => ({
        applied: !dryRun,
        inserted: 2,
        updated: 1,
        unchanged: 368,
        changed: [NAME],
        problems: ['Line 40: has no phase it can use (’sim’).'],
      }),
    })
    const card = await screen.findByRole('region', { name: 'Import the research CSV' })
    expect(within(card).getByRole('button', { name: 'Check the file' })).toBeDisabled()
    await user.upload(within(card).getByLabelText('Research CSV file'), csv())
    expect(within(card).queryByRole('button', { name: 'Import' })).toBeNull() // checked first, always
    await user.click(within(card).getByRole('button', { name: 'Check the file' }))

    const status = await within(card).findByRole('status')
    expect(status).toHaveTextContent(
      'The file would add or change rule sets: 2 new, 1 changed, 368 unchanged. Nothing is kept until you import it.',
    )
    expect(within(status).getByRole('list', { name: 'Changed templates' })).toHaveTextContent(NAME)
    expect(within(status).getByRole('list', { name: 'Rows that cannot be read' })).toHaveTextContent('Line 40')
    const [check] = changes(calls)
    expect([check?.method, check?.path, check?.query]).toEqual([
      'POST',
      '/api/admin/v1/rule-templates/import',
      { dry_run: 'true' },
    ])
    // The file is a multipart part of its own. (The test environment's File is not a browser's, so its bytes are not
    // serialized here; the browser check and the server's tests cover the real upload.)
    expect((check?.body as { multipart: string }).multipart).toContain('name="file"')
    expect(check?.csrf).not.toBeNull()

    await user.click(within(card).getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(within(card).getByRole('status')).toHaveTextContent('Imported: 2 new, 1 changed'))
    expect(changes(calls).map((c) => c.query)).toEqual([{ dry_run: 'true' }, {}])
    expect(within(card).queryByRole('button', { name: 'Import' })).toBeNull()
  })

  it('offers no import when the file would change nothing', async () => {
    const { user } = await open()
    const card = await screen.findByRole('region', { name: 'Import the research CSV' })
    await user.upload(within(card).getByLabelText('Research CSV file'), csv())
    await user.click(within(card).getByRole('button', { name: 'Check the file' }))
    expect(await within(card).findByRole('status')).toHaveTextContent('Nothing would change (3 unchanged).')
    expect(within(card).queryByRole('button', { name: 'Import' })).toBeNull()
  })

  it('shows the server’s refusal of the file', async () => {
    const { user } = await open({ changeFails: { status: 413, detail: 'That file is too large (5 MB at most).' } })
    const card = await screen.findByRole('region', { name: 'Import the research CSV' })
    await user.upload(within(card).getByLabelText('Research CSV file'), csv())
    await user.click(within(card).getByRole('button', { name: 'Check the file' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('That file is too large (5 MB at most).')
  })
})
