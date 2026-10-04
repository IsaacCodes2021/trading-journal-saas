import { describe, expect, it } from 'vitest'
import { STATUS_TEXT, metaText } from './adminCodes'

describe('what an event says besides its kind', () => {
  it('puts the reason first and then the other small facts', () => {
    expect(metaText({ reason: 'abuse', plan: 'pro', ends: '2026-12-31T00:00:00+00:00', replaced: false })).toBe(
      'abuse · plan pro · ends 2026-12-31T00:00:00+00:00',
    )
  })

  it('says a flag that is true as its name, and leaves out one that is false', () => {
    expect(metaText({ for_one_address: true, mfa: false })).toBe('for one address')
  })

  it('leaves out empty values and says nothing for no facts', () => {
    expect(metaText({ reason: '', grant: null, label: '' })).toBe('')
    expect(metaText({})).toBe('')
  })

  it('writes underscores as spaces and numbers as they are', () => {
    expect(metaText({ max_uses: 3, days: 0 })).toBe('max uses 3 · days 0')
  })

  it('spells out the abbreviation the log stores for two-step verification', () => {
    expect(metaText({ mfa: true })).toBe('two-step verification')
    expect(metaText({ mfa: 'password' })).toBe('two-step verification password')
  })

  it('keeps a long line to 140 characters', () => {
    const long = metaText({ reason: 'x'.repeat(300) })
    expect(long).toHaveLength(138)
    expect(long.endsWith('…')).toBe(true)
  })

  it('shows what it is given and nothing hidden', () => {
    expect(metaText({ reason: 'only a reason' })).toBe('only a reason')
  })
})

describe('where a code stands, in words', () => {
  it('has a word for each', () => {
    expect(STATUS_TEXT).toEqual({ works: 'Works', used_up: 'Used up', expired: 'Expired', revoked: 'Revoked' })
  })
})
