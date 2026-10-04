import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetDemoImports } from '../../lib/demo/imports'
import { demoSource } from '../../lib/demo/source'
import { csv, csvFile, manyRows } from '../../test-csv'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoImports()
  document.documentElement.removeAttribute('data-theme')
})

async function openImport() {
  stubApi()
  const app = openApp('/import')
  await screen.findByRole('heading', { name: 'Import trades' })
  return { ...app, user: userEvent.setup({ applyAccept: false }) }
}

async function choose(user: ReturnType<typeof userEvent.setup>, file: File) {
  await user.upload(screen.getByLabelText('Choose a Performance CSV'), file)
}

const tile = (label: string) =>
  within(screen.getByRole('region', { name: 'Preview summary' })).getByText(label).parentElement as HTMLElement

describe('the import page', () => {
  it('says what it does, names the account and the timezone the file is read in, and shows the history', async () => {
    await openImport()
    expect(screen.getByText(/Upload a Tradovate Performance CSV/)).toHaveTextContent(/every import can be undone/)
    // The hint states the server's real limit, and does not claim the file stays on the device (the real source uploads it).
    expect(screen.getByText(/Up to 10 MB\. Nothing is saved until you confirm the preview\./)).toBeInTheDocument()
    expect(screen.queryByText(/read on your device/)).not.toBeInTheDocument()
    const select = await screen.findByLabelText('Import into account')
    await waitFor(() =>
      expect(
        within(select)
          .getAllByRole('option')
          .map((o) => o.textContent),
      ).toEqual(['Apex 50K #1', 'Apex 150K', 'Lucid 100K', 'Apex 50K #2']),
    )
    expect(screen.getByText('America/Los_Angeles')).toBeInTheDocument() // the session's timezone
    expect(screen.getByRole('link', { name: 'Change' })).toHaveAttribute('href', '/settings')

    const history = await screen.findByRole('region', { name: 'Import history' })
    const rows = within(history).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Performance_2026-09-30.csv')
    expect(rows[0]).toHaveTextContent('Active')
    expect(rows[3]).toHaveTextContent('Undone')
    expect(within(rows[3] as HTMLElement).queryByRole('button')).not.toBeInTheDocument()
  })

  it('previews a file: counts, rows with their status, and nothing saved yet', async () => {
    const { user } = await openImport()
    await choose(user, csvFile(csv(manyRows(4)), 'my-export.csv'))

    const preview = await screen.findByRole('region', { name: 'Preview' })
    expect(screen.getByText('my-export.csv')).toBeInTheDocument()
    expect(tile('Rows in file')).toHaveTextContent('4')
    expect(tile('New fill pairs')).toHaveTextContent('4')
    expect(tile('Already imported')).toHaveTextContent('0')
    expect(tile('Skipped (other account)')).toHaveTextContent('0')
    expect(tile('Errors')).toHaveTextContent('0')
    expect(tile('Resulting trades')).toHaveTextContent('4')
    expect(within(preview).getAllByRole('row')).toHaveLength(5)
    expect(within(preview).getAllByText('New')).toHaveLength(4)
    expect(within(preview).getByRole('button', { name: 'Import 4 fill pairs' })).toBeEnabled()
    expect(within(preview).getAllByText('20,412.25').length).toBeGreaterThan(0) // prices are grouped
    expect(await demoSource.imports()).toHaveLength(4)
  })

  it('imports on confirmation, tells you, and the history grows', async () => {
    const { user } = await openImport()
    await choose(user, csvFile(csv(manyRows(3)), 'Performance_2026-10-02.csv'))
    await user.click(await screen.findByRole('button', { name: 'Import 3 fill pairs' }))

    expect(await screen.findByText('Imported 3 fill pairs into Apex 50K #1, making 3 trades.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View the trades' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Preview' })).not.toBeInTheDocument()
    const history = screen.getByRole('region', { name: 'Import history' })
    await waitFor(() => expect(within(history).getAllByRole('row')).toHaveLength(6))
    expect(within(history).getAllByRole('row')[1]).toHaveTextContent('Performance_2026-10-02.csv')
  })

  it('shows the same file as already imported, and offers nothing to import', async () => {
    const { user } = await openImport()
    const text = csv(manyRows(3))
    await choose(user, csvFile(text))
    await user.click(await screen.findByRole('button', { name: 'Import 3 fill pairs' }))
    await screen.findByText(/Imported 3 fill pairs/)

    await choose(user, csvFile(text))
    const preview = await screen.findByRole('region', { name: 'Preview' })
    expect(within(preview).getAllByText('Already imported')).toHaveLength(3)
    expect(within(preview).getByRole('status')).toHaveTextContent('Nothing new to import into Apex 50K #1')
    expect(within(preview).getByRole('button', { name: /^Import 0 fill pairs$/ })).toBeDisabled()
  })

  it('works the preview out again when the account changes', async () => {
    const { user } = await openImport()
    const text = csv(manyRows(2))
    await choose(user, csvFile(text))
    await user.click(await screen.findByRole('button', { name: 'Import 2 fill pairs' }))
    await screen.findByText(/Imported 2 fill pairs/)

    await choose(user, csvFile(text))
    await screen.findByRole('region', { name: 'Preview' })
    await user.selectOptions(screen.getByLabelText('Import into account'), 'Apex 150K')
    await waitFor(() => expect(tile('Skipped (other account)')).toHaveTextContent('2'))
    expect(within(screen.getByRole('region', { name: 'Preview' })).getAllByText('Belongs to Apex 50K #1')).toHaveLength(
      2,
    )
  })

  it('lists the rows it cannot read, with their lines, and imports the rest', async () => {
    const { user } = await openImport()
    await choose(user, csvFile(csv([...manyRows(2), { ...manyRows(1, 9000)[0], qty: '0' }])))
    const errors = await screen.findByRole('region', { name: 'Rows that cannot be imported' })
    expect(errors).toHaveTextContent('1 row cannot be imported')
    expect(errors).toHaveTextContent("Line 4: qty: not a positive whole number: '0'")
    expect(tile('Errors')).toHaveTextContent('1')
    expect(screen.getByRole('button', { name: 'Import 2 fill pairs' })).toBeEnabled()
  })

  it('refuses a file that is the wrong kind of export, in words, and lets you choose another', async () => {
    const { user } = await openImport()
    await choose(user, csvFile('Account,Order ID\nA,1'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('order-history export')
    expect(screen.queryByRole('region', { name: 'Preview' })).not.toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: 'Choose another file' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Drop a Performance CSV here')).toBeInTheDocument()
  })

  it('refuses a file that is not a .csv, in words', async () => {
    const { user } = await openImport()
    await choose(user, new File(['hello'], 'notes.txt', { type: 'text/plain' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('notes.txt is not a .csv file.')
  })

  it('accepts a dropped file, and uses only the first when several are dropped', async () => {
    await openImport()
    await waitFor(() => expect(screen.getByLabelText('Import into account')).toBeEnabled()) // the accounts have loaded
    const zone = screen.getByText('Drop a Performance CSV here').parentElement as HTMLElement
    fireEvent.drop(zone, {
      dataTransfer: { files: [csvFile(csv(manyRows(2)), 'first.csv'), csvFile(csv(manyRows(3)), 'second.csv')] },
    })
    expect(await screen.findByRole('region', { name: 'Preview' })).toBeInTheDocument()
    expect(screen.getByText('One file at a time: using first.csv.')).toBeInTheDocument()
    expect(tile('New fill pairs')).toHaveTextContent('2')
  })

  it('cancels back to the start without saving', async () => {
    const { user } = await openImport()
    await choose(user, csvFile(csv(manyRows(2))))
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Preview' })).not.toBeInTheDocument()
    expect(screen.getByText('Drop a Performance CSV here')).toBeInTheDocument()
    expect(await demoSource.imports()).toHaveLength(4)
  })

  it('says so, and keeps the preview, when the import itself is refused', async () => {
    vi.spyOn(demoSource, 'commitImport').mockRejectedValueOnce(new Error('The server said no.'))
    const { user } = await openImport()
    await choose(user, csvFile(csv(manyRows(2))))
    await user.click(await screen.findByRole('button', { name: 'Import 2 fill pairs' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The server said no.')
    expect(screen.getByRole('region', { name: 'Preview' })).toBeInTheDocument()
  })
})

describe('undoing an import', () => {
  it('shows what would be removed before anything is, and can be told to keep it', async () => {
    const { user } = await openImport()
    await user.click(await screen.findByRole('button', { name: 'Undo Performance_2026-09-30.csv' }))
    const dialog = await screen.findByRole('dialog', { name: 'Undo import' })
    expect(await within(dialog).findByText(/This removes 41 fill pairs and 14 trades\./)).toBeInTheDocument()
    expect(within(dialog).getByRole('alert')).toHaveTextContent('has notes, tags or screenshots')
    await user.click(within(dialog).getByRole('button', { name: 'Keep it' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect((await demoSource.imports())[0]?.undone_at).toBeNull()
  })

  it('undoes it on confirmation: it shows as undone, and the page says so', async () => {
    const { user } = await openImport()
    await user.click(await screen.findByRole('button', { name: 'Undo Performance_2026-09-30.csv' }))
    const dialog = await screen.findByRole('dialog', { name: 'Undo import' })
    await user.click(await within(dialog).findByRole('button', { name: 'Undo this import' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(
      screen.getByText('Undid Performance_2026-09-30.csv: its fill pairs and trades are removed.'),
    ).toBeInTheDocument()
    const history = screen.getByRole('region', { name: 'Import history' })
    await waitFor(() => expect(within(history).getAllByRole('row')[1]).toHaveTextContent('Undone'))
  })

  it('closes on Escape and gives focus back to the button that opened it', async () => {
    const { user } = await openImport()
    const button = await screen.findByRole('button', { name: 'Undo Performance_2026-09-30.csv' })
    await user.click(button)
    await screen.findByRole('dialog', { name: 'Undo import' })
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(button).toHaveFocus()
  })

  it('says so, and changes nothing, when the undo fails', async () => {
    vi.spyOn(demoSource, 'undoImport').mockRejectedValueOnce(new Error('nope'))
    const { user } = await openImport()
    await user.click(await screen.findByRole('button', { name: 'Undo Performance_2026-09-30.csv' }))
    const dialog = await screen.findByRole('dialog', { name: 'Undo import' })
    await user.click(await within(dialog).findByRole('button', { name: 'Undo this import' }))
    expect(await within(dialog).findByText('The import could not be undone. Nothing was changed.')).toBeInTheDocument()
    expect((await demoSource.imports())[0]?.undone_at).toBeNull()
  })
})

describe('the zone the file is read in', () => {
  it('is the CSV zone when one is set, and the history stays in the zone times are shown in', async () => {
    stubApi({ settings: { csv_timezone: 'America/Chicago' } })
    openApp('/import')
    await screen.findByRole('heading', { name: 'Import trades' })
    expect(await screen.findByText('America/Chicago')).toBeInTheDocument()
    expect(screen.queryByText('America/Los_Angeles')).not.toBeInTheDocument()
  })

  it('is the own zone when there is no separate CSV zone', async () => {
    stubApi()
    openApp('/import')
    await screen.findByRole('heading', { name: 'Import trades' })
    expect(await screen.findByText('America/Los_Angeles')).toBeInTheDocument()
  })
})
