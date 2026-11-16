import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usernameProblem } from '../../lib/account'
import { openApp, stubApi, type AppPath } from '../../test-utils'

afterEach(() => {
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('data-theme')
})

function open(path: AppPath, opts: Parameters<typeof stubApi>[0] = {}) {
  const saved = stubApi(opts)
  const app = openApp(path)
  return { ...app, saved, user: userEvent.setup() }
}

const TAKEN = {
  status: 409,
  body: { title: 'Conflict', status: 409, detail: 'That username is taken. Try another.', errors: null },
}

describe('the rules a username follows', () => {
  it('takes 3 to 20 letters, numbers or underscores, and ignores spaces around it', () => {
    expect(usernameProblem('abc')).toBeNull()
    expect(usernameProblem('Trader_Joe_99')).toBeNull()
    expect(usernameProblem('a'.repeat(20))).toBeNull()
    expect(usernameProblem('  joe  ')).toBeNull()
    expect(usernameProblem('ab')).toBe('Use at least 3 characters.')
    expect(usernameProblem('   ')).toBe('Use at least 3 characters.')
    expect(usernameProblem('a'.repeat(21))).toBe('Use at most 20 characters.')
    expect(usernameProblem('joe smith')).toBe('Use only letters, numbers and underscores.')
    expect(usernameProblem('joe-smith')).toBe('Use only letters, numbers and underscores.')
    expect(usernameProblem('jöe')).toBe('Use only letters, numbers and underscores.')
  })
})

describe('the prompt after sign-in', () => {
  it('asks someone with no username, and saving the name closes it', async () => {
    const { user, saved } = open('/', { askUsername: true })
    const dialog = await screen.findByRole('dialog', { name: 'Pick a username' })
    const box = within(dialog).getByLabelText('Username')
    expect(box).toHaveFocus()
    expect(box).toHaveAttribute('maxlength', '20')
    await user.type(box, ' Trader_Joe ')
    await user.click(within(dialog).getByRole('button', { name: 'Save username' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(saved).toEqual([{ username: 'Trader_Joe' }])

    const menu = screen.getByRole('button', { name: 'Account menu' })
    expect(menu).toHaveTextContent('TR') // the initials come from the username now
    await user.click(menu)
    expect(screen.getByTestId('username')).toHaveTextContent('Trader_Joe')
    expect(screen.getByTestId('who')).toHaveTextContent('alice@example.test')
  })

  it('is not shown to someone who has a username or has said not now', async () => {
    open('/', { askUsername: false })
    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.queryByRole('dialog', { name: 'Pick a username' })).not.toBeInTheDocument()
  })

  it('says what is wrong with a name before sending it', async () => {
    const { user, saved } = open('/', { askUsername: true })
    const dialog = await screen.findByRole('dialog', { name: 'Pick a username' })
    await user.type(within(dialog).getByLabelText('Username'), 'jo')
    await user.click(within(dialog).getByRole('button', { name: 'Save username' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Use at least 3 characters.')
    await user.type(within(dialog).getByLabelText('Username'), ' x')
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Use only letters, numbers and underscores.')
    expect(saved).toEqual([])
  })

  it('says so when someone else has the name, and stays open', async () => {
    const { user } = open('/', { askUsername: true, usernameProblem: TAKEN })
    const dialog = await screen.findByRole('dialog', { name: 'Pick a username' })
    await user.type(within(dialog).getByLabelText('Username'), 'taken_name')
    await user.click(within(dialog).getByRole('button', { name: 'Save username' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('That username is taken. Try another.')
    expect(screen.getByRole('dialog', { name: 'Pick a username' })).toBeInTheDocument()
  })

  it('remembers "not now" and closes', async () => {
    const { user, saved } = open('/', { askUsername: true })
    const dialog = await screen.findByRole('dialog', { name: 'Pick a username' })
    await user.click(within(dialog).getByRole('button', { name: 'Not now' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(saved).toEqual([{ skip: true }])
  })

  it('closes with Escape without answering, so it is asked again next time', async () => {
    const { user, saved } = open('/', { askUsername: true })
    await screen.findByRole('dialog', { name: 'Pick a username' })
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(saved).toEqual([])
  })

  it('is asked on the first-run setup too', async () => {
    open('/welcome', { askUsername: true })
    expect(await screen.findByRole('dialog', { name: 'Pick a username' })).toBeInTheDocument()
  })
})

describe('the username in settings', () => {
  it('lets someone without one pick one', async () => {
    const { user, saved } = open('/settings/preferences')
    const card = await screen.findByRole('region', { name: 'Username' })
    expect(within(card).getByText(/You have not picked one yet/)).toBeInTheDocument()
    const save = within(card).getByRole('button', { name: 'Save username' })
    expect(save).toBeDisabled() // nothing typed yet
    await user.type(within(card).getByLabelText('Your username'), 'first_pick')
    await user.click(save)
    expect(await within(card).findByRole('status')).toHaveTextContent('Saved')
    expect(saved).toEqual([{ username: 'first_pick' }])
  })

  it('shows the saved name and lets it be changed', async () => {
    const { user, saved } = open('/settings/preferences', { username: 'old_name' })
    const card = await screen.findByRole('region', { name: 'Username' })
    const box = within(card).getByLabelText('Your username')
    expect(box).toHaveValue('old_name')
    expect(within(card).getByRole('button', { name: 'Save username' })).toBeDisabled()
    await user.clear(box)
    await user.type(box, 'New_Name')
    await user.click(within(card).getByRole('button', { name: 'Save username' }))
    expect(await within(card).findByRole('status')).toHaveTextContent('Saved')
    expect(saved).toEqual([{ username: 'New_Name' }])
    expect(within(card).getByRole('button', { name: 'Save username' })).toBeDisabled() // saved: nothing to save
  })

  it('refuses a name of the wrong shape without sending it', async () => {
    const { user, saved } = open('/settings/preferences', { username: 'old_name' })
    const card = await screen.findByRole('region', { name: 'Username' })
    const box = within(card).getByLabelText('Your username')
    await user.clear(box)
    await user.type(box, 'has space')
    await user.click(within(card).getByRole('button', { name: 'Save username' }))
    expect(within(card).getByRole('alert')).toHaveTextContent('Use only letters, numbers and underscores.')
    expect(saved).toEqual([])
  })

  it('says so when someone else has the name', async () => {
    const { user } = open('/settings/preferences', { usernameProblem: TAKEN })
    const card = await screen.findByRole('region', { name: 'Username' })
    await user.type(within(card).getByLabelText('Your username'), 'taken_name')
    await user.click(within(card).getByRole('button', { name: 'Save username' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('That username is taken. Try another.')
  })
})
