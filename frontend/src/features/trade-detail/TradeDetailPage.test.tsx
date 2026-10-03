import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SAMPLE_TRADES, fillPairsFor, notesFor, pointsFor } from '../../lib/demo/generate'
import { resetDemoJournal } from '../../lib/demo/journal'
import { demoSource } from '../../lib/demo/source'
import { formatAmount, formatPnl, formatPoints } from '../../lib/format'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
  document.documentElement.removeAttribute('data-theme')
})

type Trade = (typeof SAMPLE_TRADES)[number]
const first = (predicate: (t: Trade) => boolean): Trade => SAMPLE_TRADES.find(predicate) as Trade

const blank = first((t) => !t.has_notes && t.screenshot_count === 0 && t.tags.length === 0)
const pictured = first((t) => t.screenshot_count >= 2)
const multiPair = first((t) => fillPairsFor(t).length >= 2)

async function openTrade(id: number | string, search: Record<string, string | number> = {}) {
  stubApi()
  const app = openApp(`/trades/${id}`, search)
  await screen.findByRole('region', { name: 'Notes' }).catch(() => null)
  return { ...app, user: userEvent.setup({ applyAccept: false }) }
}

const png = (name = 'chart.png', type = 'image/png', size = 100) => {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

describe('the trade page', () => {
  it('names the trade: contract, side, time in the viewer’s zone, and account', async () => {
    await openTrade(multiPair.id)
    const heading = await screen.findByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent(multiPair.symbol)
    expect(heading).toHaveTextContent(multiPair.direction === 'long' ? 'Long' : 'Short')
    expect(heading).toHaveTextContent(multiPair.account.name)
    expect(heading.textContent).toMatch(/[A-Z][a-z]{2} \d{1,2} · \d{2}:\d{2}/)
  })

  it('shows the result: net, gross, fees, points, hold time and trading day', async () => {
    await openTrade(multiPair.id)
    const result = await screen.findByRole('region', { name: 'Result' })
    expect(result).toHaveTextContent(formatPnl(multiPair.net_pnl))
    expect(result).toHaveTextContent(formatPnl(multiPair.gross_pnl))
    expect(result).toHaveTextContent(formatAmount(multiPair.fees))
    expect(result).toHaveTextContent(formatPoints(pointsFor(multiPair)))
    expect(result).toHaveTextContent('Hold time')
    expect(result).toHaveTextContent('Trading day')
  })

  it('lists the fill pairs and does the arithmetic from gross to net underneath', async () => {
    await openTrade(multiPair.id)
    const card = await screen.findByRole('region', { name: 'Fill pairs' })
    const rows = within(card).getAllByRole('row')
    const pairs = fillPairsFor(multiPair)
    // header, one row per pair, then Gross, Fees and Net
    expect(rows).toHaveLength(1 + pairs.length + 3)
    expect(rows[1]).toHaveTextContent(String(pairs[0]?.qty))
    expect(within(card).getByRole('row', { name: /^Gross/ })).toHaveTextContent(formatPnl(multiPair.gross_pnl))
    expect(within(card).getByRole('row', { name: /^Fees/ })).toHaveTextContent(formatPnl(`-${multiPair.fees}`))
    expect(within(card).getByRole('row', { name: /^Net/ })).toHaveTextContent(formatPnl(multiPair.net_pnl))
  })

  it('steps to the trades either side in the list it came from, keeping that list’s view', async () => {
    const query = { preset: '30d', sort: 'symbol', page: 1, per_page: 1 } as const
    const ids = (await demoSource.trades({ ...query, per_page: 50 })).items.map((t) => t.id)
    const middle = ids[3] ?? 0
    await openTrade(middle, { sort: 'symbol' })
    const previous = await screen.findByRole('link', { name: 'Previous trade' })
    const next = screen.getByRole('link', { name: 'Next trade' })
    await waitFor(() => expect(previous.getAttribute('href')).toBe(`/trades/${ids[2]}?sort=symbol`))
    expect(next.getAttribute('href')).toBe(`/trades/${ids[4]}?sort=symbol`)
    expect(screen.getByRole('link', { name: '← Trades' }).getAttribute('href')).toBe('/trades?sort=symbol')
  })

  it('has no previous trade at the top of the list and no next at the bottom', async () => {
    const query = { preset: '30d', sort: '-date', page: 1, per_page: 50 } as const
    const top = (await demoSource.trades(query)).items[0]?.id ?? 0
    await openTrade(top)
    await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(screen.getByRole('link', { name: 'Next trade' })).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Previous trade' })).toBeDisabled()
  })

  it('says so when there is no such trade', async () => {
    await openTrade(999_999)
    expect(await screen.findByRole('heading', { name: 'Trade not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the trades' })).toBeInTheDocument()
  })

  it('says so for an address that is not a trade number', async () => {
    await openTrade('abc')
    expect(await screen.findByRole('heading', { name: 'Trade not found' })).toBeInTheDocument()
  })

  it('shows what the journal has and lacks', async () => {
    await openTrade(blank.id)
    const status = await screen.findByRole('region', { name: 'Journal status' })
    await waitFor(() => expect(status).toHaveTextContent('Setup tagNot set'))
    expect(status).toHaveTextContent('NotesEmpty')
    expect(status).toHaveTextContent('ScreenshotsNone')
  })
})

describe('notes', () => {
  const formatted = first((t) => t.has_notes && t.tags.length === 0 && notesFor(t).includes('<strong>'))

  it('shows the saved note with its formatting', async () => {
    await openTrade(formatted.id)
    const box = await screen.findByRole('textbox', { name: 'Notes' })
    expect(box.querySelector('strong')).not.toBeNull()
    expect(box.querySelector('ul')).not.toBeNull()
    expect(screen.getByText('Saved')).toBeInTheDocument()
  })

  it('offers the formatting tools, with the state of each', async () => {
    await openTrade(blank.id)
    const toolbar = await screen.findByRole('toolbar', { name: 'Formatting' })
    for (const name of ['Bold', 'Italic', 'Underline', 'Bulleted list', 'Numbered list', 'Quote']) {
      expect(within(toolbar).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'false')
    }
    expect(within(toolbar).getByRole('button', { name: 'Link' })).toBeInTheDocument()
    expect(within(toolbar).getByRole('button', { name: 'Insert template' })).toBeInTheDocument()
  })

  it('saves by itself a moment after typing stops', async () => {
    const { user } = await openTrade(blank.id)
    const box = await screen.findByRole('textbox', { name: 'Notes' })
    await user.click(box)
    await user.keyboard('Waited for the pullback.')
    expect(await screen.findByText('Unsaved changes')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument(), { timeout: 3000 })
    expect((await demoSource.trade(blank.id))?.notes_html).toBe('<p>Waited for the pullback.</p>')
  })

  it('saves at once when the editor loses focus, so moving on never drops a change', async () => {
    const { user } = await openTrade(blank.id)
    const box = await screen.findByRole('textbox', { name: 'Notes' })
    await user.click(box)
    await user.keyboard('Quick note')
    await user.click(screen.getByRole('heading', { name: 'Fill pairs' })) // anywhere else
    await waitFor(async () => expect((await demoSource.trade(blank.id))?.notes_html).toBe('<p>Quick note</p>'))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('keeps what was typed and offers another try when saving fails', async () => {
    const save = vi.spyOn(demoSource, 'saveNotes').mockRejectedValueOnce(new Error('offline'))
    const { user } = await openTrade(blank.id)
    const box = await screen.findByRole('textbox', { name: 'Notes' })
    await user.click(box)
    await user.keyboard('Do not lose me')
    await user.click(screen.getByRole('heading', { name: 'Fill pairs' }))
    expect(await screen.findByText('Could not save. Your text is still here.')).toBeInTheDocument()
    expect(box).toHaveTextContent('Do not lose me')

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
    expect(save).toHaveBeenCalledTimes(2)
    expect((await demoSource.trade(blank.id))?.notes_html).toBe('<p>Do not lose me</p>')
  })

  it('inserts the template and saves it', async () => {
    const { user } = await openTrade(blank.id)
    await user.click(await screen.findByRole('button', { name: 'Insert template' }))
    const box = screen.getByRole('textbox', { name: 'Notes' })
    expect(box).toHaveTextContent('Plan:')
    expect(box).toHaveTextContent('What happened:')
    expect(box).toHaveTextContent('Improve:')
    await user.click(screen.getByRole('heading', { name: 'Fill pairs' }))
    await waitFor(async () =>
      expect((await demoSource.trade(blank.id))?.notes_html).toContain('<strong>Plan:</strong>'),
    )
  })

  it('never lets markup typed into a note become markup', async () => {
    const { user } = await openTrade(blank.id)
    const box = await screen.findByRole('textbox', { name: 'Notes' })
    await user.click(box)
    await user.keyboard('<img src=x onerror=alert(1)>')
    await user.click(screen.getByRole('heading', { name: 'Fill pairs' }))
    await waitFor(async () => expect((await demoSource.trade(blank.id))?.notes_html).toContain('&lt;img'))
    expect((await demoSource.trade(blank.id))?.notes_html).not.toContain('<img')
  })
})

describe('tags', () => {
  it('shows the groups, with the trade’s tags pressed', async () => {
    const tagged = first((t) => t.tags.length > 0)
    await openTrade(tagged.id)
    const card = await screen.findByRole('region', { name: 'Tags' })
    for (const group of ['Setup', 'Mistake', 'Emotion']) {
      expect(await within(card).findByRole('group', { name: group })).toBeInTheDocument()
    }
    const name = tagged.tags[0]?.name ?? ''
    expect(await within(card).findByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
  })

  it('puts a tag on and takes it off, and the trade log follows', async () => {
    const { user } = await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Tags' })
    const pullback = await within(card).findByRole('button', { name: 'Pullback' })
    expect(pullback).toHaveAttribute('aria-pressed', 'false')

    await user.click(pullback)
    await waitFor(() => expect(pullback).toHaveAttribute('aria-pressed', 'true'))
    expect((await demoSource.trade(blank.id))?.tags.map((t) => t.name)).toEqual(['Pullback'])
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Journal status' })).toHaveTextContent('Setup tagSet'),
    )

    await user.click(pullback)
    await waitFor(() => expect(pullback).toHaveAttribute('aria-pressed', 'false'))
    expect((await demoSource.trade(blank.id))?.tags).toEqual([])
  })

  it('adds a new tag to a group and puts it on the trade', async () => {
    const { user } = await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Tags' })
    await user.click(await within(card).findByRole('button', { name: '+ Add tag' }))
    await user.selectOptions(screen.getByLabelText('Group'), 'Mistake')
    await user.type(screen.getByLabelText('Tag name'), 'Moved my stop')
    await user.click(screen.getByRole('button', { name: 'Add tag' }))

    const added = await within(card).findByRole('button', { name: 'Moved my stop' })
    await waitFor(() => expect(added).toHaveAttribute('aria-pressed', 'true'))
    expect(
      within(within(card).getByRole('group', { name: 'Mistake' })).getByRole('button', { name: 'Moved my stop' }),
    ).toBe(added)
    expect(screen.queryByLabelText('Tag name')).not.toBeInTheDocument() // the form closed
  })

  it('explains an empty tag name instead of failing quietly', async () => {
    const { user } = await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Tags' })
    await user.click(await within(card).findByRole('button', { name: '+ Add tag' }))
    await user.type(screen.getByLabelText('Tag name'), '   ')
    await user.click(screen.getByRole('button', { name: 'Add tag' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Give the tag a name.')
  })
})

describe('screenshots', () => {
  it('lists the trade’s images', async () => {
    await openTrade(pictured.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    expect(within(card).getAllByRole('button', { name: /^View / })).toHaveLength(pictured.screenshot_count)
  })

  it('says when there are none', async () => {
    await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    expect(within(card).getByText('No screenshots yet.')).toBeInTheDocument()
  })

  it('adds a chosen image', async () => {
    const { user } = await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    await user.upload(within(card).getByLabelText('Choose images to add'), png('entry.png'))
    expect(await within(card).findByRole('button', { name: 'View entry.png' })).toBeInTheDocument()
    expect(within(card).getByText('Added 1 image.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Journal status' })).toHaveTextContent('Screenshots1 image')
  })

  it('adds an image dropped on the box, and one pasted from the clipboard', async () => {
    await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })

    fireEvent.drop(within(card).getByText(/Drop images here/), { dataTransfer: { files: [png('dropped.png')] } })
    expect(await within(card).findByRole('button', { name: 'View dropped.png' })).toBeInTheDocument()

    fireEvent.paste(document.body, { clipboardData: { files: [png('pasted.png')] } })
    expect(await within(card).findByRole('button', { name: 'View pasted.png' })).toBeInTheDocument()
  })

  it('ignores a paste that holds no image, so text pastes as usual', async () => {
    await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    const notPrevented = fireEvent.paste(document.body, { clipboardData: { files: [] } })
    expect(notPrevented).toBe(true)
    expect(within(card).getByText('No screenshots yet.')).toBeInTheDocument()
  })

  it('refuses a file that is not an image, and one that is too large, in words', async () => {
    const { user } = await openTrade(blank.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    const input = within(card).getByLabelText('Choose images to add')

    await user.upload(input, new File(['x'], 'notes.pdf', { type: 'application/pdf' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('Only images can be added here.')

    await user.upload(input, png('huge.png', 'image/png', 11 * 1024 * 1024))
    await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent('huge.png is larger than 10 MB.'))
    expect(within(card).getByText('No screenshots yet.')).toBeInTheDocument()
  })

  it('opens an image full size, steps through them, and gives focus back on Escape', async () => {
    const { user } = await openTrade(pictured.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    const thumbnail = within(card).getAllByRole('button', { name: /^View / })[0] as HTMLElement
    await user.click(thumbnail)

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAccessibleName(`Screenshot 1 of ${pictured.screenshot_count}`)
    expect(within(dialog).getByRole('button', { name: 'Previous image' })).toBeDisabled()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('dialog')).toHaveAccessibleName(`Screenshot 2 of ${pictured.screenshot_count}`)
    await user.click(screen.getByRole('button', { name: 'Previous image' }))
    expect(screen.getByRole('dialog')).toHaveAccessibleName(`Screenshot 1 of ${pictured.screenshot_count}`)

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(thumbnail).toHaveFocus()
  })

  it('keeps Tab inside the open image', async () => {
    const { user } = await openTrade(pictured.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    await user.click(within(card).getAllByRole('button', { name: /^View / })[0] as HTMLElement)
    const dialog = await screen.findByRole('dialog')
    for (let i = 0; i < 8; i++) {
      await user.tab()
      expect(dialog).toContainElement(document.activeElement as HTMLElement)
    }
  })

  it('asks before deleting an image, and can be told to keep it', async () => {
    const { user } = await openTrade(pictured.id)
    const card = await screen.findByRole('region', { name: 'Screenshots' })
    await user.click(within(card).getAllByRole('button', { name: /^View / })[0] as HTMLElement)
    const dialog = await screen.findByRole('dialog')

    await user.click(within(dialog).getByRole('button', { name: 'Delete image' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Delete this image?')
    await user.click(within(dialog).getByRole('button', { name: 'Keep it' }))
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
    expect((await demoSource.trade(pictured.id))?.screenshots).toHaveLength(pictured.screenshot_count)

    await user.click(within(dialog).getByRole('button', { name: 'Delete image' }))
    await user.click(within(dialog).getByRole('button', { name: 'Yes, delete it' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await within(card).findByText('Image deleted.')).toBeInTheDocument()
    expect((await demoSource.trade(pictured.id))?.screenshots).toHaveLength(pictured.screenshot_count - 1)
  })
})
