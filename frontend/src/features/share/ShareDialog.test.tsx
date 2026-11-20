import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoJournal } from '../../lib/demo/journal'
import { openApp, stubApi, type ApiOptions } from '../../test-utils'
import { cardToBlob, saveBlob } from './exportImage'

// jsdom has no canvas and no layout, so the image itself is not made here (its maths is tested in crop.test.ts and the
// picture was checked in a browser); what is tested is that Download hands the still card to the export.
vi.mock('./exportImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./exportImage')>()),
  cardToBlob: vi.fn(async () => new Blob(['png'], { type: 'image/png' })),
  saveBlob: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
  window.localStorage.clear()
})

type Trade = (typeof SAMPLE_TRADES)[number]
const first = (predicate: (t: Trade) => boolean): Trade => SAMPLE_TRADES.find(predicate) as Trade

const blank = first((t) => !t.has_notes && t.screenshot_count === 0 && t.tags.length === 0)
const journaled = first((t) => t.has_notes && t.tags.length > 0 && t.screenshot_count === 0)
const pictured = first((t) => t.screenshot_count >= 2) // the sample data's screenshots have no picture

const png = (name = 'chart.png', type = 'image/png', size = 100) => {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

async function openTrade(trade: Trade, api: ApiOptions = {}) {
  stubApi(api)
  openApp(`/trades/${trade.id}`)
  await screen.findByRole('region', { name: 'Notes' })
  return userEvent.setup({ applyAccept: false })
}

async function openShare(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Share' }))
  return screen.findByRole('dialog', { name: 'Share trade' })
}

const currentStep = (dialog: HTMLElement) =>
  within(dialog).getByRole('list', { name: 'Steps' }).querySelector('[aria-current="step"]')?.textContent

const cardIn = (dialog: HTMLElement) => within(dialog).getByRole('group', { name: /^Share card/ })

/** Adds images to the trade from the trade page, as a person would before sharing. */
async function addOnTradePage(user: ReturnType<typeof userEvent.setup>, ...names: string[]) {
  const card = screen.getByRole('region', { name: 'Screenshots' })
  await user.upload(
    within(card).getByLabelText('Choose images to add'),
    names.map((name) => png(name)),
  )
  await within(card).findByRole('button', { name: `View ${names[names.length - 1]}` })
}

describe('the Share button', () => {
  it('sits in the title row, right after the contract, side, date and account', async () => {
    await openTrade(journaled)
    const share = screen.getByRole('button', { name: 'Share' })
    expect(share).toHaveAttribute('aria-haspopup', 'dialog')
    expect(share.previousElementSibling).toBe(screen.getByRole('heading', { level: 1 }))
  })

  it('opens "Share trade" at step 1', async () => {
    const user = await openTrade(blank)
    const dialog = await openShare(user)
    expect(currentStep(dialog)).toBe('1Screenshot')
  })

  it('does not open over the "Pick a username" box', async () => {
    const user = await openTrade(journaled, { askUsername: true })
    expect(await screen.findByRole('dialog', { name: 'Pick a username' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Share' }))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByRole('dialog', { name: 'Share trade' })).toBeNull()
  })

  it('opens once the "Pick a username" box is put away with Escape', async () => {
    const user = await openTrade(journaled, { askUsername: true })
    expect(await screen.findByRole('dialog', { name: 'Pick a username' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Pick a username' })).toBeNull()
    expect(await openShare(user)).toBeInTheDocument()
  })
})

describe('step 1: the screenshot', () => {
  it('asks which screenshot to include when the trade has some, the first one picked', async () => {
    const user = await openTrade(blank)
    await addOnTradePage(user, 'entry.png', 'exit.png')
    const dialog = await openShare(user)
    expect(within(dialog).getByRole('heading', { name: 'Include a screenshot?' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Use entry.png' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(dialog).getByRole('button', { name: 'Use exit.png' })).toHaveAttribute('aria-pressed', 'false')
    await user.click(within(dialog).getByRole('button', { name: 'Use exit.png' }))
    expect(within(dialog).getByRole('button', { name: 'Use exit.png' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('"Share without a screenshot" goes to the card with no picture on it', async () => {
    const user = await openTrade(blank)
    await addOnTradePage(user, 'entry.png')
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    expect(currentStep(dialog)).toBe('3Card')
    expect(within(cardIn(dialog)).queryByAltText('Chart screenshot of the trade')).toBeNull()
    expect(cardIn(dialog)).toHaveClass('sc-no-shot')
    expect(within(dialog).getByRole('checkbox', { name: 'Screenshot' })).not.toBeChecked()
  })

  it('offers an upload when the trade has none, saves it to the trade and goes on to the crop', async () => {
    const user = await openTrade(blank)
    const dialog = await openShare(user)
    expect(within(dialog).getByRole('heading', { name: 'Add a screenshot?' })).toBeInTheDocument()
    expect(within(dialog).getByText('PNG, JPEG, GIF or WebP, up to 10 MB')).toBeInTheDocument()
    await user.upload(within(dialog).getByLabelText('Choose an image to add'), png('chart.png'))
    expect(await within(dialog).findByRole('heading', { name: 'Crop your screenshot' })).toBeInTheDocument()
    expect(currentStep(dialog)).toBe('2Crop')

    await user.click(within(dialog).getByRole('button', { name: 'Use this crop' }))
    expect(currentStep(dialog)).toBe('3Card')
    expect(within(dialog).getByRole('checkbox', { name: 'Screenshot' })).toBeChecked()
    // (kept invisible until it has loaded and can be placed, which it never does in jsdom: so found by its text)
    expect(within(cardIn(dialog)).getByAltText('Chart screenshot of the trade')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.getByRole('region', { name: 'Journal status' })).toHaveTextContent('Screenshots1 image')
  })

  it('takes a pasted image itself, so the trade page behind it does not add it a second time', async () => {
    const user = await openTrade(blank)
    const dialog = await openShare(user)
    fireEvent.paste(document.body, { clipboardData: { files: [png('pasted.png')] } })
    expect(await within(dialog).findByRole('heading', { name: 'Crop your screenshot' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.getByRole('region', { name: 'Journal status' })).toHaveTextContent('Screenshots1 image')
  })

  it('shows a refusal in words, as the trade page does', async () => {
    const user = await openTrade(blank)
    const dialog = await openShare(user)
    const input = within(dialog).getByLabelText('Choose an image to add')
    await user.upload(input, new File(['x'], 'notes.pdf', { type: 'application/pdf' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Only images can be added here.')
    await user.upload(input, png('huge.png', 'image/png', 11 * 1024 * 1024))
    await waitFor(() => expect(within(dialog).getByRole('alert')).toHaveTextContent('huge.png is larger than 10 MB.'))
    expect(currentStep(dialog)).toBe('1Screenshot')
  })

  it('skips steps 1 and 2 when no screenshot has a picture to show (the sample data)', async () => {
    const user = await openTrade(pictured)
    const dialog = await openShare(user)
    expect(currentStep(dialog)).toBe('3Card')
    const option = within(dialog).getByRole('checkbox', { name: /Screenshot/ })
    expect(option).toBeDisabled()
    expect(option.closest('label')).toHaveTextContent('No preview in the sample data')
  })
})

describe('step 2: the crop', () => {
  it('zooms with the slider and with plus and minus on the frame, and Reset starts again', async () => {
    const user = await openTrade(blank)
    await addOnTradePage(user, 'entry.png')
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Crop screenshot' }))
    const frame = within(dialog).getByRole('group', { name: /^Screenshot crop/ })
    const zoom = within(dialog).getByRole('slider', { name: 'Zoom' })
    expect(frame).toHaveFocus() // the new step takes focus, so the arrow keys work at once
    expect(zoom).toHaveValue('1')
    await user.keyboard('++')
    expect(zoom).toHaveValue('1.2')
    await user.keyboard('-')
    expect(zoom).toHaveValue('1.1')
    fireEvent.change(zoom, { target: { value: '9' } })
    expect(zoom).toHaveValue('4')
    await user.click(within(dialog).getByRole('button', { name: 'Reset' }))
    expect(zoom).toHaveValue('1')
  })

  it('keeps the crop while the pop-up is open, and starts a new one for another picture', async () => {
    const user = await openTrade(blank)
    await addOnTradePage(user, 'entry.png', 'exit.png')
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Crop screenshot' }))
    await user.keyboard('+')
    await user.click(within(dialog).getByRole('button', { name: 'Use this crop' }))
    await user.click(within(dialog).getByRole('button', { name: 'Crop' })) // from the card, back to the crop
    expect(within(dialog).getByRole('slider', { name: 'Zoom' })).toHaveValue('1.1')

    await user.click(within(dialog).getByRole('button', { name: '← Back' }))
    await user.click(within(dialog).getByRole('button', { name: 'Use entry.png' })) // the same one: the crop stays
    await user.click(within(dialog).getByRole('button', { name: 'Crop screenshot' }))
    expect(within(dialog).getByRole('slider', { name: 'Zoom' })).toHaveValue('1.1')

    await user.click(within(dialog).getByRole('button', { name: '← Back' }))
    await user.click(within(dialog).getByRole('button', { name: 'Use exit.png' }))
    await user.click(within(dialog).getByRole('button', { name: 'Crop screenshot' }))
    expect(within(dialog).getByRole('slider', { name: 'Zoom' })).toHaveValue('1')
  })
})

describe('step 3: the card', () => {
  it('shows the trade and its options, Gold foil first, with Notes, Tags and Account name on', async () => {
    const user = await openTrade(journaled)
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    const card = cardIn(dialog)
    expect(card).toHaveAccessibleName('Share card, gold foil style')
    expect(card).toHaveTextContent(journaled.symbol)
    expect(card).toHaveTextContent('Certificate of Trade')
    expect(card).toHaveTextContent(journaled.account.name)
    expect(card).toHaveTextContent(journaled.tags[0]?.name ?? '')
    expect(card).toHaveTextContent('roundturn.app')
    expect(within(dialog).getByRole('button', { name: 'Gold foil' })).toHaveAttribute('aria-pressed', 'true')
    for (const name of [/^Notes/, /^Tags/, 'Account name']) {
      expect(within(dialog).getByRole('checkbox', { name })).toBeChecked()
    }
    expect(within(dialog).getByText(/1080 × 1350 image, made on your device/)).toBeInTheDocument()
  })

  it('changes the card at once when a style or an option is switched', async () => {
    const user = await openTrade(journaled)
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))

    await user.click(within(dialog).getByRole('button', { name: 'Holographic' }))
    expect(cardIn(dialog)).toHaveAccessibleName('Share card, holographic style')
    expect(cardIn(dialog)).toHaveTextContent('Journaled trade')
    await user.click(within(dialog).getByRole('button', { name: 'Paper' }))
    expect(cardIn(dialog)).toHaveAccessibleName('Share card, engraved paper style')

    await user.click(within(dialog).getByRole('checkbox', { name: 'Account name' }))
    expect(cardIn(dialog)).not.toHaveTextContent(journaled.account.name)
    await user.click(within(dialog).getByRole('checkbox', { name: /^Tags/ }))
    expect(cardIn(dialog)).not.toHaveTextContent(journaled.tags[0]?.name ?? '')

    expect(cardIn(dialog)).toHaveTextContent('$')
    await user.click(within(dialog).getByRole('button', { name: 'Points only' }))
    expect(cardIn(dialog)).not.toHaveTextContent('$')
    expect(cardIn(dialog)).toHaveTextContent(/Result.+pts/)
  })

  it('remembers the last style in this browser', async () => {
    const user = await openTrade(journaled)
    let dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    await user.click(within(dialog).getByRole('button', { name: 'Paper' }))
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    expect(within(dialog).getByRole('button', { name: 'Paper' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('disables an option with nothing to show, saying why', async () => {
    const user = await openTrade(blank)
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    expect(within(dialog).getByRole('checkbox', { name: /^Notes/ })).toBeDisabled()
    expect(within(dialog).getByText('This trade has no notes.')).toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: /^Tags/ })).toBeDisabled()
    // No screenshot at all: a way back to add one.
    expect(within(dialog).getByText('No screenshot')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Add a screenshot' }))
    expect(within(dialog).getByRole('heading', { name: 'Add a screenshot?' })).toBeInTheDocument()
  })

  it('Download image makes the image from the still card and saves it under the trade’s name', async () => {
    const user = await openTrade(journaled)
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    expect(within(dialog).getByRole('button', { name: 'Download image' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Download image' }))
    await waitFor(() => expect(within(dialog).getByText('Image saved.')).toBeInTheDocument())
    expect(cardToBlob).toHaveBeenCalledTimes(1)
    const still = vi.mocked(cardToBlob).mock.calls[0]?.[0]
    expect(still).toHaveClass('sc-card', 'sc-still')
    expect(still).toHaveTextContent(journaled.symbol)
    const [blob, name] = vi.mocked(saveBlob).mock.calls[0] ?? []
    expect(blob).toBeInstanceOf(Blob)
    expect(name).toMatch(new RegExp(`^roundturn-${journaled.symbol}-${journaled.trade_day}-no-\\d{5,}\\.png$`))
  })

  it('says so when the image cannot be made', async () => {
    vi.mocked(cardToBlob).mockRejectedValueOnce(new Error('no canvas'))
    const user = await openTrade(journaled)
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    await user.click(within(dialog).getByRole('button', { name: 'Download image' }))
    expect(await within(dialog).findByText(/The image could not be made/)).toBeInTheDocument()
    expect(saveBlob).not.toHaveBeenCalled()
  })

  it('offers Copy image only where the browser can put a picture on the clipboard', async () => {
    const user = await openTrade(journaled)
    let dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    expect(within(dialog).queryByRole('button', { name: 'Copy image' })).toBeNull() // jsdom has no ClipboardItem
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    const write = vi.fn(async () => undefined)
    vi.stubGlobal(
      'ClipboardItem',
      class {
        constructor(readonly items: Record<string, Promise<Blob>>) {}
      },
    )
    Object.defineProperty(navigator, 'clipboard', { value: { write }, configurable: true })
    dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    await user.click(within(dialog).getByRole('button', { name: 'Copy image' }))
    await waitFor(() => expect(within(dialog).getByText('Image copied.')).toBeInTheDocument())
    expect(write).toHaveBeenCalledTimes(1)
    Reflect.deleteProperty(navigator, 'clipboard')
  })
})

describe('the signature', () => {
  it('signs the card with the username, and can be switched off', async () => {
    const user = await openTrade(journaled, { username: 'Trader_Joe' })
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    const sign = within(dialog).getByRole('checkbox', { name: 'Sign as Trader_Joe' })
    expect(sign).toBeChecked()
    expect(cardIn(dialog)).toHaveTextContent('Trader_JoeTrader')
    await user.click(sign)
    expect(cardIn(dialog)).not.toHaveTextContent('Trader_Joe')
  })

  it('leaves the card unsigned without a username, and says where to pick one', async () => {
    const user = await openTrade(journaled)
    const dialog = await openShare(user)
    await user.click(within(dialog).getByRole('button', { name: 'Share without a screenshot' }))
    const sign = within(dialog).getByRole('checkbox', { name: /Sign your card/ })
    expect(sign).toBeDisabled()
    expect(within(dialog).getByText('Pick a username in Settings, Preferences to sign your card.')).toBeInTheDocument()
    expect(cardIn(dialog)).not.toHaveTextContent('Trader')
  })
})
