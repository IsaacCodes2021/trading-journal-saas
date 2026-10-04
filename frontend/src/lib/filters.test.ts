import { describe, expect, it } from 'vitest'
import {
  DEFAULT_PRESET,
  filtersOf,
  hasNarrowingFilters,
  parseFilters,
  pickFilters,
  summarizeSelection,
} from './filters'

describe('pickFilters', () => {
  it('keeps valid keys and nothing else, with no default filled in', () => {
    expect(pickFilters({})).toEqual({})
    expect(pickFilters({ preset: '7d', page: 3, junk: 'x' })).toEqual({ preset: '7d' })
  })

  it('drops values it does not know instead of failing', () => {
    expect(pickFilters({ preset: 'forever', account_type: 'demo', direction: 'sideways', firms: 42 })).toEqual({})
  })

  it('reads show_archived as on only when it is exactly on', () => {
    expect(pickFilters({ show_archived: true })).toEqual({ show_archived: true })
    expect(pickFilters({ show_archived: 'true' })).toEqual({ show_archived: true })
    expect(pickFilters({ show_archived: false })).toEqual({})
    expect(pickFilters({ show_archived: 'false' })).toEqual({})
    expect(pickFilters({ show_archived: 1 })).toEqual({})
  })

  it('reads one or many firms in any of the shapes a URL produces', () => {
    expect(pickFilters({ firms: 'Apex' })).toEqual({ firms: ['Apex'] })
    expect(pickFilters({ firms: ['Apex', 'Lucid', 'Apex', ' ', 7] })).toEqual({ firms: ['Apex', 'Lucid'] })
    expect(pickFilters({ firms: '' })).toEqual({})
  })
})

describe('parseFilters', () => {
  it('defaults the range to the last 30 days', () => {
    expect(DEFAULT_PRESET).toBe('30d')
    expect(parseFilters({})).toEqual({ preset: '30d' })
  })

  it('lets the URL win', () => {
    expect(parseFilters({ preset: 'ytd', direction: 'long', symbol_roots: ['NQ'] })).toEqual({
      preset: 'ytd',
      direction: 'long',
      symbol_roots: ['NQ'],
    })
  })

  it('keeps only filters when carried between pages', () => {
    expect(filtersOf({ preset: '90d', firms: ['Apex'] })).toEqual({ preset: '90d', firms: ['Apex'] })
    expect(filtersOf({ preset: '7d', q: 'nq', page: 4 } as never)).toEqual({ preset: '7d' })
  })

  it('knows when a narrowing filter is set (the range alone is not one)', () => {
    expect(hasNarrowingFilters({ preset: 'all' })).toBe(false)
    expect(hasNarrowingFilters({ preset: 'all', direction: 'short' })).toBe(true)
  })
})

describe('summarizeSelection', () => {
  it('names one or two, counts more, and falls back to the all-label', () => {
    expect(summarizeSelection(undefined, 'All firms', 'firms')).toBe('All firms')
    expect(summarizeSelection([], 'All firms', 'firms')).toBe('All firms')
    expect(summarizeSelection(['NQ'], 'All symbols', 'symbols')).toBe('NQ')
    expect(summarizeSelection(['Apex', 'Lucid'], 'All firms', 'firms')).toBe('Apex, Lucid')
    expect(summarizeSelection(['A', 'B', 'C'], 'All firms', 'firms')).toBe('3 firms')
  })
})
