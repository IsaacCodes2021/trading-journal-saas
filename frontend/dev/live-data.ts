/**
 * Whether pages read the real API (`apiSource`) or the sample journal (`demoSource`); vite.config.ts bakes the answer
 * into the bundle as `__TJ_LIVE_DATA__`.
 *
 * - A **build** is always live. What it ships to is signed in (a request without a session is sent to sign-in), so there
 *   is never a reason to show anyone made-up numbers there.
 * - `pnpm dev` is live only against a real API (`TJ_DEV_API=real`); otherwise it keeps the sample journal and the
 *   development session stub, so the screens can be tried with no backend running.
 * - **Tests are never live**, whatever the environment says: they wrap the tree in their own data source.
 */
export function liveData(command: 'build' | 'serve', mode: string, realApi: boolean): boolean {
  if (mode === 'test') return false
  return command === 'build' || realApi
}

/**
 * What `pnpm dev` talks to, from `TJ_DEV_API`:
 * - unset: no backend; the sample journal and a stub session (`dev/session-stub.ts`)
 * - `real`: the API started with `python -m tradejournal.api.devserver`, which signs in as one development user; this
 *   dev server sends that user's fixed session cookie for the browser
 * - `signin`: the API started with `--real-sign-in`, where people sign in through Kinde and get real sessions; no cookie
 *   is added (adding one would replace the real session), and the API's `/auth` routes are proxied so the redirects work
 */
export type DevApi = 'stub' | 'dev-user' | 'signin'

export function devApi(value: string | undefined): DevApi {
  if (value === 'real') return 'dev-user'
  if (value === 'signin') return 'signin'
  return 'stub'
}
