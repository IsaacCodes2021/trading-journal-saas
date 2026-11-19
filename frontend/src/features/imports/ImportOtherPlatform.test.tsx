import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { resetDemoImports } from '../../lib/demo/imports'
import { demoSource } from '../../lib/demo/source'
import type { ImportPreview, ImportRecord } from '../../lib/data/model'
import { IMPORT_PROMPT } from '../../lib/otherPlatform'
import { openApp, stubApi } from '../../test-utils'

/*
 * Importing from any other platform: the dialog takes the statement's net P&L and gives the prompt for an AI chatbot;
 * the preview checks the chatbot's file against the statement before the import button works. The sample journal reads
 * Tradovate files only, so the server's answers here are given by hand.
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

const LABEL = 'Other platform (converted with an AI chatbot)'
const roundturnFile = () => new File(['roundturn_version,symbol\n'], 'roundturn-import.csv', { type: 'text/csv' })

/** What the server says about the golden file of the backend tests: 5 round turns, net +$347.08. */
function filePreview(changes: Partial<ImportPreview> = {}): ImportPreview {
  return {
    platform: 'other',
    recognised: false,
    suggested_platform: null,
    file_name: 'roundturn-import.csv',
    file_count: 1,
    file_bytes: 812,
    fatal: null,
    rows_parsed: 5,
    rows_added: 5,
    duplicates: 0,
    duplicates_in_file: 0,
    duplicates_other_accounts: {},
    matches_other_accounts: {},
    skipped_by_status: {},
    one_page: false,
    open_positions: [],
    file_commission: '25.42',
    commissions: [],
    time_zone: 'UTC-05:00',
    log_accounts: [],
    log_account: null,
    file_gross: '372.50',
    file_fees: '25.42',
    file_net: '347.08',
    first_day: '2026-11-18',
    last_day: '2026-11-18',
    errors: [],
    warnings: [],
    trades_built: 5,
    trades_changed: 0,
    trades_removed: 0,
    rows: [
      {
        line: 2,
        status: 'new',
        account: null,
        symbol: 'NQZ6',
        bought_at: '11/18/2026 09:31:05',
        sold_at: '11/18/2026 09:33:10',
        qty: 2,
        buy_price: '21000.25',
        sell_price: '21010.5',
        pnl: '410.00',
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
  const dialog = await screen.findByRole('dialog', { name: 'Where are these trades from?' })
  await user.selectOptions(within(dialog).getByLabelText('Platform'), LABEL)
  return dialog
}

/** Chooses the chatbot's file through the dialog, with `statement` typed; `previews` are the server's answers. */
async function chooseFile(statement: string, ...previews: ImportPreview[]) {
  const spy = vi.spyOn(demoSource, 'previewImport')
  for (const preview of previews.slice(0, -1)) spy.mockResolvedValueOnce(preview)
  spy.mockResolvedValue(previews[previews.length - 1] ?? filePreview())
  const { user, button } = await openImport()
  const dialog = await ask(user, button)
  await user.type(within(dialog).getByLabelText('Net P&L on your platform’s statement'), statement)
  await user.click(within(dialog).getByRole('button', { name: 'Choose file' }))
  await user.upload(screen.getByLabelText('Export files'), [roundturnFile()])
  return { user, spy }
}

describe('the dialog', () => {
  it('asks for the statement first, then opens the file window for one file', async () => {
    const { user, button } = await openImport()
    const input = screen.getByLabelText('Export files') as HTMLInputElement
    const click = vi.spyOn(input, 'click')
    const dialog = await ask(user, button)
    const choose = within(dialog).getByRole('button', { name: 'Choose file' })
    expect(choose).toBeDisabled()
    const statement = within(dialog).getByLabelText('Net P&L on your platform’s statement')
    await user.type(statement, 'lots')
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Type an amount like 1234.50, or -125 for a loss.')
    expect(choose).toBeDisabled()
    await user.clear(statement)
    await user.type(statement, '($1,234.50)')
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
    await user.click(choose)
    expect(click).toHaveBeenCalledTimes(1)
    expect(input.multiple).toBe(false)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('copies the prompt, shows it on request, and links the guide and our address', async () => {
    const { user, button } = await openImport()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const dialog = await ask(user, button)
    await user.click(within(dialog).getByRole('button', { name: 'Copy prompt' }))
    expect(writeText).toHaveBeenCalledWith(IMPORT_PROMPT)
    expect(await within(dialog).findByText('Copied.')).toBeVisible()
    expect(within(dialog).queryByLabelText('The prompt')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'See the prompt' }))
    expect(within(dialog).getByLabelText('The prompt')).toHaveValue(IMPORT_PROMPT)
    expect(within(dialog).getByRole('link', { name: 'How to import from another platform' })).toHaveAttribute(
      'href',
      'https://roundturn.app/docs/import/other/',
    )
    expect(within(dialog).getByRole('link', { name: 'hello@roundturn.app' })).toHaveAttribute(
      'href',
      'mailto:hello@roundturn.app',
    )
    expect(dialog).toHaveTextContent('Your export goes to the chatbot you choose, under its own terms')
  })

  it('shows the prompt to copy by hand when the browser will not copy it', async () => {
    const { user, button } = await openImport()
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const dialog = await ask(user, button)
    await user.click(within(dialog).getByRole('button', { name: 'Copy prompt' }))
    expect(await within(dialog).findByText('Copying did not work: select the prompt and copy it.')).toBeVisible()
    expect(within(dialog).getByLabelText('The prompt')).toHaveValue(IMPORT_PROMPT)
  })

  it('in the demo, asks the person to sign up instead', async () => {
    sessionStorage.setItem('roundturn-demo', '1')
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    expect(dialog).toHaveTextContent('Sign up to import from another platform.')
    expect(within(dialog).queryByRole('button', { name: 'Copy prompt' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Choose file' })).toBeDisabled()
  })

  it('lists Other platform in place of "My platform isn’t listed"', async () => {
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    const options = within(within(dialog).getByLabelText('Platform')).getAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual([
      'Tradovate: Performance report',
      'TradeSea: Orders (Filled)',
      'NinjaTrader: Round Turn Monitor log',
      LABEL,
    ])
  })
})

describe("the file's preview", () => {
  it('shows the round turns and their totals, and imports once the statement matches', async () => {
    const { user, spy } = await chooseFile('347.08', filePreview())
    const preview = await screen.findByRole('region', { name: 'Preview' })
    expect(spy).toHaveBeenCalledWith(1, [expect.any(File)], 'other', null)
    expect(screen.getByLabelText('What was read')).toHaveTextContent(
      'Other platform · 5 round turns, 5 new · into Apex 50K #1 · times in UTC-05:00',
    )
    const summary = screen.getByRole('region', { name: 'Preview summary' })
    expect(within(summary).getByText('New round turns').parentElement).toHaveTextContent('5')
    const check = screen.getByRole('region', { name: 'Check against your statement' })
    expect(check).toHaveTextContent('The file has 5 round turns on Wed, Nov 18, 2026: gross +$372.50, fees $25.42')
    expect(within(check).getByLabelText('Net P&L on your platform’s statement')).toHaveValue('347.08')
    expect(within(check).getByRole('status')).toHaveTextContent('The file’s net P&L matches your statement.')
    expect(preview).toHaveTextContent('One row per round turn.')
    const notes = screen.getByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent('The file’s own fees for these round turns add up to $25.42.')

    const record: ImportRecord = {
      id: 9,
      platform: 'other',
      imported_at: '2026-11-19T17:00:00Z',
      file_name: 'roundturn-import.csv',
      account: (await demoSource.imports())[0]?.account as ImportRecord['account'],
      rows_added: 5,
      trades_built: 5,
      undone_at: null,
    }
    const commit = vi.spyOn(demoSource, 'commitImport').mockResolvedValue(record)
    await user.click(within(preview).getByRole('button', { name: 'Import 5 round turns' }))
    expect(
      await screen.findByText('Imported 5 round turns from your converted file into Apex 50K #1, making 5 trades.'),
    ).toBeVisible()
    expect(commit).toHaveBeenCalledWith(1, [expect.any(File)], 'other', null)
  })

  it('warns when the file and the statement differ, and still lets the trader import', async () => {
    await chooseFile('-350', filePreview())
    const check = await screen.findByRole('region', { name: 'Check against your statement' })
    expect(within(check).getByRole('status')).toHaveTextContent(
      'The file’s net P&L is $347.08, but your statement says -$350.00: $697.08 apart. A trade may be missing, ' +
        'doubled or wrong in the file.',
    )
    expect(screen.getByRole('button', { name: 'Import 5 round turns' })).toBeEnabled()
  })

  it('waits for a statement figure when the file was dropped on the page, then checks it', async () => {
    vi.spyOn(demoSource, 'previewImport').mockResolvedValue(filePreview({ recognised: true }))
    const { user } = await openImport()
    const zone = screen.getByText('Drop your export here').parentElement as HTMLElement
    fireEvent.drop(zone, { dataTransfer: { files: [roundturnFile()] } })
    const check = await screen.findByRole('region', { name: 'Check against your statement' })
    const importButton = screen.getByRole('button', { name: 'Import 5 round turns' })
    expect(importButton).toBeDisabled()
    expect(within(check).getByRole('status')).toHaveTextContent(
      'Type the net P&L your platform’s statement shows for the same dates. The import waits for it.',
    )
    await user.type(within(check).getByLabelText('Net P&L on your platform’s statement'), '347.08')
    expect(importButton).toBeEnabled()
    expect(within(check).getByRole('status')).toHaveTextContent('matches your statement')
  })

  it('says the rows it refused are not in the totals, and lists them', async () => {
    const refused = {
      line: 5,
      message: 'gross_pnl 20.00 is not what the prices make: 1 CL short from 60.15 to 59.95 is 200.00',
    }
    await chooseFile(
      '347.08',
      filePreview({ rows_parsed: 4, rows_added: 4, trades_built: 4, file_net: '151.08', errors: [refused] }),
    )
    const check = await screen.findByRole('region', { name: 'Check against your statement' })
    expect(check).toHaveTextContent('The 1 row below that cannot be imported is not in these totals.')
    expect(within(check).getByRole('status')).toHaveTextContent('$196.00 apart')
    const errors = screen.getByRole('region', { name: 'Rows that cannot be imported' })
    expect(errors).toHaveTextContent('Line 5: gross_pnl 20.00 is not what the prices make')
  })

  it("compares the file's fees with the account's setting in the file's own words", async () => {
    const check = {
      root: 'NQ',
      round_trips: '3',
      file_total: '13.50',
      file_rate: '4.50',
      schedule_rate: '4.00',
      schedule_total: '12.00',
    }
    await chooseFile('347.08', filePreview({ commissions: [check], matches_other_accounts: { 'Lucid 13': 5 } }))
    const notes = await screen.findByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent(
      'The file’s fees for NQ are $4.50 per contract round trip. Your setting on Apex 50K #1 charges $4.00 per ' +
        'contract, and Roundturn uses your setting.',
    )
    expect(notes).toHaveTextContent(
      'These 5 round turns are already in Lucid 13. Check that you chose the right account.',
    )
  })

  it("refuses a file that is not Roundturn's in the server's words", async () => {
    const notOurs =
      "This is not Roundturn's import file. On the Import page, choose Other platform, copy the prompt again, and " +
      'give it to the AI chatbot with your export.'
    await chooseFile('10', filePreview({ fatal: notOurs, rows: [] }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("This is not Roundturn's import file.")
    expect(within(alert).getByRole('button', { name: 'Choose another file' })).toBeInTheDocument()
  })
})
