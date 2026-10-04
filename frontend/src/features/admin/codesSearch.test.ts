import { describe, expect, it } from 'vitest'
import { auditQueryOf, pickCodesSearch, searchWithAudit } from './codesSearch'

describe('what the codes and audit address holds', () => {
  it('keeps the valid keys and nothing else', () => {
    expect(
      pickCodesSearch({
        tab: 'codes',
        action: 'admin_suspended',
        actor: 'admin',
        q: 'dana',
        days: 30,
        page: 3,
        junk: 1,
      }),
    ).toEqual({ tab: 'codes', action: 'admin_suspended', actor: 'admin', q: 'dana', days: 30, page: 3 })
  })

  it('keeps nothing for an empty address', () => {
    expect(pickCodesSearch({})).toEqual({})
  })

  it.each(['invites', 'Codes', '', 'codes ', 5, null])('does not take %j as the plan codes tab', (tab) => {
    expect(pickCodesSearch({ tab })).toEqual({})
  })

  it.each(["x'; drop", '../x', 'a b', '', 'x'.repeat(65), 5, null, '<script>'])(
    'refuses %j as an action or an actor',
    (value) => {
      expect(pickCodesSearch({ action: value, actor: value })).toEqual({})
    },
  )

  it.each(['login', 'admin_plan_override_set', 'cli:owner', 'a.b-c'])('accepts %s', (value) => {
    expect(pickCodesSearch({ action: value })).toEqual({ action: value })
  })

  it('trims the search and keeps at most 100 characters', () => {
    expect(pickCodesSearch({ q: '  dana  ' })).toEqual({ q: 'dana' })
    expect(pickCodesSearch({ q: 'x'.repeat(300) })).toEqual({ q: 'x'.repeat(100) })
    expect(pickCodesSearch({ q: '  ' })).toEqual({})
  })

  it.each([30, 90, '30', '90'])('accepts %j days back', (days) => {
    expect(pickCodesSearch({ days })).toEqual({ days: Number(days) })
  })

  it('accepts 0 days back as all of it, and only when it was asked for', () => {
    expect(pickCodesSearch({ days: 0 })).toEqual({ days: 0 })
    expect(pickCodesSearch({ days: '0' })).toEqual({ days: 0 })
    expect(pickCodesSearch({ days: '' })).toEqual({})
    expect(pickCodesSearch({})).toEqual({})
  })

  it.each([5, 7, -1, 91, 1.5, 'x', null])(
    'does not take %j days back (7 is the default, so it is not written)',
    (days) => {
      expect(pickCodesSearch({ days })).toEqual({})
    },
  )

  it.each([1, 0, -2, 1.5, 'x', 100_001, null])('does not take %j as a page to go to', (page) => {
    expect(pickCodesSearch({ page })).toEqual({})
  })

  it('takes a page from 2 up', () => {
    expect(pickCodesSearch({ page: '2' })).toEqual({ page: 2 })
    expect(pickCodesSearch({ page: 100_000 })).toEqual({ page: 100_000 })
  })

  it('turns an address into a log query with the defaults filled in', () => {
    expect(auditQueryOf({})).toEqual({ action: '', actor: '', q: '', days: 7 })
    expect(auditQueryOf({ action: 'login', actor: 'user', q: 'a', days: 0 })).toEqual({
      action: 'login',
      actor: 'user',
      q: 'a',
      days: 0,
    })
  })

  it('writes a query back leaving the defaults out, and keeps the tab and the page', () => {
    expect(searchWithAudit({}, { action: '', actor: '', q: '', days: 7 })).toEqual({})
    expect(searchWithAudit({ tab: 'codes', page: 2 }, { action: 'login', actor: 'admin', q: 'x', days: 90 })).toEqual({
      tab: 'codes',
      page: 2,
      action: 'login',
      actor: 'admin',
      q: 'x',
      days: 90,
    })
    expect(searchWithAudit({}, { action: '', actor: '', q: '', days: 0 })).toEqual({ days: 0 })
  })
})
