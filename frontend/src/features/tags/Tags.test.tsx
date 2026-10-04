import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SAMPLE_TAGS, SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoJournal } from '../../lib/demo/journal'
import { demoSource } from '../../lib/demo/source'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
  document.documentElement.removeAttribute('data-theme')
})

async function open() {
  stubApi()
  const app = openApp('/settings/tags')
  await screen.findByRole('heading', { name: 'Tags' })
  await screen.findByRole('group', { name: 'Category Setup' })
  return { ...app, user: userEvent.setup() }
}

const category = (name: string) => screen.getByRole('group', { name: `Category ${name}` })
const carrying = (tagId: number) => SAMPLE_TRADES.filter((t) => t.tags.some((tag) => tag.id === tagId)).length
const dialog = (name: string) => screen.findByRole('dialog', { name })

describe('the tags screen', () => {
  it('lists each category with its tags and how many trades carry each', async () => {
    await open()
    const setup = category('Setup')
    expect(within(setup).getByText('4 tags')).toBeInTheDocument()
    const row = within(setup).getByText('A+ setup').closest('li') as HTMLElement
    const count = carrying(SAMPLE_TAGS.aPlus.id)
    expect(row).toHaveTextContent(`${count} ${count === 1 ? 'trade' : 'trades'}`)
    expect(within(category('Mistake')).getByText('FOMO')).toBeInTheDocument()
    expect(within(category('Emotion')).getByText('Calm')).toBeInTheDocument()
  })

  it('adds a category, and says so in words when the name is taken (in any capitalization) or blank', async () => {
    const { user } = await open()
    const add = screen.getByRole('button', { name: 'Add category' })
    expect(add).toBeDisabled() // nothing typed
    await user.type(screen.getByRole('textbox', { name: 'New category' }), 'Market regime')
    await user.click(add)
    expect(await screen.findByRole('group', { name: 'Category Market regime' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'New category' })).toHaveValue('') // cleared once it worked

    await user.type(screen.getByRole('textbox', { name: 'New category' }), 'setup')
    await user.click(screen.getByRole('button', { name: 'Add category' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('There is already a category called setup.')
  })

  it('adds a tag to a category with no trades on it yet', async () => {
    const { user } = await open()
    await user.type(screen.getByRole('textbox', { name: 'New tag in Setup' }), 'Gap fade')
    await user.click(within(category('Setup')).getByRole('button', { name: 'Add tag' }))
    const row = (await within(category('Setup')).findByText('Gap fade')).closest('li') as HTMLElement
    expect(row).toHaveTextContent('0 trades')
    expect(within(category('Setup')).getByText('5 tags')).toBeInTheDocument()
  })

  it('renames a category, and refuses a name another category has', async () => {
    const { user } = await open()
    await user.click(screen.getByRole('button', { name: 'Rename category Setup' }))
    const box = await screen.findByRole('textbox', { name: 'Category name' })
    await user.clear(box)
    await user.type(box, 'mistake')
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('There is already a category called mistake.')
    await user.clear(box)
    await user.type(box, 'Entries')
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    expect(await screen.findByRole('group', { name: 'Category Entries' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Category Setup' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument() // closed once it worked
  })

  it('deletes a category after saying what goes with it, and keeps it when asked to', async () => {
    const { user } = await open()
    await user.click(screen.getByRole('button', { name: 'Delete category Emotion' }))
    const ask = await dialog('Delete this category?')
    expect(ask).toHaveTextContent('Emotion and its 4 tags will be deleted')
    expect(ask).toHaveTextContent('The trades themselves stay.')
    await user.click(within(ask).getByRole('button', { name: 'Keep it' }))
    expect(screen.getByRole('group', { name: 'Category Emotion' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete category Emotion' }))
    await user.click(within(await dialog('Delete this category?')).getByRole('button', { name: 'Delete category' }))
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Category Emotion' })).not.toBeInTheDocument())
  })
})

describe('changing a tag', () => {
  it('renames, recolours and moves a tag, and the trade log shows it at once', async () => {
    const { user } = await open()
    await user.click(screen.getByRole('button', { name: 'Edit tag A+ setup' }))
    const edit = await dialog('Edit tag')
    const name = within(edit).getByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'Gap and go')
    await user.selectOptions(within(edit).getByRole('combobox', { name: 'Category' }), 'Mistake')
    await user.click(within(edit).getByRole('button', { name: 'Red' }))
    expect(within(edit).getByRole('textbox', { name: /colour code/ })).toHaveValue('#d03b3b')
    await user.click(within(edit).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(within(category('Mistake')).getByText('Gap and go')).toBeInTheDocument()
    expect(within(category('Setup')).queryByText('A+ setup')).not.toBeInTheDocument()
    const log = await demoSource.trades({ preset: 'all', sort: 'date', page: 1, per_page: 500 })
    const tagged = log.items.flatMap((row) => row.tags).filter((tag) => tag.id === SAMPLE_TAGS.aPlus.id)
    expect(tagged.length).toBeGreaterThan(0)
    expect(tagged.every((tag) => tag.name === 'Gap and go' && tag.color === '#d03b3b')).toBe(true)
  })

  it('refuses a name already used in the category and a colour code that is not one, in words', async () => {
    const { user } = await open()
    await user.click(screen.getByRole('button', { name: 'Edit tag Pullback' }))
    const edit = await dialog('Edit tag')
    const name = within(edit).getByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'reversal')
    await user.click(within(edit).getByRole('button', { name: 'Save' }))
    expect(await within(edit).findByRole('alert')).toHaveTextContent('There is already a tag called Reversal.')
    await user.clear(name)
    await user.type(name, 'Pullback')
    const code = within(edit).getByRole('textbox', { name: /colour code/ })
    await user.clear(code)
    await user.type(code, 'blue')
    await user.click(within(edit).getByRole('button', { name: 'Save' }))
    expect(await within(edit).findByRole('alert')).toHaveTextContent("Not a hex color like #2a78d6: 'blue'.")
    expect(screen.getByRole('dialog', { name: 'Edit tag' })).toBeInTheDocument() // still open, nothing changed
  })

  it('merges a tag into another: its trades move over, a trade with both keeps one, and the first tag is gone', async () => {
    const { user } = await open()
    const from = SAMPLE_TAGS.fomo
    const into = SAMPLE_TAGS.chased
    const union = new Set(
      SAMPLE_TRADES.filter((t) => t.tags.some((tag) => tag.id === from.id || tag.id === into.id)).map((t) => t.id),
    ).size
    await user.click(screen.getByRole('button', { name: `Merge tag ${from.name}` }))
    const ask = await dialog('Merge this tag?')
    expect(ask).toHaveTextContent(`Every trade with ${from.name}`)
    const target = within(ask).getByRole('combobox', { name: 'Merge into' })
    expect(within(target).queryByRole('option', { name: new RegExp(`· ${from.name}$`) })).not.toBeInTheDocument()
    await user.selectOptions(target, `Mistake · ${into.name}`)
    await user.click(within(ask).getByRole('button', { name: 'Merge' }))
    await waitFor(() => expect(within(category('Mistake')).queryByText(from.name)).not.toBeInTheDocument())
    const row = within(category('Mistake')).getByText(into.name).closest('li') as HTMLElement
    expect(row).toHaveTextContent(`${union} ${union === 1 ? 'trade' : 'trades'}`)
  })

  it('deletes a tag after saying how many trades lose it', async () => {
    const { user } = await open()
    const count = carrying(SAMPLE_TAGS.calm.id)
    await user.click(screen.getByRole('button', { name: 'Delete tag Calm' }))
    const ask = await dialog('Delete this tag?')
    expect(ask).toHaveTextContent(`Calm will be taken off ${count === 1 ? '1 trade' : `${count} trades`}.`)
    await user.click(within(ask).getByRole('button', { name: 'Delete tag' }))
    await waitFor(() => expect(within(category('Emotion')).queryByText('Calm')).not.toBeInTheDocument())
    const log = await demoSource.trades({ preset: 'all', sort: 'date', page: 1, per_page: 500 })
    expect(log.items.some((row) => row.tags.some((tag) => tag.id === SAMPLE_TAGS.calm.id))).toBe(false)
  })
})
