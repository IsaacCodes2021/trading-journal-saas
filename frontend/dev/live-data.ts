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
