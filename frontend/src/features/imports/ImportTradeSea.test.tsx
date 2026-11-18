import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { resetDemoImports } from '../../lib/demo/imports'
import { demoSource } from '../../lib/demo/source'
import type { ImportPreview, ImportRecord } from '../../lib/data/model'
import { openApp, stubApi } from '../../test-utils'

/*
 * The Import page's platform question and TradeSea's preview (docs/design/tradesea-import-plan.md, sections 2 and 4).
 * The sample journal reads Tradovate files only, so TradeSea's answers here are the server's, given by hand.
 */

afterEach(() => {
  vi.restoreAllMocks()
  resetDemoAccounts()
  resetDemoImports()
  try {
    sessionStorage.clear()
  } catch {
    // no storage: nothing to clear
  }
})

const page = (n: number) => new File([`"Time","Symbol"\n${n}`], `page${n}.csv`, { type: 'text/csv' })

/** What the server says about 2026-11-18's two pages, with the extras each test turns on. */
function tradeseaPreview(changes: Partial<ImportPreview> = {}): ImportPreview {
  return {
    platform: 'tradesea',
    recognised: false,
    suggested_platform: null,
    file_name: 'page1.csv, page2.csv',
    file_count: 2,
    file_bytes: 1800,
    fatal: null,
    rows_parsed: 17,
    rows_added: 17,
    duplicates: 0,
    duplicates_in_file: 0,
    duplicates_other_accounts: {},
    matches_other_accounts: {},
    skipped_by_status: {},
    one_page: false,
    open_positions: [],
    file_commission: '165.00',
    commissions: [],
    time_zone: 'PST',
    errors: [],
    warnings: [],
    trades_built: 5,
    trades_changed: 0,
    trades_removed: 0,
    rows: [
      {
        line: 1,
        status: 'new',
        account: null,
        symbol: 'NQ',
        bought_at: '11/18/2026 08:44:47',
        sold_at: '11/18/2026 08:44:52',
        qty: 5,
        buy_price: '27080.45',
        sell_price: '27070.40',
        pnl: '-1005.00',
      },
    ],
    rows_shown_limit: 100,
    ...changes,
  }
}

async function openImport() {
  stubApi()
  openApp('/import')
  await screen.findByRole('heading', { name: 'Import trades' })
  const user = userEvent.setup({ applyAccept: false })
  const button = screen.getByRole('button', { name: 'Choose files' })
  await waitFor(() => expect(button).toBeEnabled()) // the accounts have loaded
  return { user, button }
}

async function ask(user: ReturnType<typeof userEvent.setup>, button: HTMLElement) {
  await user.click(button)
  return screen.findByRole('dialog', { name: 'Where are these trades from?' })
}

async function chooseTradeSea(files: File[] = [page(1), page(2)], preview = tradeseaPreview()) {
  const spy = vi.spyOn(demoSource, 'previewImport').mockResolvedValue(preview)
  const { user, button } = await openImport()
  const dialog = await ask(user, button)
  await user.selectOptions(within(dialog).getByLabelText('Platform'), 'TradeSea: Orders (Filled)')
  await user.click(within(dialog).getByRole('button', { name: 'Choose files' }))
  await user.upload(screen.getByLabelText('Export files'), files)
  await screen.findByRole('region', { name: 'Preview' })
  return { user, spy }
}

