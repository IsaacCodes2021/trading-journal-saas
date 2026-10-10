import { describe, expect, it } from 'vitest'
import { betaQueryOf, betaSearchOf, pickBetaSearch } from './betaSearch'

describe('pickBetaSearch', () => {
  it('keeps what is valid', () => {
    expect(pickBetaSearch({ q: ' apex ', state: 'waiting', page: '3', person: '12' })).toEqual({
      q: 'apex',
      state: 'waiting',
      page: 3,
      person: 12,
    })
  })

  it.each([
    ['an unknown state', { state: 'deleted' }],
    ['a state that is not text', { state: 4 }],
    ['page 1, which is the default', { page: 1 }],
    ['a page that is not a number', { page: 'x' }],
    ['a negative page', { page: -2 }],
    ['a page that is not whole', { page: 2.5 }],
    ['a page that is absurdly large', { page: 1_000_000 }],
    ['a person that is not a number', { person: 'abc' }],
    ['a person of zero', { person: 0 }],
    ['a negative person', { person: -4 }],
    ['a person past what a number in the database holds', { person: 3_000_000_000 }],
    ['an empty search', { q: '   ' }],
  ])('drops %s', (_name, raw) => {
    expect(pickBetaSearch(raw)).toEqual({})
  })

  it('cuts the search to 100 characters', () => {
    expect(pickBetaSearch({ q: 'x'.repeat(300) }).q).toHaveLength(100)
  })
})

describe('the query and the address', () => {
  it('apply the defaults, and leave them out again', () => {
    expect(betaQueryOf({})).toEqual({ q: '', state: '', page: 1 })
    expect(betaSearchOf({ q: '', state: '', page: 1 }, null)).toEqual({})
  })

  it('round-trip a full view', () => {
    const search = { q: 'apex', state: 'ended' as const, page: 2, person: 7 }
    expect(betaSearchOf(betaQueryOf(search), 7)).toEqual(search)
  })
})
