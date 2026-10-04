import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './api/client'
import {
  SIGN_IN_ERRORS,
  deletionDay,
  logoutTarget,
  looksLikeInviteCode,
  normalizeInviteCode,
  reauthUrl,
  signInErrorOf,
  signInUrl,
  signOut,
  signUpUrl,
} from './auth'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('normalizeInviteCode', () => {
  it('trims, capitalizes, and turns spaces and underscores into dashes', () => {
    expect(normalizeInviteCode('  tj-7kq2-m9xa ')).toBe('TJ-7KQ2-M9XA')
    expect(normalizeInviteCode('tj 7kq2  m9xa')).toBe('TJ-7KQ2-M9XA')
    expect(normalizeInviteCode('tj_7kq2_m9xa')).toBe('TJ-7KQ2-M9XA')
  })
})

describe('looksLikeInviteCode', () => {
  it.each(['TJ-7KQ2-M9XA', 'AB-CDE', 'BETA-2026-ABCD-1234'])('accepts %s', (code) => {
    expect(looksLikeInviteCode(code)).toBe(true)
  })

  it.each([
    '',
    'TJ',
    'TJ7KQ2M9XA',
    'TJ--7KQ2',
    '-TJ-7KQ2',
    'TJ-7KQ2-',
    'tj-7kq2-m9xa',
    'TJ-7KQ2-M9X!',
    'A-B',
    'X'.repeat(41),
  ])('refuses %j (the server decides whether a code is real, this only checks its shape)', (code) => {
    expect(looksLikeInviteCode(code)).toBe(false)
  })
})

describe('the addresses the browser goes to', () => {
  it('sends the code to the server, encoded', () => {
    expect(signUpUrl('TJ-7KQ2-M9XA')).toBe('/auth/login?invite=TJ-7KQ2-M9XA')
    expect(signUpUrl('A&B=C')).toBe('/auth/login?invite=A%26B%3DC')
  })

  it('remembers where to return to after signing in', () => {
    expect(signInUrl()).toBe('/auth/login?next=%2F')
    expect(signInUrl('/trades?preset=7d')).toBe('/auth/login?next=%2Ftrades%3Fpreset%3D7d')
  })
})

describe('signing in again on purpose', () => {
  it('asks the server to make the provider check again, and to come back to the page it was asked from', () => {
    expect(reauthUrl('/settings/privacy?delete=1')).toBe('/auth/login?reauth=1&next=%2Fsettings%2Fprivacy%3Fdelete%3D1')
    expect(reauthUrl('/x&reauth=0')).toBe('/auth/login?reauth=1&next=%2Fx%26reauth%3D0')
  })
})

describe('the day an account will be deleted', () => {
  it.each([
    ['2026-10-11T03:20:00Z', 'Oct 11, 2026'],
    ['2026-10-11T03:20:00.123456Z', 'Oct 11, 2026'],
    ['2026-10-11T03:20:00+00:00', 'Oct 11, 2026'],
    ['2026-10-11T03:20Z', 'Oct 11, 2026'],
    ['2026-12-31T23:59:59Z', 'Dec 31, 2026'],
    ['2027-01-01T00:00:00Z', 'Jan 1, 2027'],
  ])('is %s on %s, in UTC whatever the browser’s zone', (value, day) => {
    expect(deletionDay(value)).toBe(day)
  })

  it('is a day in UTC, not in the offset it came with', () => {
    expect(deletionDay('2026-10-11T23:30:00-05:00')).toBe('Oct 12, 2026')
  })

  it.each([
    undefined,
    '',
    'tomorrow',
    '2026-10-11',
    '2026-10-11 03:20:00Z',
    '2026-10-11T03:20:00',
    '2026-13-45T03:20:00Z',
    '2026-10-11T03:20:00Z<script>',
    '2026-10-11T03:20:00Zjavascript:alert(1)',
    '<b>2026-10-11T03:20:00Z</b>',
    '12026-10-11T03:20:00Z',
  ])('shows nothing for %j', (value) => {
    expect(deletionDay(value)).toBeNull()
  })
})

describe('the sign-in error codes', () => {
  it('knows the seven codes the server uses, each with a sentence of its own', () => {
    // The same list as ERR_* in backend/tradejournal/auth/flow.py (a backend test compares the two).
    expect(Object.keys(SIGN_IN_ERRORS).sort()).toEqual([
      'account_unavailable',
      'email_taken',
      'email_unverified',
      'expired',
      'invite_invalid',
      'reauth_failed',
      'signin_failed',
    ])
    const sentences = Object.values(SIGN_IN_ERRORS)
    expect(new Set(sentences).size).toBe(sentences.length)
    for (const sentence of sentences) expect(sentence).toMatch(/^[A-Z].*[.]$/)
  })

  it('never says why an invite was refused, only the ways it can go wrong', () => {
    expect(SIGN_IN_ERRORS.invite_invalid).toContain('wrong, already used, expired, or meant for a different email')
  })

  it.each(Object.keys(SIGN_IN_ERRORS))('accepts %s', (code) => {
    expect(signInErrorOf(code)).toBe(code)
  })

  it.each([
    undefined,
    null,
    42,
    ['expired'],
    {},
    '',
    'EXPIRED',
    'expired ',
    '__proto__',
    'constructor',
    'toString',
    '<script>',
  ])('ignores %j', (value) => {
    expect(signInErrorOf(value)).toBeNull()
  })
})

describe('where to go after signing out', () => {
  it.each([['https://roundturn.kinde.com/logout?redirect=https%3A%2F%2Fapp.example.com%2F'], ['/'], ['/invite']])(
    'follows %s',
    (target) => {
      expect(logoutTarget(target)).toBe(target)
    },
  )

  it.each([
    undefined,
    null,
    '',
    42,
    'javascript:alert(1)',
    'data:text/html,hi',
    'http://insecure.example/logout',
    '//evil.example/x',
    '/\\evil.example',
    'relative/path',
  ])('does not follow %j, and goes to the sign-in page instead', (target) => {
    expect(logoutTarget(target)).toBe('/invite')
  })
})

describe('signOut', () => {
  afterEach(() => vi.unstubAllGlobals())

  const answer = (response: () => Response | Promise<Response>) => {
    const fetched = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => response())
    vi.stubGlobal('fetch', fetched)
    return fetched
  }

  it('posts with the CSRF token and returns where the server says to go', async () => {
    setCsrfToken('tok-1')
    const fetched = answer(() => json({ redirect: 'https://idp.example/logout' }))
    await expect(signOut()).resolves.toBe('https://idp.example/logout')
    expect(fetched).toHaveBeenCalledExactlyOnceWith('/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': 'tok-1' },
    })
  })

  it('treats a session that is already over as signed out, without starting a sign-in', async () => {
    answer(() => json({ title: 'Unauthorized', status: 401 }, 401))
    await expect(signOut()).resolves.toBe('/invite')
  })

  it('refuses to follow a redirect that is not a page: an unreadable answer or a script address', async () => {
    answer(() => new Response('not json', { status: 200 }))
    await expect(signOut()).resolves.toBe('/invite')
    answer(() => json({ redirect: 'javascript:alert(1)' }))
    await expect(signOut()).resolves.toBe('/invite')
  })

  it('throws when the server could not end the session, so the page can say so', async () => {
    answer(() => json({ title: 'Internal Server Error', status: 500 }, 500))
    await expect(signOut()).rejects.toThrow('could not sign you out')
    answer(() => json({ title: 'Forbidden', status: 403 }, 403))
    await expect(signOut()).rejects.toThrow()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('offline'))),
    )
    await expect(signOut()).rejects.toThrow('offline')
  })
})
