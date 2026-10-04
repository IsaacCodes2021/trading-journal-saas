import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { go } from '../../lib/auth'
import { openApp, stubApi, type ApiOptions } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const EMAIL = 'alice@example.test'

async function open(opts: ApiOptions = {}, search: Record<string, string | number> = {}) {
  stubApi(opts)
  const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
  const app = openApp('/settings/privacy', search)
  await screen.findByRole('heading', { name: 'Data & privacy' })
  return { ...app, leave, user: userEvent.setup() }
}

/** Long enough for a request that the page was going to make to have been made: a request is sent a moment after the click. */
const settled = () => new Promise((resolve) => setTimeout(resolve, 50))

const card = () => screen.getByRole('region', { name: 'Delete your account' })
const dialog = () => screen.findByRole('dialog', { name: 'Delete your account' })

/** The requests that asked to delete the account: the address, the headers and the body. */
async function deletionRequests() {
  const seen: { method: string; csrf: string | null; body: unknown }[] = []
  for (const [request] of vi.mocked(globalThis.fetch).mock.calls as unknown as [Request][]) {
    if (new URL(request.url).pathname !== '/api/v1/account/deletion') continue
    seen.push({
      method: request.method,
      csrf: request.headers.get('X-CSRF-Token'),
      body: await request.clone().json(),
    })
  }
  return seen
}

