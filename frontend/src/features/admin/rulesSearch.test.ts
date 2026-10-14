import { describe, expect, it } from 'vitest'
import { pickRulesSearch, rulesQueryOf, rulesSearchOf } from './rulesSearch'

describe('what the firm rules address holds', () => {
  it('keeps the valid keys and nothing else', () => {
    expect(pickRulesSearch({ q: 'apex 50k', page: 3, template: 42, junk: 'x' })).toEqual({
      q: 'apex 50k',
      page: 3,
      template: 42,
    })
    expect(pickRulesSearch({})).toEqual({})
  })

  it('trims the search and keeps at most 100 characters', () => {
    expect(pickRulesSearch({ q: '  lucid  ' })).toEqual({ q: 'lucid' })
    expect(pickRulesSearch({ q: 'x'.repeat(300) })).toEqual({ q: 'x'.repeat(100) })
    expect(pickRulesSearch({ q: '   ' })).toEqual({})
    expect(pickRulesSearch({ q: 5 })).toEqual({})
  })

  it.each([1, 0, -2, 1.5, 'x', '', 100_001, NaN, null, true])('does not take %j as a page to go to', (page) => {
    expect(pickRulesSearch({ page })).toEqual({})
  })

  it.each([0, -1, 2.5, 'abc', '', null, true, [3], Number.MAX_SAFE_INTEGER + 2])(
    'does not take %j as a template',
    (template) => {
      expect(pickRulesSearch({ template })).toEqual({})
    },
  )

  it.each([1, '7', 371])('takes %j as a template', (template) => {
    expect(pickRulesSearch({ template })).toEqual({ template: Number(template) })
  })

  it('leaves the defaults out of the address, and reads them back', () => {
    expect(rulesSearchOf({ q: '', page: 1 }, null)).toEqual({})
    expect(rulesSearchOf({ q: 'topstep', page: 2 }, 9)).toEqual({ q: 'topstep', page: 2, template: 9 })
    expect(rulesQueryOf({})).toEqual({ q: '', page: 1 })
    expect(rulesQueryOf({ q: 'topstep', page: 2, template: 9 })).toEqual({ q: 'topstep', page: 2 })
  })
})
