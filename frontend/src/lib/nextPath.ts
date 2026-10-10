/*
 * Where to come back to after signing in. Kept apart from `auth.ts` because the API client needs it, and `auth.ts`
 * itself imports the client.
 */

/** Paths that are the way in, so going "back" to one would only show the way in again. */
const ENTRY_PATHS = /^\/(?:signin|signup|invite)(?:[/?#]|$)/

/**
 * A path on this site worth returning to after sign-in, or null. The server checks `next` again and is the one that
 * decides; this keeps an edited address from being carried around and from sending the person in a circle.
 */
export function safeNext(value: unknown): string | null {
  if (typeof value !== 'string' || value === '' || value.length > 500) return null
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is being refused
  if (/[\u0000-\u001f\u007f]/.test(value)) return null
  if (value.startsWith('/auth/') || ENTRY_PATHS.test(value)) return null
  return value
}

/** Our own sign-in page, with the place to return to when there is one worth keeping (the home page is the default). */
export function signInPage(next?: string): string {
  const kept = safeNext(next)
  return kept && kept !== '/' ? `/signin?next=${encodeURIComponent(kept)}` : '/signin'
}
