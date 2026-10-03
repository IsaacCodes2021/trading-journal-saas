/*
 * Where the browser goes to sign in or sign up. Both are full-page navigations to the server (`/auth/…`), never calls
 * from the app: the identity provider's pages are not ours, and the session cookie is set by the server's reply.
 */

/** "tj 7kq2-m9xa" to "TJ-7KQ2-M9XA": trimmed, capitals, and spaces or underscores as dashes. */
export function normalizeInviteCode(text: string): string {
  return text
    .trim()
    .toUpperCase()
    .replace(/[\s_]+/g, '-')
}

/** Letters and digits in groups of two or more joined by dashes, like TJ-7KQ2-M9XA. Whether a code is real is the server's call. */
export function looksLikeInviteCode(code: string): boolean {
  return code.length >= 6 && code.length <= 40 && /^[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+$/.test(code)
}

export const EXAMPLE_INVITE = 'TJ-7KQ2-M9XA'

export function signUpUrl(code: string): string {
  return `/auth/login?invite=${encodeURIComponent(code)}`
}

export function signInUrl(next = '/'): string {
  return `/auth/login?next=${encodeURIComponent(next)}`
}

/** The marketing site, where the waitlist and the legal pages live. */
export const SITE = 'https://roundturn.app'
