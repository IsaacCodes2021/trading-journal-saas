import { describe, expect, it } from 'vitest'
import { activityText, type Activity, type ActivityKind } from './activity'

const event = (kind: ActivityKind, extra: Partial<Activity> = {}): Activity => ({
  at: '2026-10-04T12:00:00Z',
  kind,
  ended: null,
  mfa: null,
  ...extra,
})

describe('the sentence for each kind of event', () => {
  it.each([
    [event('sign_up'), 'Account created'],
    [event('sign_in'), 'Signed in'],
    [event('sign_in', { mfa: false }), 'Signed in'],
    [event('sign_in', { mfa: true }), 'Signed in with two-step verification'],
    [event('sign_in_refused'), 'A sign-in to your account was refused'],
    [event('sign_out'), 'Signed out'],
    [event('other_sign_out'), 'Signed another place out'],
    [event('sign_out_everywhere'), 'Signed out everywhere else'],
    [event('sign_out_everywhere', { ended: 1 }), 'Signed out of 1 other place'],
    [event('sign_out_everywhere', { ended: 5 }), 'Signed out of 5 other places'],
    [event('sign_out_everywhere', { ended: 0 }), 'Asked to sign out everywhere else (there was nowhere else)'],
    [event('export'), 'Downloaded your data'],
    [event('deletion_requested'), 'Asked to delete the account'],
    [event('deletion_cancelled'), 'Signing in cancelled the account deletion'],
  ])('says %j as %s', (activity, text) => {
    expect(activityText(activity)).toBe(text)
  })

  it('has a distinct sentence for every kind, ending without a full stop so a time can follow', () => {
    const kinds: ActivityKind[] = [
      'sign_up',
      'sign_in',
      'sign_in_refused',
      'sign_out',
      'other_sign_out',
      'sign_out_everywhere',
      'export',
      'deletion_requested',
      'deletion_cancelled',
    ]
    const sentences = kinds.map((kind) => activityText(event(kind)))
    expect(new Set(sentences).size).toBe(kinds.length)
    for (const sentence of sentences) expect(sentence).toMatch(/^[A-Z][^.]*$/)
  })
})
