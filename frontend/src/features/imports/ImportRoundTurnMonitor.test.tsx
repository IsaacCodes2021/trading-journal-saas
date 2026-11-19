import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { resetDemoImports } from '../../lib/demo/imports'
import { demoSource } from '../../lib/demo/source'
import type { ImportPreview, ImportRecord, LogAccount } from '../../lib/data/model'
import { openApp, stubApi } from '../../test-utils'

/*
 * Importing the Round Turn Monitor's log from NinjaTrader (docs/design/round-turn-monitor-import-plan.md, section 2):
 * the dialog's reminder, the preview in round turns, and choosing which NinjaTrader account of the log to import. The
 * sample journal reads Tradovate files only, so the log's answers here are the server's, given by hand.
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

const LABEL = 'NinjaTrader: Round Turn Monitor log'
const LUCID = 'LFE0000000000001'
const logFile = () => new File(['date_local,account\n'], 'RoundTurnLog.csv', { type: 'text/csv' })

const lucid: LogAccount = { name: LUCID, round_turns: 18, practice: false, here: 0, elsewhere: null }
const sim: LogAccount = { name: 'Sim101', round_turns: 5, practice: true, here: 0, elsewhere: null }

/** What the server says about the real log of 2026-11-18, with the extras each test turns on. */
function logPreview(changes: Partial<ImportPreview> = {}): ImportPreview {
  return {
    platform: 'round_turn_monitor',
    recognised: false,
    suggested_platform: null,
    file_name: 'RoundTurnLog.csv',
    file_count: 1,
    file_bytes: 4092,
    fatal: null,
    rows_parsed: 18,
    rows_added: 18,
    duplicates: 0,
    duplicates_in_file: 0,
    duplicates_other_accounts: {},
    matches_other_accounts: {},
    skipped_by_status: {},
    one_page: false,
    open_positions: [],
    file_commission: '28.00',
    commissions: [],
    time_zone: 'America/Los_Angeles',
    log_accounts: [lucid, sim],
    log_account: LUCID,
    errors: [],
    warnings: [],
    trades_built: 18,
    trades_changed: 0,
    trades_removed: 0,
    rows: [
      {
        line: 4,
        status: 'new',
        account: null,
        symbol: 'NQZ6',
        bought_at: '11/17/2026 22:29:58',
        sold_at: '11/17/2026 22:32:24',
        qty: 1,
        buy_price: '27218.75',
        sell_price: '27223.75',
        pnl: '100.00',
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

/** Chooses the log through the dialog; `previews` are the server's answers in turn (the last one repeats). */
async function chooseLog(...previews: ImportPreview[]) {
  const spy = vi.spyOn(demoSource, 'previewImport')
  for (const preview of previews.slice(0, -1)) spy.mockResolvedValueOnce(preview)
  spy.mockResolvedValue(previews[previews.length - 1] ?? logPreview())
  const { user, button } = await openImport()
  const dialog = await ask(user, button)
  await user.selectOptions(within(dialog).getByLabelText('Platform'), LABEL)
  await user.click(within(dialog).getByRole('button', { name: 'Choose files' }))
  await user.upload(screen.getByLabelText('Export files'), [logFile()])
  return { user, spy }
}

describe("the dialog's reminder", () => {
  it('says which file to choose, that the same log can be chosen again, and how times are read', async () => {
    const { user, button } = await openImport()
    const input = screen.getByLabelText('Export files') as HTMLInputElement
    const dialog = await ask(user, button)
    await user.selectOptions(within(dialog).getByLabelText('Platform'), LABEL)
    expect(dialog).toHaveTextContent(
      'Choose RoundTurnLog.csv in Documents › NinjaTrader 8 › RoundTurnLog, not a file from the Export button. ' +
        'If RoundTurnLog_overflow.csv is there too, choose both.',
    )
    expect(dialog).toHaveTextContent('You can choose the same log every time: only new round turns are added.')
    expect(dialog).toHaveTextContent('Times are read as')
    const guide = within(dialog).getByRole('link', { name: 'How to import from Round Turn Monitor' })
    expect(guide).toHaveAttribute('href', 'https://roundturn.app/docs/import/round-turn-monitor/')
    const click = vi.spyOn(input, 'click')
    await user.click(within(dialog).getByRole('button', { name: 'Choose files' }))
    expect(click).toHaveBeenCalledTimes(1)
    expect(input.multiple).toBe(true) // the log and its overflow file
  })

  it('in the demo, asks the person to sign up instead', async () => {
    sessionStorage.setItem('roundturn-demo', '1')
    const { user, button } = await openImport()
    const dialog = await ask(user, button)
    await user.selectOptions(within(dialog).getByLabelText('Platform'), LABEL)
    expect(dialog).toHaveTextContent('Sign up to import the Round Turn Monitor’s log.')
    expect(within(dialog).getByRole('button', { name: 'Choose files' })).toBeDisabled()
  })
})

describe("the log's preview", () => {
  it('reads the log in round turns, from the NinjaTrader account the server picked', async () => {
    const { spy } = await chooseLog(logPreview())
    await screen.findByRole('region', { name: 'Preview' })
    expect(spy).toHaveBeenCalledWith(1, [expect.any(File)], 'round_turn_monitor', null)
    expect(screen.getByLabelText('What was read')).toHaveTextContent(
      `Round Turn Monitor · 18 round turns, 18 new · from ${LUCID} · into Apex 50K #1 · times in America/Los_Angeles`,
    )
    const summary = screen.getByRole('region', { name: 'Preview summary' })
    expect(within(summary).getByText('New round turns').parentElement).toHaveTextContent('18')
    const preview = screen.getByRole('region', { name: 'Preview' })
    expect(preview).toHaveTextContent('One row per round turn.')
    expect(within(preview).getByRole('button', { name: 'Import 18 round turns' })).toBeEnabled()
    const notes = screen.getByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent('The log’s own commissions for these round turns add up to $28.00.')
    // The log's accounts, with the one read chosen; Sim101 is listed as practice.
    const picker = screen.getByRole('region', { name: 'NinjaTrader account' })
    const select = within(picker).getByLabelText('NinjaTrader account to import into Apex 50K #1')
    expect(select).toHaveValue(LUCID)
    expect(within(picker).getByRole('option', { name: 'Sim101 · 5 round turns · practice' })).toBeInTheDocument()
  })

  it('reads another account of the log when it is chosen, and imports exactly that one', async () => {
    const { user, spy } = await chooseLog(
      logPreview(),
      logPreview({ log_account: 'Sim101', rows_parsed: 5, rows_added: 5, trades_built: 5 }),
    )
    const picker = await screen.findByRole('region', { name: 'NinjaTrader account' })
    await user.selectOptions(within(picker).getByLabelText(/NinjaTrader account to import into/), 'Sim101')
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith(1, [expect.any(File)], 'round_turn_monitor', 'Sim101'))
    const record: ImportRecord = {
      id: 9,
      platform: 'round_turn_monitor',
      imported_at: '2026-11-18T17:00:00Z',
      file_name: 'RoundTurnLog.csv',
      account: (await demoSource.imports())[0]?.account as ImportRecord['account'],
      rows_added: 5,
      trades_built: 5,
      undone_at: null,
    }
    const commit = vi.spyOn(demoSource, 'commitImport').mockResolvedValue(record)
    await user.click(await screen.findByRole('button', { name: 'Import 5 round turns' }))
    expect(
      await screen.findByText('Imported 5 round turns from Round Turn Monitor into Apex 50K #1, making 5 trades.'),
    ).toBeVisible()
    expect(commit).toHaveBeenCalledWith(1, [expect.any(File)], 'round_turn_monitor', 'Sim101')
  })

  it('asks which account when the log has several real ones, instead of showing an error', async () => {
    const second: LogAccount = { ...lucid, name: 'LFE0000000000002' }
    const question = 'This log has round turns from 3 accounts. Choose which one to import into this account.'
    const { user, spy } = await chooseLog(
      logPreview({ fatal: question, log_account: null, log_accounts: [lucid, second, sim], rows: [] }),
      logPreview({ log_accounts: [lucid, second, sim], log_account: 'LFE0000000000002' }),
    )
    const picker = await screen.findByRole('region', { name: 'NinjaTrader account' })
    expect(picker).toHaveTextContent(question)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Preview' })).not.toBeInTheDocument()
    const select = within(picker).getByLabelText(/NinjaTrader account to import into/)
    expect(select).toHaveValue('')
    await user.selectOptions(select, 'LFE0000000000002 · 18 round turns')
    await waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith(1, [expect.any(File)], 'round_turn_monitor', 'LFE0000000000002'),
    )
    expect(await screen.findByRole('region', { name: 'Preview' })).toBeInTheDocument()
  })

  it('shows no account list for a log of one account', async () => {
    await chooseLog(logPreview({ log_accounts: [lucid] }))
    await screen.findByRole('region', { name: 'Preview' })
    expect(screen.queryByRole('region', { name: 'NinjaTrader account' })).not.toBeInTheDocument()
  })

  it('offers the rate the add-on charges when the account charges no commission', async () => {
    const setFee = vi.spyOn(demoSource, 'setFee')
    const check = {
      root: 'NQ',
      round_trips: '19',
      file_total: '28.00',
      file_rate: '3.50',
      schedule_rate: '0.00',
      schedule_total: '0.00',
    }
    const { user, spy } = await chooseLog(logPreview({ commissions: [check] }))
    const notes = await screen.findByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent(
      'Your commission setting for NQ on Apex 50K #1 is $0.00, but the Round Turn Monitor charges $3.50 per contract ' +
        'round trip. Roundturn uses your setting, so your net P&L would be too high.',
    )
    await user.click(within(notes).getByRole('button', { name: 'Use $3.50 per contract for NQ' }))
    await waitFor(() => expect(setFee).toHaveBeenCalledWith(1, 'NQ', '3.50'))
    // Worked out again for the same account of the log.
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith(1, [expect.any(File)], 'round_turn_monitor', LUCID))
  })

  it('warns when the round turns are already in another account', async () => {
    await chooseLog(logPreview({ matches_other_accounts: { 'Lucid 13': 18 } }))
    const notes = await screen.findByRole('region', { name: 'Things to check' })
    expect(notes).toHaveTextContent(
      'These 18 round turns are already in Lucid 13. Check that you chose the right NinjaTrader account.',
    )
  })

  it("refuses the Export button's file in the server's words", async () => {
    const exportFile =
      "This is the Round Turn Monitor's Export file, which has no contract or commission. Choose RoundTurnLog.csv " +
      'instead (in Documents\\NinjaTrader 8\\RoundTurnLog).'
    await chooseLog(logPreview({ fatal: exportFile, log_accounts: [], log_account: null, rows: [] }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("This is the Round Turn Monitor's Export file")
    expect(within(alert).getByRole('button', { name: 'Choose another file' })).toBeInTheDocument()
  })
})
