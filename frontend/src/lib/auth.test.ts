import { describe, expect, it } from 'vitest'
import { looksLikeInviteCode, normalizeInviteCode, signInUrl, signUpUrl } from './auth'

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
