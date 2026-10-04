import { describe, expect, it } from 'vitest'
import { actionText, formatBytes, stepUpUrl, utcDay, utcMoment } from './admin'

describe('where to confirm a second factor', () => {
  it('asks the server for a step-up and says where to come back to', () => {
    expect(stepUpUrl('/admin')).toBe('/auth/login?stepup=1&next=%2Fadmin')
    expect(stepUpUrl('/admin?user=1&q=a b')).toBe('/auth/login?stepup=1&next=%2Fadmin%3Fuser%3D1%26q%3Da%20b')
  })
})

describe('dates an operator reads', () => {
  it('are in UTC, the same as the server logs them', () => {
    expect(utcDay('2026-10-04T03:20:00Z')).toBe('Oct 4, 2026')
    expect(utcDay('2026-10-04T23:30:00-05:00')).toBe('Oct 5, 2026')
    expect(utcMoment('2026-10-04T03:20:00Z')).toBe('Oct 4, 2026 · 03:20 UTC')
    expect(utcMoment('2026-12-31T23:59:59Z')).toBe('Dec 31, 2026 · 23:59 UTC')
  })
})

describe('sizes', () => {
  it.each([
    [0, '0 B'],
    [1023, '1023 B'],
    [1024, '1.0 KB'],
    [1536, '1.5 KB'],
    [38 * 1024 * 1024, '38 MB'],
    [5.5 * 1024 * 1024, '5.5 MB'],
    [2 * 1024 ** 3, '2.0 GB'],
    [3 * 1024 ** 4, '3.0 TB'],
    [5000 * 1024 ** 4, '5000 TB'],
  ])('%d bytes is %s', (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text)
  })
})

describe('the words for what the log says', () => {
  it('has a sentence for each thing an operator does', () => {
    for (const action of [
      'admin_viewed_user',
      'admin_suspended',
      'admin_unsuspended',
      'admin_sessions_revoked',
      'admin_deletion_scheduled',
      'admin_deletion_cancelled',
      'admin_plan_override_set',
      'admin_plan_override_revoked',
    ]) {
      expect(actionText(action)).not.toBe(action.replaceAll('_', ' '))
    }
  })

  it('shows a word it does not know as written, readable', () => {
    expect(actionText('something_new')).toBe('something new')
    expect(actionText('')).toBe('')
  })

  it('knows the person’s own events too', () => {
    expect(actionText('login')).toBe('Signed in')
    expect(actionText('export')).toBe('Downloaded their data')
  })
})
