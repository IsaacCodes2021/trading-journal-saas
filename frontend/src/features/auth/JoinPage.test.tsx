import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

type Seen = { url: string; init: RequestInit | undefined }

/** The join page needs no session: the only call it may make is the form's own, so every other fetch fails the test. */
function open(answer: () => Promise<Response> | Response = () => new Response('{"status":"ok"}', { status: 202 })) {
  const seen: Seen[] = []
  const fetched = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
    seen.push({ url, init })
    if (url === '/api/v1/beta/join') return answer()
    return new Response('{}', { status: 500 })
  })
  vi.stubGlobal('fetch', fetched)
  const app = openApp('/join')
  return { ...app, seen, user: userEvent.setup() }
}

const heading = { name: 'Join the beta' }

async function fill(
  user: ReturnType<typeof userEvent.setup>,
  name = 'Alex',
  email = 'alex@example.com',
  firm = 'Topstep',
) {
  if (name) await user.type(await screen.findByLabelText('First name'), name)
  if (email) await user.type(screen.getByLabelText('Email'), email)
  if (firm) await user.type(screen.getByLabelText('Favourite prop firm'), firm)
}

const submit = () => screen.getByRole('button', { name: /^(Join the beta|Sending…)$/ })
const bodyOf = (seen: Seen[]) => JSON.parse(String(seen.at(-1)?.init?.body)) as Record<string, string>

describe('the form', () => {
  it('says what it is and what will happen', async () => {
    open()
    expect(await screen.findByRole('heading', heading)).toBeInTheDocument()
    expect(
      screen.getByText(/We will email you a sign-up code. It works once and is tied to your email address./),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('First name')).toHaveValue('')
    expect(screen.getByLabelText('Email')).toHaveValue('')
    expect(screen.getByLabelText('Favourite prop firm')).toHaveValue('')
    expect(screen.getByText('Type "none" if you do not use one yet.')).toBeInTheDocument()
  })

  it('works before sign-in: nothing is asked of the server until it is sent, and the app menu is not shown', async () => {
    const { seen } = open()
    await screen.findByRole('heading', heading)
    expect(seen).toEqual([])
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Account menu' })).not.toBeInTheDocument()
  })

  it('points people who have a code to sign-up, and everyone to how their details are handled', async () => {
    open()
    await screen.findByRole('heading', heading)
    expect(screen.getByRole('link', { name: 'Sign up here' })).toHaveAttribute('href', '/signup')
    expect(screen.getByRole('link', { name: 'How your details are handled' })).toHaveAttribute(
      'href',
      'https://roundturn.app/privacy/',
    )
  })

  it('keeps the fields in a sensible keyboard order, and the trap out of it', async () => {
    const { user } = open()
    await user.click(await screen.findByLabelText('First name'))
    await user.tab()
    expect(screen.getByLabelText('Email')).toHaveFocus()
    await user.tab()
    expect(screen.getByLabelText('Favourite prop firm')).toHaveFocus()
    await user.tab()
    expect(submit()).toHaveFocus()
  })

  it('has a field only a script would fill in, out of the tab order and hidden from screen readers', async () => {
    open()
    await screen.findByRole('heading', heading)
    const field = document.getElementById('join-website') as HTMLInputElement
    expect(field.tabIndex).toBe(-1)
    expect(field.closest('[aria-hidden="true"]')).not.toBeNull()
    expect(field).toHaveValue('')
  })

  it('limits what can be typed to what the server accepts', async () => {
    open()
    await screen.findByRole('heading', heading)
    expect(screen.getByLabelText('First name')).toHaveAttribute('maxlength', '80')
    expect(screen.getByLabelText('Email')).toHaveAttribute('maxlength', '254')
    expect(screen.getByLabelText('Favourite prop firm')).toHaveAttribute('maxlength', '80')
  })
})

describe('what is wrong with the form', () => {
  it('says so for each field, flags it, and sends nothing', async () => {
    const { user, seen } = open()
    await screen.findByRole('heading', heading)
    await user.click(submit())
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.map((a) => a.textContent)).toEqual([
      'Enter your first name.',
      'That does not look like an email address.',
      'Enter a firm, or "none" if you do not use one yet.',
    ])
    expect(screen.getByLabelText('First name')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Favourite prop firm')).toHaveAttribute('aria-invalid', 'true')
    expect(seen).toEqual([])
  })

  it('clears a field’s complaint as soon as that field is typed in, and leaves the others', async () => {
    const { user } = open()
    await screen.findByRole('heading', heading)
    await user.click(submit())
    await screen.findAllByRole('alert')
    await user.type(screen.getByLabelText('First name'), 'A')
    expect(screen.getAllByRole('alert')).toHaveLength(2)
    expect(screen.getByLabelText('First name')).toHaveAttribute('aria-invalid', 'false')
  })

  it.each(['alex', 'alex@', '@example.com', 'alex@example', 'a lex@example.com'])(
    'does not accept %j as an address',
    async (email) => {
      const { user, seen } = open()
      await fill(user, 'Alex', email, 'Topstep')
      await user.click(submit())
      expect(await screen.findByRole('alert')).toHaveTextContent('That does not look like an email address.')
      expect(seen).toEqual([])
    },
  )
})

