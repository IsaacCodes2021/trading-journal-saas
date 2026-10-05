import { go } from './auth'

/*
 * Demo mode: a person who has not imported anything yet can look around the whole app on a made-up journal that lives in
 * their browser tab. It is something they ask for, it is announced on every page (the banner in AppShell), nothing they do
 * in it is sent anywhere or kept, and closing the tab ends it.
 *
 * Only the switch lives here. The sample journal itself (src/lib/demo) is loaded on demand and never reaches a person who
 * did not ask for it; `scripts/check-bundle.mjs` fails the build if it is in anything loaded up front. Keep this file out of
 * `lib/demo/`, so the entry chunk can know the switch without the journal.
 *
 * Entering and leaving are full page loads (`go.to`), so no sample number can linger in a cache or a form, and the app
 * always starts from one known state.
 */

const KEY = 'roundturn-demo'

/** Whether this tab is in demo mode. Storage can be blocked (a private window, site data turned off): that is "no". */
export function isDemo(): boolean {
  try {
    return sessionStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

/** Turns the mode on or off for this tab, and reports whether it took (it cannot without storage). */
function set(on: boolean): boolean {
  try {
    if (on) sessionStorage.setItem(KEY, '1')
    else sessionStorage.removeItem(KEY)
    return isDemo() === on
  } catch {
    return false
  }
}

/** Start looking around on the sample journal, from the dashboard. Does nothing when this tab cannot remember the choice. */
export function enterDemo(): void {
  if (set(true)) go.to('/')
}

/** Back to the person's own data, from the dashboard. */
export function exitDemo(): void {
  set(false)
  go.to('/')
}