describe('where the trades come from', () => {
  it('Choose files asks first, starting on the platform this account last imported from', async () => {
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    expect(within(dialog).getByLabelText('Platform')).toHaveValue('tradovate') // Apex 50K #1's imports are Tradovate
    expect(within(dialog).getByRole('button', { name: 'Choose files' })).toBeEnabled()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(button).toHaveFocus()
  })

  it('chooses nothing for an account with no imports yet, and keeps the main button off until one is picked', async () => {
    vi.spyOn(demoSource, 'imports').mockResolvedValue([])
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    expect(within(dialog).getByLabelText('Platform')).toHaveValue('')
    expect(within(dialog).getByRole('option', { name: 'Choose a platform' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Choose files' })).toBeDisabled()
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'TradeSea: Orders (Filled)')
    expect(within(dialog).getByRole('button', { name: 'Choose files' })).toBeEnabled()
  })

  it('reminds a TradeSea trader to export every page, and links the export guide', async () => {
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'TradeSea: Orders (Filled)')
    expect(dialog).toHaveTextContent('In TradeSea, open Orders, select Filled, and export.')
    expect(dialog).toHaveTextContent('export every page')
    expect(dialog).toHaveTextContent('Hold Ctrl (⌘ on a Mac) to choose more than one file.')
    const guide = within(dialog).getByRole('link', { name: 'How to export from TradeSea' })
    expect(guide).toHaveAttribute('href', 'https://roundturn.app/docs/import/tradesea/')
    expect(guide).toHaveAttribute('target', '_blank')
    expect(within(dialog).queryByText(/Times are read as/)).not.toBeInTheDocument() // Tradovate's line only
  })

  it('opens the file window for several files for TradeSea, one for Tradovate', async () => {
    const { user, button } = await openImport()
    const input = screen.getByLabelText('Export files') as HTMLInputElement
    const click = vi.spyOn(input, 'click')
    let dialog = await ask(user, button)
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'TradeSea: Orders (Filled)')
    await user.click(within(dialog).getByRole('button', { name: 'Choose files' }))
    expect(click).toHaveBeenCalledTimes(1)
    expect(input.multiple).toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    dialog = await ask(user, button) // cancelling the file window changed nothing, and the choice is kept
    expect(within(dialog).getByLabelText('Platform')).toHaveValue('tradesea')
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'Tradovate: Performance report')
    await user.click(within(dialog).getByRole('button', { name: 'Choose files' }))
    expect(input.multiple).toBe(false)
    expect(screen.queryByRole('region', { name: 'Preview' })).not.toBeInTheDocument()
  })

  it('a platform not listed says which ones are, and how to ask for another', async () => {
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'My platform isn’t listed')
    expect(dialog).toHaveTextContent('We import Tradovate and TradeSea today')
    expect(within(dialog).getByRole('link', { name: 'hello@roundturn.app' })).toHaveAttribute(
      'href',
      'mailto:hello@roundturn.app',
    )
    expect(within(dialog).queryByRole('button', { name: 'Choose files' })).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('in the demo, TradeSea asks the person to sign up instead', async () => {
    sessionStorage.setItem('roundturn-demo', '1')
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    await user.selectOptions(within(dialog).getByLabelText('Platform'), 'TradeSea: Orders (Filled)')
    expect(dialog).toHaveTextContent('Sign up to import TradeSea files.')
    expect(within(dialog).getByRole('button', { name: 'Choose files' })).toBeDisabled()
  })
})

