/*
 * The payment page (`/billing/pay`, pay.html): the only page where Paddle's script runs (docs/design/paddle-integration.md
 * section 6). It loads Paddle.js from Paddle's address, as Paddle requires; Paddle.js then opens its checkout for the
 * transaction named in the address (`?_ptxn=txn_...`), which the server made for the person signed in. Nothing here
 * decides a price, a customer or a plan: the payment is Paddle's, and access changes only when Paddle tells the server.
 */

export const PADDLE_JS = 'https://cdn.paddle.com/paddle/v2/paddle.js'

/** The three calls this page makes, typed by hand (the `@paddle/paddle-js` package would be an install). */
export interface PaddleJs {
  Environment: { set(environment: 'sandbox'): void }
  Initialize(options: {
    token: string
    eventCallback?: (event: { name?: string }) => void
    checkout?: { settings: Record<string, unknown> }
  }): void
}

export interface PayConfig {
  token: string
  environment: 'sandbox' | 'production'
  transaction: string
}

const TRANSACTION = /^txn_[a-z0-9]{10,64}$/
const TOKEN = /^(?:test|live)_[A-Za-z0-9_]{1,200}$/

/** What the server put in the page and the transaction in the address, or null when either is missing or not real. */
export function payConfig(doc: Document, search: string): PayConfig | null {
  const meta = (name: string) => doc.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)?.content ?? ''
  const token = meta('tj-paddle-token')
  const environment = meta('tj-paddle-env')
  const transaction = new URLSearchParams(search).get('_ptxn') ?? ''
  if (!TOKEN.test(token) || !TRANSACTION.test(transaction)) return null
  if (environment !== 'sandbox' && environment !== 'production') return null
  if ((environment === 'sandbox') !== token.startsWith('test_')) return null // a token from the other environment
  return { token, environment, transaction }
}

export const MESSAGES = {
  incomplete: 'This payment link is not complete. Go back to Plan & billing and press Subscribe again.',
  unavailable: 'The payment form could not be loaded. Check your connection, then try again in a minute.',
  open: 'The secure payment form is open. If you closed it, go back to Plan & billing and press Subscribe again.',
  done: 'Payment received. Taking you back to Plan & billing…',
}

/**
 * Load Paddle.js and hand it the page's settings: an overlay, dark, in English, card only for now (the Paddle guide's
 * question 5), and back to Plan & billing when it is paid (`?checkout=done`, where the page waits for Paddle's word) or
 * closed. `say` shows a sentence in the page; `go` leaves it.
 */
export function startPayment(
  win: Window & { Paddle?: PaddleJs },
  say: (text: string) => void,
  go: (url: string) => void,
): void {
  const config = payConfig(win.document, win.location.search)
  if (!config) {
    say(MESSAGES.incomplete)
    return
  }
  const script = win.document.createElement('script')
  script.src = PADDLE_JS
  script.async = true
  script.onerror = () => say(MESSAGES.unavailable)
  script.onload = () => {
    const paddle = win.Paddle
    if (!paddle) {
      say(MESSAGES.unavailable)
      return
    }
    if (config.environment === 'sandbox') paddle.Environment.set('sandbox')
    paddle.Initialize({
      token: config.token,
      eventCallback: (event) => {
        if (event.name === 'checkout.completed') say(MESSAGES.done)
        if (event.name === 'checkout.closed') go('/settings/plan')
      },
      checkout: {
        settings: {
          displayMode: 'overlay',
          theme: 'dark',
          locale: 'en',
          allowedPaymentMethods: ['card'],
          showAddDiscounts: false, // a code is typed in the app and saved there; the checkout carries it already
          successUrl: `${win.location.origin}/settings/plan?checkout=done`,
        },
      },
    })
    say(MESSAGES.open)
  }
  win.document.head.appendChild(script)
}
