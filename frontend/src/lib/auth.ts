import { csrfHeader } from './api/client'

/*
 * Where the browser goes to sign in or sign up. Both are full-page navigations to the server (`/auth/…`), never calls
 * from the app: the identity provider's pages are not ours, and the session cookie is set by the server's reply.
 */

/** Where the browser goes next. A function of its own so a test can watch it, as the page cannot leave in a test. */
export const go = {
  to: (url: string) => window.location.assign(url),
}

/**
 * What the server puts after `/invite?error=` when a sign-in did not complete (`ERR_*` in `auth/flow.py`; a backend test
 * checks the two lists match). The server gives one short code and never the reason, so the words are chosen here:
 * a bad invite is always one answer, whether it was wrong, used up, expired or meant for someone else.
 */
export const SIGN_IN_ERRORS = {
  expired: 'That sign-in took too long, or was started in a different browser. Please try again.',
  signin_failed: 'We could not sign you in. Please try again.',
  email_unverified:
    'Your email address is not verified yet. Look for the message from the sign-in service, confirm it, and try again.',
  invite_invalid:
    'That invite code did not work. It may be wrong, already used, expired, or meant for a different email address.',
  account_unavailable:
    'This account is not available right now. If you think that is a mistake, email hello@roundturn.app.',
  email_taken:
    'That email address already belongs to an account that signs in another way. Sign in the way you did before, or email hello@roundturn.app.',
} as const

export type SignInErrorCode = keyof typeof SIGN_IN_ERRORS

/** The code if it is one the server uses; anything else (a mistyped or edited address) is ignored, never shown. */
export function signInErrorOf(value: unknown): SignInErrorCode | null {
  return typeof value === 'string' && Object.hasOwn(SIGN_IN_ERRORS, value) ? (value as SignInErrorCode) : null
}

/**
 * Where to go after signing out: the identity provider's logout page (https) or a path on this site, as the server
 * says. Anything else (a script address, another scheme, a protocol-relative address) is not followed.
 */
export function logoutTarget(value: unknown): string {
  if (typeof value !== 'string') return '/invite'
  if (value.startsWith('https://')) return value
  if (value.startsWith('/') && !value.startsWith('//') && !value.includes('\\')) return value
  return '/invite'
}

/**
 * End the session and say where to go. A session that is already over (401) counts as signed out; it must not be sent
 * through sign-in, which would quietly sign the person back in. Throws when the server could not end it.
 */
export async function signOut(): Promise<string> {
  // Not through the API client: its 401 handling would start a sign-in.
  const response = await fetch('/auth/logout', { method: 'POST', headers: csrfHeader() })
  if (response.status === 401) return '/invite'
  if (!response.ok) throw new Error('The server could not sign you out.')
  const body: unknown = await response.json().catch(() => null)
  return logoutTarget(typeof body === 'object' && body !== null ? (body as { redirect?: unknown }).redirect : null)
}

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
