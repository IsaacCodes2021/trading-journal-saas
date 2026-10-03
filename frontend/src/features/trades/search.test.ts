import { describe, expect, it } from 'vitest'
import { nextSort, parseTradeSearch, pickTradeSearch, toQuery } from './search'

describe('pickTradeSearch', () => {
  it('keeps only valid keys, so a link can name just what it cares about', () => {
    expect(pickTradeSearch({})).toEqual({})
    expect(pickTradeSearch({ q: '  nq ', sort: '-net_pnl', page: '2', per_page: '25', preset: '7d', junk: 1 })).toEqual(
      { q: 'nq', sort: '-net_pnl', page: 2, per_page: 25, preset: '7d' },
    )
  })

  it('drops anything unreadable', () => {
    expect(pickTradeSearch({ q: '   ', sort: 'color', page: '0', per_page: '13' })).toEqual({})
    expect(pickTradeSearch({ sort: '-', page: 'abc', per_page: 1.5 })).toEqual({})
    expect(pickTradeSearch({ page: -3 })).toEqual({})
  })
})

describe('parseTradeSearch', () => {
  it('fills the defaults: newest first, first page, twelve a page, last 30 days', () => {
    expect(parseTradeSearch({})).toEqual({ preset: '30d', sort: '-date', page: 1, per_page: 12 })
  })

  it('lets the URL win', () => {
    const search = parseTradeSearch({ preset: 'all', sort: 'symbol', page: 3, per_page: 50, q: 'apex' })
    expect(search).toMatchObject({ preset: 'all', sort: 'symbol', page: 3, per_page: 50, q: 'apex' })
  })

  it('hands the whole search to the data source as the query', () => {
    const search = parseTradeSearch({ firms: ['Apex'], sort: 'hold' })
    expect(toQuery(search)).toEqual(search)
  })
})

describe('nextSort', () => {
  it('starts date and P&L biggest-first and the other columns ascending', () => {
    expect(nextSort('symbol', 'date')).toBe('-date')
    expect(nextSort('-date', 'net_pnl')).toBe('-net_pnl')
    expect(nextSort('-date', 'symbol')).toBe('symbol')
    expect(nextSort('-date', 'qty')).toBe('qty')
  })

  it('flips the same column each click', () => {
    expect(nextSort('symbol', 'symbol')).toBe('-symbol')
    expect(nextSort('-symbol', 'symbol')).toBe('symbol')
    expect(nextSort('-net_pnl', 'net_pnl')).toBe('net_pnl')
    expect(nextSort('net_pnl', 'net_pnl')).toBe('-net_pnl')
  })
})