describe("TradeSea's preview", () => {
  it('sends every page as one TradeSea import and says what was read', async () => {
    const { spy } = await chooseTradeSea()
    expect(spy).toHaveBeenCalledWith(1, [expect.any(File), expect.any(File)], 'tradesea')
    expect(screen.getByLabelText('What was read')).toHaveTextContent(
      'TradeSea · 2 files · 17 filled orders, 17 new · into Apex 50K #1 · times in PST',
    )
    expect(screen.getByText('2 files')).toBeInTheDocument()
    const summary = screen.getByRole('region', { name: 'Preview summary' })
    expect(within(summary).getByText('New orders').parentElement).toHaveTextContent('17')
    expect(within(summary).getByText('Resulting trades').parentElement).toHaveTextContent('5')
    const preview = screen.getByRole('region', { name: 'Preview' })
    expect(preview).toHaveTextContent('The fill pairs these orders make.')
    expect(within(preview).getByRole('button', { name: 'Import 17 orders' })).toBeEnabled()
    const notes = screen.getByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent('These files’ commissions add up to $165.00. If TradeSea’s Total Charges')
  })

  it('flags a single page, and Add files adds the other pages to the same import', async () => {
    const first = page(1)
    const { user, spy } = await chooseTradeSea([first], tradeseaPreview({ one_page: true, file_count: 1 }))
    const notes = screen.getByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent('This looks like one page of TradeSea’s list.')
    const input = screen.getByLabelText('Export files') as HTMLInputElement
    await user.click(within(notes).getByRole('button', { name: 'Add files' }))
    expect(input.multiple).toBe(true)
    await user.upload(input, [page(2), first]) // page 1 chosen again counts once
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect((spy.mock.calls[1]?.[1] as File[]).map((f) => f.name)).toEqual(['page1.csv', 'page2.csv'])
    expect(spy.mock.calls[1]?.[2]).toBe('tradesea')
  })

  it('warns about a position left open, orders that did not fill, and copy trading', async () => {
    await chooseTradeSea(
      undefined,
      tradeseaPreview({
        open_positions: [{ symbol: 'NQ', direction: 'long', qty: 1, since: '11/16/2026 09:51:28' }],
        skipped_by_status: { cancelled: 3, rejected: 1 },
        matches_other_accounts: { 'Lucid 13': 17 },
        trades_changed: 2,
        warnings: [{ line: 4, message: 'two fills in the same second' }],
      }),
    )
    const notes = screen.getByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent('These orders end with 1 NQ long still open (opened 11/16/2026 09:51:28).')
    expect(notes).toHaveTextContent('3 cancelled orders and 1 rejected order were skipped: they did not fill.')
    expect(notes).toHaveTextContent('These 17 orders match orders in Lucid 13. That is normal if you copy trades')
    expect(notes).toHaveTextContent('2 trades you already have change')
    expect(notes).toHaveTextContent('Line 4: two fills in the same second')
    const summary = screen.getByRole('region', { name: 'Preview summary' })
    expect(within(summary).getByText('Not filled (skipped)').parentElement).toHaveTextContent('4')
  })

  it('says when the account charges no commission, and sets the files’ rate on one click', async () => {
    const setFee = vi.spyOn(demoSource, 'setFee')
    const check = {
      root: 'NQ',
      round_trips: '33',
      file_total: '165.00',
      file_rate: '5.00',
      schedule_rate: '0.00',
      schedule_total: '0.00',
    }
    const { user, spy } = await chooseTradeSea(undefined, tradeseaPreview({ commissions: [check] }))
    const notes = screen.getByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent(
      'Your commission setting for NQ on Apex 50K #1 is $0.00, but these files show $165.00 in commissions. ' +
        'Roundturn uses your setting, so your net P&L would be $165.00 too high.',
    )
    await user.click(within(notes).getByRole('button', { name: 'Use $5.00 per contract for NQ' }))
    await waitFor(() => expect(setFee).toHaveBeenCalledWith(1, 'NQ', '5.00'))
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2)) // the preview is worked out again with the new setting
    expect(
      await screen.findByText(
        'Apex 50K #1 now charges $5.00 per contract for NQ, and its stored trades were recalculated.',
      ),
    ).toBeInTheDocument()
  })

  it('imports as TradeSea and says so in words', async () => {
    const record: ImportRecord = {
      id: 9,
      platform: 'tradesea',
      imported_at: '2026-11-18T17:00:00Z',
      file_name: 'page1.csv, page2.csv',
      account: (await demoSource.imports())[0]?.account as ImportRecord['account'],
      rows_added: 17,
      trades_built: 5,
      undone_at: null,
    }
    const commit = vi.spyOn(demoSource, 'commitImport').mockResolvedValue(record)
    const { user } = await chooseTradeSea()
    await user.click(screen.getByRole('button', { name: 'Import 17 orders' }))
    expect(await screen.findByText('Imported 17 orders from TradeSea into Apex 50K #1, making 5 trades.')).toBeVisible()
    expect(commit).toHaveBeenCalledWith(1, [expect.any(File), expect.any(File)], 'tradesea')
  })

  it('offers to read files from the other platform as that platform', async () => {
    const refused = tradeseaPreview({
      platform: 'tradovate',
      fatal: 'These look like TradeSea files, not Tradovate.',
      suggested_platform: 'tradesea',
      rows: [],
    })
    const spy = vi
      .spyOn(demoSource, 'previewImport')
      .mockResolvedValueOnce(refused)
      .mockResolvedValue(tradeseaPreview())
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    await user.click(within(dialog).getByRole('button', { name: 'Choose files' })) // Tradovate, this account's last
    await user.upload(screen.getByLabelText('Export files'), [page(1)])
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('These look like TradeSea files, not Tradovate.')
    await user.click(within(alert).getByRole('button', { name: 'Read it as TradeSea' }))
    expect(await screen.findByRole('region', { name: 'Preview' })).toBeInTheDocument()
    expect(spy).toHaveBeenLastCalledWith(1, [expect.any(File)], 'tradesea')
    const again = await ask(user, screen.getByRole('button', { name: 'Replace files' }))
    expect(within(again).getByLabelText('Platform')).toHaveValue('tradesea') // the choice made is kept
  })
})
