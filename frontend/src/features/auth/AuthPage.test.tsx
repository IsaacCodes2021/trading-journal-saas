import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp, type AppPath } from '../../test-utils'
import { SIGN_IN_ERRORS, go } from '../../lib/auth'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

/** The way-in pages need no session: any call to the API is a mistake, so every fetch fails the test. */
function open(to: AppPath, search: Record<string, string> = {}) {
  const fetched = vi.fn(async () => new Response('{}', { status: 500 }))
  vi.stubGlobal('fetch', fetched)
  const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
  const app = openApp(to, search)
  return { ...app, fetched, leave, user: userEvent.setup() }
}

const signInHeading = { name: 'Welcome back' }
const signUpHeading = { name: 'Create your account' }

describe('signing in', () => {
  it('says what it is, and offers one way forward', async () => {
    open('/signin')
    expect(await screen.findByRole('heading', signInHeading)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Continue to sign in' })).toHaveAttribute('href', '/auth/login?next=%2F')
    expect(screen.getByText(/confirm your email and password on our sign-in service/)).toBeInTheDocument()
  })

  it('works before sign-in: no session is asked for, and the app menu is not shown', async () => {
    const { fetched } = open('/signin')
    await screen.findByRole('heading', signInHeading)
    expect(fetched).not.toHaveBeenCalled()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Account menu' })).not.toBeInTheDocument()
  })

  it('carries the place the person was going through sign-in', async () => {
    open('/signin', { next: '/trades?preset=7d' })
    const link = await screen.findByRole('link', { name: 'Continue to sign in' })
    expect(link).toHaveAttribute('href', '/auth/login?next=%2Ftrades%3Fpreset%3D7d')
  })

  it.each(['https://evil.test/', '//evil.test', '/auth/logout', '/signin', '/signup?code=x', '/invite', '/a\\b'])(
    'does not carry %j: it is not a place in the app',
    async (next) => {
      open('/signin', { next })
      const link = await screen.findByRole('link', { name: 'Continue to sign in' })
      expect(link).toHaveAttribute('href', '/auth/login?next=%2F')
    },
  )

  it('points people with a sign-up code at the sign-up form, keeping the place they were going', async () => {
    open('/signin', { next: '/trades' })
    const link = await screen.findByRole('link', { name: 'Have a sign-up code? Sign up here' })
    expect(link).toHaveAttribute('href', '/signup?next=%2Ftrades')
  })

  it('points people without a code at the beta form, and at the terms and the privacy policy', async () => {
    open('/signin')
    await screen.findByRole('heading', signInHeading)
    expect(screen.getByRole('link', { name: 'Join the beta' })).toHaveAttribute('href', '/join')
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', 'https://roundturn.app/terms/')
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute(
      'href',
      'https://roundturn.app/privacy/',
    )
    expect(screen.getByText(/is not investment advice/)).toBeInTheDocument()
  })

  it('marks which of the two pages this is', async () => {
    open('/signin')
    const nav = await screen.findByRole('navigation', { name: 'Sign in or sign up' })
    expect(within(nav).getByRole('link', { name: 'Sign in' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Sign up' })).not.toHaveAttribute('aria-current')
  })

  it('keeps the place the person was going when they use the switch', async () => {
    open('/signin', { next: '/trades' })
    const nav = await screen.findByRole('navigation', { name: 'Sign in or sign up' })
    expect(within(nav).getByRole('link', { name: 'Sign up' })).toHaveAttribute('href', '/signup?next=%2Ftrades')
    expect(within(nav).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/signin?next=%2Ftrades')
  })

  it('moves to the sign-up form from the switch, without leaving the app', async () => {
    const { user, fetched } = open('/signin')
    const nav = await screen.findByRole('navigation', { name: 'Sign in or sign up' })
    await user.click(within(nav).getByRole('link', { name: 'Sign up' }))
    expect(await screen.findByRole('heading', signUpHeading)).toBeInTheDocument()
    expect(screen.getByLabelText('Sign-up code')).toBeInTheDocument()
    expect(fetched).not.toHaveBeenCalled()
  })
})

describe('signing up', () => {
  it('says what it is: the beta, and what to do with the code', async () => {
    open('/signup')
    expect(await screen.findByRole('heading', signUpHeading)).toBeInTheDocument()
    expect(screen.getByText('INVITE-ONLY BETA')).toBeInTheDocument()
    expect(screen.getByText('It works once and is tied to the email address we sent it to.')).toBeInTheDocument()
    expect(screen.getByText(/30 days of full access, counted from the day you create your account/)).toBeInTheDocument()
    expect(screen.getByLabelText('Sign-up code')).toHaveValue('')
  })

  it('works before sign-in: no session is asked for, and the app menu is not shown', async () => {
    const { fetched } = open('/signup')
    await screen.findByRole('heading', signUpHeading)
    expect(fetched).not.toHaveBeenCalled()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('fills in the code from the link in the email', async () => {
    open('/signup', { code: 'TJ-7KQ2-M9XA' })
    expect(await screen.findByLabelText('Sign-up code')).toHaveValue('TJ-7KQ2-M9XA')
  })

  it('sends the code, tidied, to the server for sign-up', async () => {
    const { user, leave } = open('/signup')
    await user.type(await screen.findByLabelText('Sign-up code'), '  tj 7kq2 m9xa ')
    await user.click(screen.getByRole('button', { name: 'Continue to create account' }))
    expect(leave).toHaveBeenCalledExactlyOnceWith('/auth/login?invite=TJ-7KQ2-M9XA')
  })

  it('says what a code looks like when what was typed does not, and goes nowhere', async () => {
    const { user, leave } = open('/signup')
    const box = await screen.findByLabelText('Sign-up code')
    await user.type(box, 'hello')
    await user.click(screen.getByRole('button', { name: 'Continue to create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That does not look like a sign-up code. It looks like TJ-7KQ2-M9XA.',
    )
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(leave).not.toHaveBeenCalled()
    await user.type(box, '1') // typing again clears the complaint
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(box).toHaveAttribute('aria-invalid', 'false')
  })

  it('submits with the Enter key', async () => {
    const { user, leave } = open('/signup', { code: 'TJ-7KQ2-M9XA' })
    await user.type(await screen.findByLabelText('Sign-up code'), '{Enter}')
    expect(leave).toHaveBeenCalledWith('/auth/login?invite=TJ-7KQ2-M9XA')
  })

  it('points people without a code at the beta form', async () => {
    open('/signup')
    await screen.findByRole('heading', signUpHeading)
    expect(screen.getByRole('link', { name: 'Join the beta' })).toHaveAttribute('href', '/join')
  })

  it('keeps the page reachable by keyboard in a sensible order', async () => {
    const { user } = open('/signup')
    const box = await screen.findByLabelText('Sign-up code')
    await user.click(box)
    await user.tab()
    expect(screen.getByLabelText(/Promo code/)).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Continue to create account' })).toHaveFocus()
    await user.tab()
    expect(within(screen.getByRole('main')).getByRole('link', { name: 'Join the beta' })).toHaveFocus()
  })

  it('says the code did not work, on the box and in words, and keeps the way to get another', async () => {
    open('/signup', { error: 'invite_invalid' })
    expect(await screen.findByRole('alert')).toHaveTextContent(SIGN_IN_ERRORS.invite_invalid)
    expect(screen.getByLabelText('Sign-up code')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('link', { name: 'join the beta again' })).toHaveAttribute('href', '/join')
    expect(screen.getByRole('link', { name: 'hello@roundturn.app' })).toHaveAttribute(
      'href',
      'mailto:hello@roundturn.app',
    )
    expect(screen.queryByText(/30 days of full access/)).not.toBeInTheDocument()
  })
})

describe('the address the server and the emails use', () => {
  it('opens on sign-in for an ordinary visit, the place signing out ends', async () => {
    open('/invite')
    expect(await screen.findByRole('heading', signInHeading)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('opens on sign-up, with the code in the box, from the link in the email', async () => {
    open('/invite', { code: 'TJ-7KQ2-M9XA' })
    expect(await screen.findByRole('heading', signUpHeading)).toBeInTheDocument()
    expect(screen.getByLabelText('Sign-up code')).toHaveValue('TJ-7KQ2-M9XA')
  })

  it('marks the form it opened on as the current page in the switch', async () => {
    const first = open('/invite')
    const nav = await screen.findByRole('navigation', { name: 'Sign in or sign up' })
    expect(within(nav).getByRole('link', { name: 'Sign in' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Sign up' })).not.toHaveAttribute('aria-current')
    first.unmount()

    open('/invite', { code: 'TJ-7KQ2-M9XA' })
    const second = await screen.findByRole('navigation', { name: 'Sign in or sign up' })
    expect(within(second).getByRole('link', { name: 'Sign up' })).toHaveAttribute('aria-current', 'page')
    expect(within(second).getByRole('link', { name: 'Sign in' })).not.toHaveAttribute('aria-current')
  })

  it('opens on sign-up when the server refused the code', async () => {
    open('/invite', { error: 'invite_invalid' })
    expect(await screen.findByRole('heading', signUpHeading)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(SIGN_IN_ERRORS.invite_invalid)
  })

  it('opens on sign-up with both codes back in their boxes when the server refused the promo code', async () => {
    open('/invite', { error: 'promo_invalid', code: 'TJ-7KQ2-M9XA', promo: 'nope99' })
    expect(await screen.findByRole('heading', signUpHeading)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(SIGN_IN_ERRORS.promo_invalid)
    expect(screen.getByLabelText('Sign-up code')).toHaveValue('TJ-7KQ2-M9XA')
    expect(screen.getByLabelText(/Promo code/)).toHaveValue('nope99')
    expect(screen.getByLabelText(/Promo code/)).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Sign-up code')).toHaveAttribute('aria-invalid', 'false')
  })

  it.each(Object.entries(SIGN_IN_ERRORS).filter(([code]) => code !== 'invite_invalid' && code !== 'promo_invalid'))(
    'tells the person what happened when sign-in comes back with %s, and lets them try again',
    async (code, sentence) => {
      open('/invite', { error: code })
      expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
      expect(screen.getByRole('link', { name: 'Continue to sign in' })).toBeInTheDocument()
    },
  )

  it('shows nothing for an error code the server does not use, whatever the address says', async () => {
    open('/invite', { error: '<script>alert(1)</script>' })
    await screen.findByRole('heading', signInHeading)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain('<script>alert')
  })
})

describe('after asking to delete the account', () => {
  it('says when the account will be deleted, that sign-in is over, and that signing in again undoes it', async () => {
    open('/invite', { deleted: '2026-10-11T03:20:00Z' })
    const note = (await screen.findByText(/scheduled for deletion/)).closest('[role="status"]')
    expect(note).toHaveTextContent('Your account is scheduled for deletion on Oct 11, 2026.')
    expect(note).toHaveTextContent('signed out everywhere')
    expect(note).toHaveTextContent('sign in before then and nothing is lost')
  })

  it('still offers sign-in, since signing in is how it is undone', async () => {
    open('/invite', { deleted: '2026-10-11T03:20:00Z' })
    expect(await screen.findByRole('link', { name: 'Continue to sign in' })).toBeInTheDocument()
  })

  it.each(['', 'soon', '2026-10-11', '<b>2026-10-11T03:20:00Z</b>'])('shows no date for %j', async (deleted) => {
    open('/invite', { deleted })
    await screen.findByRole('heading', signInHeading)
    expect(screen.queryByText(/scheduled for deletion/)).not.toBeInTheDocument()
  })
})

describe('a promo code at sign-up', () => {
  it('is optional: the sign-up goes ahead without one', async () => {
    const { user, leave } = open('/signup')
    await user.type(await screen.findByLabelText('Sign-up code'), 'TJ-7KQ2-M9XA')
    await user.click(screen.getByRole('button', { name: 'Continue to create account' }))
    await waitFor(() => expect(leave).toHaveBeenCalledWith('/auth/login?invite=TJ-7KQ2-M9XA'))
  })

  it('goes to the server beside the sign-up code, as typed', async () => {
    const { user, leave } = open('/signup')
    await user.type(await screen.findByLabelText('Sign-up code'), 'TJ-7KQ2-M9XA')
    await user.type(screen.getByLabelText(/Promo code/), 'free30')
    await user.click(screen.getByRole('button', { name: 'Continue to create account' }))
    await waitFor(() => expect(leave).toHaveBeenCalledWith('/auth/login?invite=TJ-7KQ2-M9XA&promo=free30'))
  })

  it('is checked for its shape first, and nothing is sent while it cannot be a code', async () => {
    const { user, leave } = open('/signup')
    await user.type(await screen.findByLabelText('Sign-up code'), 'TJ-7KQ2-M9XA')
    await user.type(screen.getByLabelText(/Promo code/), 'no!')
    await user.click(screen.getByRole('button', { name: 'Continue to create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That does not look like a promo code')
    expect(leave).not.toHaveBeenCalled()
  })
})

describe('an old address', () => {
  it('still reaches the sign-up form from /invite with a code, and signs up with it', async () => {
    const { user, leave } = open('/invite', { code: 'tj-7kq2-m9xa' })
    await user.click(await screen.findByRole('button', { name: 'Continue to create account' }))
    await waitFor(() => expect(leave).toHaveBeenCalledWith('/auth/login?invite=TJ-7KQ2-M9XA'))
  })
})
