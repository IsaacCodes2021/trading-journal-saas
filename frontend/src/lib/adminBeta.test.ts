import { describe, expect, it } from 'vitest'
import { person } from '../features/admin/admin-stub'
import { accessText, daysLeft, revokeKind } from './adminBeta'

const NOW = new Date('2026-10-16T09:00:00Z').getTime()

describe('daysLeft', () => {
  it('counts whole days, and a part of a day as a day', () => {
    expect(daysLeft('2026-11-08T09:00:00Z', NOW)).toBe(23)
    expect(daysLeft('2026-10-17T09:00:01Z', NOW)).toBe(2)
    expect(daysLeft('2026-10-17T09:00:00Z', NOW)).toBe(1)
  })

  it('is never below one while the access still runs', () => {
    expect(daysLeft('2026-10-16T09:00:01Z', NOW)).toBe(1)
    expect(daysLeft('2026-10-16T09:00:00Z', NOW)).toBe(1)
  })
})

describe('accessText', () => {
  it.each([
    [{ state: 'active' as const, access_ends_at: '2026-11-08T09:00:00Z' }, 'Active · 23 days left'],
    [{ state: 'active' as const, access_ends_at: '2026-10-17T09:00:00Z' }, 'Active · 1 day left'],
    [{ state: 'active' as const, access_ends_at: null }, 'Active'],
    [{ state: 'waiting' as const, code: 'unused' as const }, 'Waiting · code unused'],
    [{ state: 'waiting' as const, code: 'expired' as const }, 'Waiting · code expired'],
    [{ state: 'waiting' as const, code: 'withdrawn' as const }, 'Waiting · code withdrawn'],
    [{ state: 'ended' as const, access_ends_at: '2026-10-01T09:00:00Z' }, 'Ended · Oct 1, 2026'],
    [{ state: 'revoked' as const, revoked_at: '2026-10-05T10:00:00Z' }, 'Revoked · Oct 5, 2026'],
    [{ state: 'revoked' as const, revoked_at: null }, 'Revoked'],
  ])('says %j as %s', (over, text) => {
    expect(accessText(person(1, 'Alex', over), NOW)).toBe(text)
  })
})

describe('revokeKind', () => {
  it('ends the access of someone whose 30 days run', () => {
    expect(revokeKind(person(1, 'A', { state: 'active' }))).toBe('access')
  })

  it('withdraws the code of someone with no account whose code can still be used', () => {
    expect(revokeKind(person(1, 'A', { state: 'waiting', code: 'unused' }))).toBe('code')
  })

  it.each([
    [{ state: 'waiting' as const, code: 'expired' as const }],
    [{ state: 'waiting' as const, code: 'withdrawn' as const }],
    [{ state: 'ended' as const }],
    [{ state: 'revoked' as const }],
  ])('has nothing to end for %j', (over) => {
    expect(revokeKind(person(1, 'A', over))).toBeNull()
  })
})
