import { describe, expect, it } from 'vitest'
import { safeNext, signInPage } from './nextPath'

describe('safeNext', () => {
  it.each(['/', '/trades', '/trades?preset=7d&sort=-pnl', '/day/2026-10-09', '/settings/privacy?delete=1'])(
    'keeps %s',
    (value) => expect(safeNext(value)).toBe(value),
  )

  it.each([
    ['nothing', undefined],
    ['a number', 7],
    ['an empty string', ''],
    ['another site', 'https://evil.test/'],
    ['a protocol-relative address', '//evil.test/x'],
    ['a relative path', 'trades'],
    ['a backslash', '/a\\b'],
    ['a line break', '/a\r\nSet-Cookie: x=1'],
    ['a control character', '/a\u0000b'],
    ['the sign-in routes', '/auth/login?next=%2F'],
    ['the sign-in page', '/signin'],
    ['the sign-up page', '/signup?code=TJ-7KQ2-M9XA'],
    ['the invite page', '/invite?error=expired'],
    ['too long', '/' + 'a'.repeat(500)],
  ])('refuses %s', (_name, value) => expect(safeNext(value)).toBeNull())

  it('does not mistake a path that merely starts with the same letters for the way in', () => {
    expect(safeNext('/signings')).toBe('/signings')
    expect(safeNext('/invitees')).toBe('/invitees')
  })
})

describe('signInPage', () => {
  it('is our own page, with no parameter when there is nowhere special to return to', () => {
    expect(signInPage()).toBe('/signin')
    expect(signInPage('/')).toBe('/signin')
    expect(signInPage('https://evil.test/')).toBe('/signin')
    expect(signInPage('/signin')).toBe('/signin')
  })

  it('keeps the place the person was going, encoded', () => {
    expect(signInPage('/trades?preset=7d')).toBe('/signin?next=%2Ftrades%3Fpreset%3D7d')
  })
})
