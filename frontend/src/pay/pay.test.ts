import { afterEach, describe, expect, it, vi } from 'vitest'
import { MESSAGES, PADDLE_JS, payConfig, startPayment, type PaddleJs } from './pay'

const TOKEN = 'test_' + 'abc123'.repeat(3)
const TXN = 'txn_' + '0'.repeat(24) + '01'

function page(token = TOKEN, environment = 'sandbox'): Document {
  const doc = document.implementation.createHTMLDocument('pay')
  for (const [name, content] of [
    ['tj-paddle-token', token],
    ['tj-paddle-env', environment],
  ]) {
    const meta = doc.createElement('meta')
    meta.name = name as string
    meta.content = content as string
    doc.head.appendChild(meta)
  }
  return doc
}

function fakeWindow(doc: Document, search = `?_ptxn=${TXN}`) {
  return { document: doc, location: { search, origin: 'https://app.example.test' } } as unknown as Window & {
    Paddle?: PaddleJs
  }
}

afterEach(() => vi.restoreAllMocks())

describe('payConfig', () => {
  it('reads the token and environment the server filled in, and the transaction in the address', () => {
    expect(payConfig(page(), `?_ptxn=${TXN}`)).toEqual({ token: TOKEN, environment: 'sandbox', transaction: TXN })
    expect(payConfig(page('live_' + 'x'.repeat(10), 'production'), `?_ptxn=${TXN}`)?.environment).toBe('production')
  })

  it('refuses a page the server did not fill, a token of the other environment, or no real transaction', () => {
    expect(payConfig(page('__TJ_PADDLE_TOKEN__', '__TJ_PADDLE_ENV__'), `?_ptxn=${TXN}`)).toBeNull()
    expect(payConfig(page(TOKEN, 'production'), `?_ptxn=${TXN}`)).toBeNull()
    expect(payConfig(page('live_' + 'x'.repeat(10), 'sandbox'), `?_ptxn=${TXN}`)).toBeNull()
    expect(payConfig(page(), '')).toBeNull()
    expect(payConfig(page(), '?_ptxn=txn_1"><script>')).toBeNull()
    expect(payConfig(page(), '?_ptxn=sub_' + '0'.repeat(26))).toBeNull()
  })
})

describe('startPayment', () => {
  it('says the link is not complete, and loads nothing, when the page cannot be used', () => {
    const doc = page()
    const say = vi.fn()
    startPayment(fakeWindow(doc, ''), say, vi.fn())
    expect(say).toHaveBeenCalledWith(MESSAGES.incomplete)
    expect(doc.querySelector('script')).toBeNull()
  })

  it("loads Paddle.js from Paddle's address only, and hands it the sandbox, the token and the page's settings", () => {
    const doc = page()
    const win = fakeWindow(doc)
    const say = vi.fn()
    const go = vi.fn()
    startPayment(win, say, go)
    const script = doc.querySelector('script')
    expect(script?.src).toBe(PADDLE_JS)
    const paddle = { Environment: { set: vi.fn() }, Initialize: vi.fn() }
    win.Paddle = paddle
    script?.onload?.(new Event('load'))
    expect(paddle.Environment.set).toHaveBeenCalledWith('sandbox')
    const options = paddle.Initialize.mock.calls[0]?.[0]
    expect(options.token).toBe(TOKEN)
    expect(options.checkout.settings).toEqual({
      displayMode: 'overlay',
      theme: 'dark',
      locale: 'en',
      allowedPaymentMethods: ['card'],
      showAddDiscounts: false,
      successUrl: 'https://app.example.test/settings/plan?checkout=done',
    })
    expect(say).toHaveBeenLastCalledWith(MESSAGES.open)
    options.eventCallback({ name: 'checkout.completed' })
    expect(say).toHaveBeenLastCalledWith(MESSAGES.done)
    options.eventCallback({ name: 'checkout.closed' })
    expect(go).toHaveBeenCalledWith('/settings/plan')
  })

  it('never switches to the sandbox for a live token', () => {
    const doc = page('live_' + 'x'.repeat(10), 'production')
    const win = fakeWindow(doc)
    startPayment(win, vi.fn(), vi.fn())
    const paddle = { Environment: { set: vi.fn() }, Initialize: vi.fn() }
    win.Paddle = paddle
    doc.querySelector('script')?.onload?.(new Event('load'))
    expect(paddle.Environment.set).not.toHaveBeenCalled()
    expect(paddle.Initialize).toHaveBeenCalledTimes(1)
  })

  it('says so when Paddle.js cannot be loaded', () => {
    const doc = page()
    const say = vi.fn()
    startPayment(fakeWindow(doc), say, vi.fn())
    doc.querySelector('script')?.onerror?.(new Event('error'))
    expect(say).toHaveBeenLastCalledWith(MESSAGES.unavailable)
    const loadedEmpty = page()
    const again = vi.fn()
    startPayment(fakeWindow(loadedEmpty), again, vi.fn())
    loadedEmpty.querySelector('script')?.onload?.(new Event('load')) // loaded, but no Paddle on the window
    expect(again).toHaveBeenLastCalledWith(MESSAGES.unavailable)
  })
})
