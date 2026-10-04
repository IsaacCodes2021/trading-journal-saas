import { describe, expect, it } from 'vitest'
import { pickAdminSearch, queryOf, searchOf } from './search'

const ID = '11111111-1111-4111-8111-111111111111'

describe('what the operator address holds', () => {
  it('keeps the valid keys and nothing else', () => {
    expect(pickAdminSearch({ q: 'dana', status: 'suspended', page: 3, user: ID, junk: 'x' })).toEqual({
      q: 'dana',
      status: 'suspended',
      page: 3,
      user: ID,
    })
  })

  it('keeps nothing for an empty address', () => {
    expect(pickAdminSearch({})).toEqual({})
  })

  it('trims the search and keeps at most 100 characters', () => {
    expect(pickAdminSearch({ q: '  dana  ' })).toEqual({ q: 'dana' })
    expect(pickAdminSearch({ q: 'x'.repeat(300) })).toEqual({ q: 'x'.repeat(100) })
    expect(pickAdminSearch({ q: '   ' })).toEqual({})
    expect(pickAdminSearch({ q: 5 })).toEqual({})
  })

  it.each(['deleted', '', 'ACTIVE', 'active ', '__proto__', 5, null])('refuses the status %j', (status) => {
    expect(pickAdminSearch({ status })).toEqual({})
  })

  it.each(['active', 'suspended', 'pending_deletion'])('accepts the status %s', (status) => {
    expect(pickAdminSearch({ status })).toEqual({ status })
  })

  it.each([1, 0, -2, 1.5, 'x', '2.5', 100_001, NaN, null, undefined])('does not take %j as a page to go to', (page) => {
    expect(pickAdminSearch({ page })).toEqual({})
  })

  it.each([2, '3', 100_000])('takes %j as a page', (page) => {
    expect(pickAdminSearch({ page })).toEqual({ page: Number(page) })
  })

  it.each(['not-an-id', '', '1', `${ID}x`, `'${ID}`, '../../etc', 5, null])('refuses the account %j', (user) => {
    expect(pickAdminSearch({ user })).toEqual({})
  })

  it('writes an account id in lower case', () => {
    expect(pickAdminSearch({ user: ID.toUpperCase() })).toEqual({ user: ID })
  })

  it('goes between the address and the query without losing anything', () => {
    const search = { q: 'dana', status: 'active', page: 2, user: ID } as const
    const query = queryOf(search)
    expect(query).toEqual({ q: 'dana', status: 'active', page: 2 })
    expect(searchOf(query, ID)).toEqual(search)
  })

  it('leaves the defaults out of the address', () => {
    expect(queryOf({})).toEqual({ q: '', status: '', page: 1 })
    expect(searchOf({ q: '', status: '', page: 1 }, null)).toEqual({})
    expect(searchOf({ q: '', status: '', page: 1 }, ID)).toEqual({ user: ID })
  })
})
