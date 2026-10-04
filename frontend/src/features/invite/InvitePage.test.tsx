import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp } from '../../test-utils'
import { SIGN_IN_ERRORS, go } from '../../lib/auth'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

/** The invite page needs no session: any call to the API is a mistake, so every fetch fails the test. */
function open(search: Record<string, string> = {}) {
  const fetched = vi.fn(async () => new Response('{}', { status: 500 }))
  vi.stubGlobal('fetch', fetched)
  const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
  const app = openApp('/invite', search)
  return { ...app, fetched, leave, user: userEvent.setup() }
}

describe('after asking to delete the account', () => {
  it('says when the account will be deleted, that sign-in is over, and that signing in again undoes it', async () => {
    open({ deleted: '2026-10-11T03:20:00Z' })
    const note = (await screen.findByText(/scheduled for deletion/)).closest('[role="status"]')
    expect(note).toHaveTextContent('Your account is scheduled for deletion on Oct 11, 2026.')
    expect(note).toHaveTextContent('signed out everywhere')
    expect(note).toHaveTextContent('sign in before then and nothing is lost')
  })

  it('still offers the invite form, since signing in is how it is undone', async () => {
    open({ deleted: '2026-10-11T03:20:00Z' })
    expect(await screen.findByLabelText('Invite code')).toBeInTheDocument()
  })

  it.each(['', 'soon', '2026-10-11', '<b>2026-10-11T03:20:00Z</b>'])('shows no date for %j', async (deleted) => {
    open({ deleted })
    await screen.findByRole('heading', { name: 'Enter your invite code' })
    expect(screen.queryByText(/scheduled for deletion/)).not.toBeInTheDocument()
  })

  it('shows nothing about deletion on an ordinary visit', async () => {
    open()
    await screen.findByRole('heading', { name: 'Enter your invite code' })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('the invite page', () => {
  it('says what it is: the beta, and what to do with the code', async () => {
    open()
    expect(await screen.findByRole('heading', { name: 'Enter your invite code' })).toBeInTheDocument()
    expect(screen.getByText('Private beta')).toBeInTheDocument()
    expect(screen.getByText('Codes are single-use and tied to one signup.')).toBeInTheDocument()
    expect(screen.getByLabelText('Invite code')).toHaveValue('')
  })

  it('works before sign-in: no session is asked for, and the app menu is not shown', async () => {
    const { fetched } = open()
    await screen.findByRole('heading', { name: 'Enter your invite code' })
    expect(fetched).not.toHaveBeenCalled()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Account menu' })).not.toBeInTheDocument()
  })

  it('fills in the code from an invite link', async () => {
    open({ code: 'TJ-7KQ2-M9XA' })
    expect(await screen.findByLabelText('Invite code')).toHaveValue('TJ-7KQ2-M9XA')
  })

  it('sends the code, tidied, to the server for sign-up', async () => {
    const { user, leave } = open()
    await user.type(await screen.findByLabelText('Invite code'), '  tj 7kq2 m9xa ')
    await user.click(screen.getByRole('button', { name: 'Continue to sign up' }))
    expect(leave).toHaveBeenCalledExactlyOnceWith('/auth/login?invite=TJ-7KQ2-M9XA')
  })

  it('says what a code looks like when what was typed does not, and goes nowhere', async () => {
    const { user, leave } = open()
    const box = await screen.findByLabelText('Invite code')
    await user.type(box, 'hello')
    await user.click(screen.getByRole('button', { name: 'Continue to sign up' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That does not look like an invite code. It looks like TJ-7KQ2-M9XA.',
    )
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(leave).not.toHaveBeenCalled()
    await user.type(box, '1') // typing again clears the complaint
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('submits with the Enter key', async () => {
    const { user, leave } = open({ code: 'TJ-7KQ2-M9XA' })
    await user.type(await screen.findByLabelText('Invite code'), '{Enter}')
    expect(leave).toHaveBeenCalled()
  })

  it.each(Object.entries(SIGN_IN_ERRORS))(
    'tells the person what happened when sign-in comes back with %s',
    async (code, sentence) => {
      open({ error: code })
      expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
      expect(screen.getByLabelText('Invite code')).toBeInTheDocument() // and they can try again from the same page
    },
  )

  it('shows nothing for an error code the server does not use, whatever the address says', async () => {
    open({ error: '<script>alert(1)</script>' })
    await screen.findByRole('heading', { name: 'Enter your invite code' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain('<script>alert')
  })

  it('shows no complaint when nothing went wrong', async () => {
    open()
    await screen.findByRole('heading', { name: 'Enter your invite code' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('offers sign-in for people who already have an account', async () => {
    open()
    const link = await screen.findByRole('link', { name: /I already have an account/ })
    expect(link).toHaveAttribute('href', '/auth/login?next=%2F')
  })

  it('points people without a code at the waitlist, and at the terms and the privacy policy', async () => {
    open()
    await screen.findByRole('heading', { name: 'Enter your invite code' })
    expect(screen.getByRole('link', { name: 'Join the waitlist' })).toHaveAttribute(
      'href',
      'https://roundturn.app/#join',
    )
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', 'https://roundturn.app/terms/')
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute(
      'href',
      'https://roundturn.app/privacy/',
    )
    expect(screen.getByText(/is not investment advice/)).toBeInTheDocument()
  })

  it('keeps the page reachable by keyboard in a sensible order', async () => {
    const { user } = open()
    const box = await screen.findByLabelText('Invite code')
    await user.click(box)
    await user.tab()
    expect(screen.getByRole('button', { name: 'Continue to sign up' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('link', { name: /I already have an account/ })).toHaveFocus()
    await waitFor(() =>
      expect(within(screen.getByRole('main')).getByRole('link', { name: 'Join the waitlist' })).toBeInTheDocument(),
    )
  })
})