describe('sending it', () => {
  it('posts the trimmed answers as JSON to the form’s own address, with the hidden field empty', async () => {
    const { user, seen } = open()
    await fill(user, '  Alex ', ' alex@example.com ', ' Topstep ')
    await user.click(submit())
    await screen.findByRole('heading', { name: 'Check your email' })
    expect(seen).toHaveLength(1)
    expect(seen[0]?.url).toBe('/api/v1/beta/join')
    expect(seen[0]?.init?.method).toBe('POST')
    expect(seen[0]?.init?.headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(bodyOf(seen)).toEqual({
      first_name: 'Alex',
      email: 'alex@example.com',
      prop_firm: 'Topstep',
      website: '',
    })
  })

  it('sends what a script put in the hidden field, so the server can tell', async () => {
    const { user, seen } = open()
    await fill(user)
    const trap = document.getElementById('join-website') as HTMLInputElement
    await user.type(trap, 'http://spam.example')
    await user.click(submit())
    await screen.findByRole('heading', { name: 'Check your email' })
    expect(bodyOf(seen)['website']).toBe('http://spam.example')
  })

  it('says to check email, with the name and the address, and what the code gives', async () => {
    const { user } = open()
    await fill(user)
    await user.click(submit())
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    const note = screen.getByRole('status')
    expect(note).toHaveTextContent('Thanks, Alex.')
    expect(note).toHaveTextContent('alex@example.com')
    expect(note).toHaveTextContent('your sign-up code is on its way')
    expect(note).toHaveTextContent('check your spam folder')
    expect(screen.getByText(/30 days of full access, counted from the day you create your account/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /I have my code/ })).toHaveAttribute('href', '/signup')
    expect(screen.getByRole('link', { name: 'Back to roundturn.app' })).toHaveAttribute('href', 'https://roundturn.app')
  })

  it('does not say a code was sent for certain: the server answers the same whatever became of the address', async () => {
    const { user } = open()
    await fill(user)
    await user.click(submit())
    const note = await screen.findByRole('status')
    expect(note).toHaveTextContent('If alex@example.com can join')
  })

  it('shows that it is working, and sends only once however often it is pressed', async () => {
    let release: (response: Response) => void = () => undefined
    const { user, seen } = open(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve
        }),
    )
    await fill(user)
    await user.click(submit())
    const busy = await screen.findByRole('button', { name: 'Sending…' })
    expect(busy).toBeDisabled()
    await user.click(busy)
    await user.type(screen.getByLabelText('Favourite prop firm'), '{Enter}')
    fireEvent.submit(busy.closest('form') as HTMLFormElement) // a script, or a browser that ignores the disabled button
    expect(seen).toHaveLength(1)
    release(new Response('{"status":"ok"}', { status: 202 }))
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
  })

  it.each([
    [429, 'Too many tries from here. Please wait a little and try again.'],
    [503, 'Joining the beta is not open yet. Please try again later.'],
    [422, 'Something in the form was not accepted. Please check it and try again.'],
    [500, 'Something went wrong. Please try again.'],
    [403, 'Something went wrong. Please try again.'],
  ])(
    'says one plain sentence for a %i answer, keeps what was typed, and lets them try again',
    async (status, words) => {
      const { user } = open(
        () =>
          new Response(
            JSON.stringify({ detail: 'internal: secret reason', errors: [{ field: 'email', message: 'no' }] }),
            {
              status,
            },
          ),
      )
      await fill(user)
      await user.click(submit())
      expect(await screen.findByRole('alert')).toHaveTextContent(words)
      expect(document.body).not.toHaveTextContent('secret reason')
      expect(screen.getByLabelText('First name')).toHaveValue('Alex')
      expect(screen.getByLabelText('Email')).toHaveValue('alex@example.com')
      expect(submit()).toBeEnabled()
    },
  )

  it('says so when the server cannot be reached, and lets them try again', async () => {
    let calls = 0
    const { user } = open(() => {
      calls += 1
      if (calls === 1) throw new TypeError('network down')
      return new Response('{"status":"ok"}', { status: 202 })
    })
    await fill(user)
    await user.click(submit())
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not reach the server. Check your connection and try again.',
    )
    await user.click(submit())
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('goes on to the sign-up page from the "sent" screen', async () => {
    const { user, router } = open()
    await fill(user)
    await user.click(submit())
    const link = await screen.findByRole('link', { name: /I have my code/ })
    await user.click(link)
    await waitFor(() => expect(router.state.location.pathname).toBe('/signup'))
    expect(await screen.findByRole('heading', { name: 'Create your account' })).toBeInTheDocument()
    expect(within(screen.getByRole('main')).getByLabelText('Sign-up code')).toBeInTheDocument()
  })
})
