import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp, stubApi, type ApiOptions } from '../../test-utils'

afterEach(() => vi.unstubAllGlobals())

async function open(opts: ApiOptions = {}) {
  stubApi(opts)
  openApp('/settings/plan')
  await screen.findByRole('heading', { name: 'Plan & billing' })
  return userEvent.setup()
}

const card = () => screen.getByRole('region', { name: 'Redeem a promo code' })
const field = () => within(card()).getByLabelText('Promo code')
const button = () => within(card()).getByRole('button', { name: 'Redeem' })

/** The requests that tried a code: the method, the CSRF token and the body. */
async function attempts() {
  const seen: { csrf: string | null; body: unknown }[] = []
  for (const [request] of vi.mocked(globalThis.fetch).mock.calls as unknown as [Request][]) {
    if (new URL(request.url).pathname !== '/api/v1/grants/redeem') continue
    seen.push({ csrf: request.headers.get('X-CSRF-Token'), body: await request.clone().json() })
  }
  return seen
}

describe('redeeming a promo code', () => {
  it('offers a box for it, with the shape of a code, and nothing to press until something is typed', async () => {
    await open()
    expect(within(card()).getByRole('heading', { name: 'Have a promo code?' })).toBeInTheDocument()
    expect(within(card()).getByText(/For example FREE30/)).toBeInTheDocument()
    expect(field()).toHaveValue('')
    expect(button()).toBeDisabled()
  })

  it('stays off for spaces alone, and turns on once there is something to send', async () => {
    const user = await open()
    await user.type(field(), '   ')
    expect(button()).toBeDisabled()
    await user.type(field(), 'gr-abcd-efgh')
    expect(button()).toBeEnabled()
  })

  it('sends the code as typed, trimmed, with the CSRF token, and says what plan the person is on now', async () => {
    const user = await open()
    await user.type(field(), '  gr abcd efgh ')
    await user.click(button())
    expect(await within(card()).findByRole('status')).toHaveTextContent(
      'Done. You are on the pro plan until Jan 12, 2027.',
    )
    expect(await attempts()).toEqual([{ csrf: 'tok', body: { code: 'gr abcd efgh' } }])
  })

  it('reads the plan again, so the page shows the new plan, where it came from and when it ends', async () => {
    const user = await open()
    await user.type(field(), 'GR-ABCD-EFGH')
    await user.click(button())
    const plan = await screen.findByRole('region', { name: 'Your plan' })
    await waitFor(() => expect(plan).toHaveTextContent('pro'))
    expect(plan).toHaveTextContent('Given to you by an invitation or a code.')
    expect(plan).toHaveTextContent('Ends Jan 12, 2027.')
  })

  it('empties the box after a code worked, so it cannot be sent twice by accident', async () => {
    const user = await open()
    await user.type(field(), 'GR-ABCD-EFGH')
    await user.click(button())
    await within(card()).findByRole('status')
    expect(field()).toHaveValue('')
    expect(button()).toBeDisabled()
  })

  it('shows the same sentence the server gave for a code that did not work, and keeps what was typed', async () => {
    const detail = 'That code did not work. It may be wrong, already used, expired, or revoked.'
    const user = await open({
      redeem: { status: 422, body: { title: 'Unprocessable', status: 422, detail, errors: null } },
    })
    await user.type(field(), 'GR-NOPE-NOPE')
    await user.click(button())
    expect(await within(card()).findByRole('alert')).toHaveTextContent(detail)
    expect(field()).toHaveValue('GR-NOPE-NOPE')
    expect(button()).toBeEnabled() // and can be tried again
    expect(within(card()).queryByRole('status')).not.toBeInTheDocument()
  })

  it('says why when a plan from a code is still running, in the server’s words', async () => {
    const detail = 'You already have a plan from a code, until Dec 3, 2026. Use another code when it ends.'
    const user = await open({ redeem: { status: 409, body: { title: 'Conflict', status: 409, detail, errors: null } } })
    await user.type(field(), 'GR-ABCD-EFGH')
    await user.click(button())
    expect(await within(card()).findByRole('alert')).toHaveTextContent(detail)
  })

  it('says it is slowing down when there have been too many tries', async () => {
    const detail = 'Too many requests. Try again in 20 minutes.'
    const user = await open({
      redeem: { status: 429, body: { title: 'Too Many Requests', status: 429, detail, errors: null } },
    })
    await user.type(field(), 'GR-ABCD-EFGH')
    await user.click(button())
    expect(await within(card()).findByRole('alert')).toHaveTextContent(detail)
  })

  it('can be tried again after a refusal, with Enter', async () => {
    const user = await open({
      redeem: { status: 422, body: { title: 'x', status: 422, detail: 'Nope.', errors: null } },
    })
    await user.type(field(), 'GR-NOPE-NOPE')
    await user.click(button())
    expect(await within(card()).findByRole('alert')).toBeInTheDocument()
    await user.type(field(), 'x')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(attempts()).resolves.toHaveLength(2))
  })

  it('sends one request however many times Enter is pressed while it is out', async () => {
    stubApi()
    const normal = globalThis.fetch
    let answer: (response: Response) => void = () => undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((request: Request) =>
        new URL(request.url).pathname === '/api/v1/grants/redeem'
          ? new Promise<Response>((resolve) => (answer = resolve))
          : normal(request),
      ),
    )
    openApp('/settings/plan')
    const user = userEvent.setup()
    await screen.findByRole('heading', { name: 'Plan & billing' })
    await user.type(field(), 'GR-ABCD-EFGH{Enter}')
    expect(await within(card()).findByRole('button', { name: 'Redeeming…' })).toBeDisabled()
    fireEvent.submit(field()) // a form can be submitted without its button, and the button is off now
    fireEvent.submit(field())
    answer(new Response(JSON.stringify({ effective: 'pro', source: 'grant', expires_at: null }), { status: 200 }))
    await within(card()).findByRole('status')
    expect(await attempts()).toHaveLength(1)
  })

  it('says the plan has no end when it has none', async () => {
    const user = await open({
      redeem: { status: 200, body: { effective: 'pro', source: 'override', expires_at: null } },
    })
    await user.type(field(), 'GR-ABCD-EFGH')
    await user.click(button())
    expect(await within(card()).findByRole('status')).toHaveTextContent(/^Done. You are on the pro plan.$/)
  })

  it('does nothing when Enter is pressed with an empty box', async () => {
    const user = await open()
    await user.click(field())
    await user.keyboard('{Enter}')
    fireEvent.submit(field()) // and when the form is submitted some other way
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(await attempts()).toEqual([])
  })

  it('is not a way around the limit of 64 characters', async () => {
    const user = await open()
    await user.type(field(), 'G'.repeat(80))
    expect(field()).toHaveValue('G'.repeat(64))
  })
})

describe('a promo code that could not be applied at sign-up', () => {
  it('says so once, above the box, when the address says so', async () => {
    stubApi()
    openApp('/settings/plan?promo=not_applied')
    const notice = (await screen.findByText(/the promo code could not be applied/)).closest('[role="status"]')
    expect(notice).toHaveTextContent('If it still works when those end, enter it below.')
    expect(card()).toBeInTheDocument()
  })

  it('says nothing for any other value', async () => {
    await open()
    stubApi()
    openApp('/settings/plan?promo=yes')
    await screen.findByRole('heading', { name: 'Plan & billing' })
    expect(screen.queryByText(/could not be applied/)).not.toBeInTheDocument()
  })
})
