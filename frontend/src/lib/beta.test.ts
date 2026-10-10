import { afterEach, describe, expect, it, vi } from 'vitest'
import { joinProblems, joinTheBeta } from './beta'

afterEach(() => vi.unstubAllGlobals())

const GOOD = { firstName: 'Alex', email: 'alex@example.com', propFirm: 'Topstep', website: '' }

describe('joinProblems', () => {
  it('finds nothing wrong with a good form', () => {
    expect(joinProblems(GOOD)).toEqual({})
  })

  it('trims before it checks, so blanks are empty', () => {
    expect(joinProblems({ ...GOOD, firstName: '   ', propFirm: '\t' })).toEqual({
      firstName: 'Enter your first name.',
      propFirm: 'Enter a firm, or "none" if you do not use one yet.',
    })
  })

  it('allows 80 characters and refuses 81', () => {
    expect(joinProblems({ ...GOOD, firstName: 'a'.repeat(80), propFirm: 'b'.repeat(80) })).toEqual({})
    expect(joinProblems({ ...GOOD, firstName: 'a'.repeat(81) })).toEqual({ firstName: 'Use 80 characters or fewer.' })
    expect(joinProblems({ ...GOOD, propFirm: 'b'.repeat(81) })).toEqual({ propFirm: 'Use 80 characters or fewer.' })
  })

  it('allows an address of 254 characters and refuses 255', () => {
    const local = (n: number) => 'a'.repeat(n - '@example.com'.length) + '@example.com'
    expect(joinProblems({ ...GOOD, email: local(254) })).toEqual({})
    expect(joinProblems({ ...GOOD, email: local(255) })).toEqual({ email: 'That does not look like an email address.' })
  })

  it('does not look at the hidden field', () => {
    expect(joinProblems({ ...GOOD, website: 'x'.repeat(500) })).toEqual({})
  })
})

describe('joinTheBeta', () => {
  it('sends the form, trimmed, and says it worked on a 202', async () => {
    const fetched = vi.fn(async () => new Response('{"status":"ok"}', { status: 202 }))
    vi.stubGlobal('fetch', fetched)
    const result = await joinTheBeta({
      ...GOOD,
      firstName: ' Alex ',
      email: ' alex@example.com ',
      propFirm: ' Topstep ',
    })
    expect(result).toEqual({ ok: true })
    const call = fetched.mock.calls[0] as unknown as [string, RequestInit]
    expect(call[0]).toBe('/api/v1/beta/join')
    expect(JSON.parse(String(call[1].body))).toEqual({
      first_name: 'Alex',
      email: 'alex@example.com',
      prop_firm: 'Topstep',
      website: '',
    })
  })

  it.each([
    [429, /Too many tries/],
    [503, /not open yet/],
    [422, /not accepted/],
    [500, /Something went wrong/],
  ])('turns a %i into a plain sentence', async (status, words) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"internal"}', { status })),
    )
    const result = await joinTheBeta(GOOD)
    expect(result.ok).toBe(false)
    expect(result.ok === false && result.message).toMatch(words)
    expect(result.ok === false && result.message).not.toContain('internal')
  })

  it('turns a network failure into a plain sentence', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const result = await joinTheBeta(GOOD)
    expect(result).toEqual({ ok: false, message: 'Could not reach the server. Check your connection and try again.' })
  })
})