describe('the delete-account card', () => {
  it('says what deleting does, in the card, without opening anything', async () => {
    await open()
    expect(within(card()).getByText(/a week after you ask/)).toBeInTheDocument()
    expect(within(card()).getByText(/signing in again cancels it/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens a dialog that says exactly what will happen, and offers a copy of the data first', async () => {
    const { user } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    expect(box.getByRole('heading', { name: 'Delete your account?' })).toBeInTheDocument()
    expect(box.getByText('permanently deleted 7 days from now')).toBeInTheDocument()
    expect(box.getByText(/accounts, trades, notes, tags, screenshots and import history/)).toBeInTheDocument()
    expect(box.getByText(/signed out everywhere, straight away/)).toBeInTheDocument()
    expect(box.getByText('sign in again before then')).toBeInTheDocument()
    expect(box.getByRole('link', { name: 'Download your data' })).toHaveAttribute('href', '/api/v1/export')
  })

  it('closes with Cancel and with Escape, and asks the server for nothing', async () => {
    const { user } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    await user.click(within(await dialog()).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    await dialog()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await deletionRequests()).toEqual([])
  })
})

describe('deleting, after signing in a moment ago', () => {
  it('keeps the red button off until the typed address is the account’s own', async () => {
    const { user } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    const button = box.getByRole('button', { name: 'Delete my account' })
    expect(button).toBeDisabled()
    const field = box.getByLabelText('Type your email address to confirm')
    expect(box.getByText(EMAIL)).toBeInTheDocument() // the address to type is shown beside the box
    await user.type(field, 'alice@example')
    expect(button).toBeDisabled()
    await user.type(field, '.testx')
    expect(button).toBeDisabled()
    await user.clear(field)
    await user.type(field, 'bob@example.test')
    expect(button).toBeDisabled()
    await user.clear(field)
    await user.type(field, EMAIL)
    expect(button).toBeEnabled()
  })

  it('does not mind capitals or stray spaces in the typed address', async () => {
    const { user } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    await user.type(box.getByLabelText('Type your email address to confirm'), '  Alice@Example.TEST ')
    expect(box.getByRole('button', { name: 'Delete my account' })).toBeEnabled()
  })

  it('sends the address with the CSRF token once, then goes to the sign-in page with the date', async () => {
    const { user, leave } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    await user.type(box.getByLabelText('Type your email address to confirm'), EMAIL)
    await user.click(box.getByRole('button', { name: 'Delete my account' }))
    await waitFor(() => expect(leave).toHaveBeenCalledTimes(1))
    expect(leave).toHaveBeenCalledWith('/invite?deleted=2026-10-11T03%3A20%3A00Z')
    expect(await deletionRequests()).toEqual([{ method: 'POST', csrf: 'tok', body: { email: EMAIL } }])
  })

  it('sends what was typed, trimmed, so the server is the one that compares it', async () => {
    const { user, leave } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    await user.type(box.getByLabelText('Type your email address to confirm'), ' ALICE@example.test {Enter}')
    await waitFor(() => expect(leave).toHaveBeenCalled())
    expect(await deletionRequests()).toEqual([{ method: 'POST', csrf: 'tok', body: { email: 'ALICE@example.test' } }])
  })

  it('does not send anything when Enter is pressed before the address matches', async () => {
    const { user, leave } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    await user.type(box.getByLabelText('Type your email address to confirm'), 'alice{Enter}')
    await settled()
    expect(await deletionRequests()).toEqual([])
    expect(leave).not.toHaveBeenCalled()
  })

  it('does not send anything when the form is submitted some other way before the address matches', async () => {
    const { user, leave } = await open()
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    const field = box.getByLabelText('Type your email address to confirm')
    await user.type(field, 'alice')
    fireEvent.submit(field)
    await settled()
    expect(await deletionRequests()).toEqual([])
    expect(leave).not.toHaveBeenCalled()
  })

  it('shows what the server said when it refuses, stays put, and lets the person try again', async () => {
    const { user, leave } = await open({
      deletion: {
        status: 403,
        body: { title: 'Forbidden', status: 403, detail: 'Sign in again first, so we know it is you.', errors: null },
      },
    })
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    await user.type(box.getByLabelText('Type your email address to confirm'), EMAIL)
    await user.click(box.getByRole('button', { name: 'Delete my account' }))
    expect(await box.findByRole('alert')).toHaveTextContent('Sign in again first, so we know it is you.')
    expect(leave).not.toHaveBeenCalled()
    expect(box.getByRole('button', { name: 'Delete my account' })).toBeEnabled()
    expect(box.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  })

  it('cannot be closed, or sent twice, while the request is out', async () => {
    let answer: (response: Response) => void = () => undefined
    stubApi()
    const normal = globalThis.fetch
    vi.stubGlobal(
      'fetch',
      vi.fn((request: Request) =>
        new URL(request.url).pathname === '/api/v1/account/deletion'
          ? new Promise<Response>((resolve) => (answer = resolve))
          : normal(request),
      ),
    )
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    openApp('/settings/privacy')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    await user.type(box.getByLabelText('Type your email address to confirm'), EMAIL)
    await user.click(box.getByRole('button', { name: 'Delete my account' }))
    const working = await box.findByRole('button', { name: 'Deleting…' })
    expect(working).toBeDisabled()
    fireEvent.submit(box.getByLabelText('Type your email address to confirm')) // a form can be submitted without its button
    expect(box.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: 'Delete your account' })).toBeInTheDocument()
    answer(new Response(JSON.stringify({ scheduled_for: '2026-10-11T03:20:00Z' }), { status: 200 }))
    await waitFor(() => expect(leave).toHaveBeenCalledTimes(1))
    expect(await deletionRequests()).toHaveLength(1)
  })
})

describe('deleting, when the person has not signed in a moment ago', () => {
  it('asks them to sign in again first, with no way to type the address or delete', async () => {
    const { user } = await open({ recentlyAuthenticated: false })
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    const box = within(await dialog())
    expect(box.getByRole('note')).toHaveTextContent('sign in again first')
    expect(box.queryByLabelText('Type your email address to confirm')).not.toBeInTheDocument()
    expect(box.queryByRole('button', { name: 'Delete my account' })).not.toBeInTheDocument()
    expect(await deletionRequests()).toEqual([])
  })

  it('sends them through sign-in with the dialog as the place to come back to', async () => {
    const { user, leave } = await open({ recentlyAuthenticated: false })
    await user.click(within(card()).getByRole('button', { name: 'Delete my account…' }))
    await user.click(within(await dialog()).getByRole('button', { name: 'Sign in again' }))
    expect(leave).toHaveBeenCalledExactlyOnceWith('/auth/login?reauth=1&next=%2Fsettings%2Fprivacy%3Fdelete%3D1')
  })
})

describe('coming back to the page', () => {
  it('opens the dialog straight away after signing in again for it (?delete=1)', async () => {
    await open({}, { delete: 1 })
    const box = within(await dialog())
    expect(box.getByLabelText('Type your email address to confirm')).toBeInTheDocument()
  })

  it('takes ?delete=1 out of the address when the dialog is closed, so a reload does not reopen it', async () => {
    const { user, router } = await open({}, { delete: 1 })
    await user.click(within(await dialog()).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(router.state.location.search).toEqual({}))
    expect(router.state.location.href).toBe('/settings/privacy')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens for the form the router writes back into the address (?delete=true), so a reload keeps the dialog', async () => {
    await open({}, { delete: 'true' })
    expect(await dialog()).toBeInTheDocument()
  })

  it.each([{ delete: 0 }, { delete: 'yes' }, { delete: '' }, { delete: 'false' }])('ignores %j', async (search) => {
    await open({}, search)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('says a scheduled deletion was cancelled by signing in (?restored=1), and nothing otherwise', async () => {
    await open({}, { restored: 1 })
    expect(screen.getByRole('status')).toHaveTextContent('Welcome back.')
    expect(screen.getByRole('status')).toHaveTextContent('Nothing was deleted.')
  })

  it('says it for ?restored=true as well', async () => {
    await open({}, { restored: 'true' })
    expect(screen.getByRole('status')).toHaveTextContent('Welcome back.')
  })

  it('does not say that on an ordinary visit', async () => {
    await open()
    expect(screen.queryByText('Welcome back.')).not.toBeInTheDocument()
  })
})
