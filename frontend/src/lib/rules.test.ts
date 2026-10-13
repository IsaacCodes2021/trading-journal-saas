import { describe, expect, it } from 'vitest'
import type { RuleField } from './data/model'
import { checkRules, coerceRule, netOf, readRuleForm, warningOf } from './rules'

const field = (name: string, kind: RuleField['kind'], label: string, choices: string[] = []): RuleField => ({
  name,
  kind,
  group: 'Drawdown',
  label,
  choices,
  hint: '',
})

const MAX_LOSS = field('max_loss_usd', 'money', 'Max loss')
const PCT = field('consistency_pct', 'pct', 'Consistency %')
const DAYS = field('min_trading_days', 'int', 'Minimum trading days')
const PHASE = field('phase', 'choice', 'Phase', ['evaluation', 'funded', 'live'])
const FROM = field('effective_from', 'date', 'Effective from')

const NAMED = { firm: 'X', program: 'Y', account_size: '50K', phase: 'funded', rule_version: 'v1' }

describe('typing a rule (as prop_rules.coerce)', () => {
  it.each([
    [MAX_LOSS, '2000', '2000.00'],
    [MAX_LOSS, '2,000.5', '2000.50'],
    [MAX_LOSS, '', null],
    [PCT, '33.333', '33.33'],
    [DAYS, '5', 5],
    [PHASE, 'live', 'live'],
    [FROM, '2026-03-01', '2026-03-01'],
  ] as const)('%s.name %j is %j', (f, value, typed) => {
    expect(coerceRule(f, value)).toEqual(typed)
  })

  it.each([
    [MAX_LOSS, '-5', 'Max loss cannot be negative.'],
    [MAX_LOSS, 'up to 1500', 'Max loss must be a number.'],
    [PCT, '0', 'Consistency % must be more than 0 and at most 100.'],
    [PCT, '100.5', 'Consistency % must be more than 0 and at most 100.'],
    [DAYS, '-1', 'Minimum trading days must be a whole number, 0 or more.'],
    [PHASE, 'sim', 'Phase must be one of: evaluation, funded, live.'],
    [FROM, '2026-02-30x', 'Effective from must be a date (YYYY-MM-DD).'],
  ] as const)('refuses %s.name %j in the server’s words', (f, value, message) => {
    expect(() => coerceRule(f, value)).toThrow(message)
  })
})

describe('what blocks saving (as prop_rules.check)', () => {
  it.each([
    [{}, []],
    [{ drawdown_type: 'static' }, ['max_loss_usd']],
    [{ daily_loss_type: 'hard' }, ['daily_loss_limit_usd']],
    [{ daily_loss_type: 'optional' }, []], // optional with no amount: not bought
    [{ consistency_basis: 'best_day_vs_total_profit' }, ['consistency_pct']],
    [{ consistency_pct: '40.00' }, ['consistency_basis']],
    [{ consistency_pct: '40.00', consistency_basis: 'none' }, ['consistency_basis']],
    [{ payout_buffer_usd: '1000.00', max_loss_usd: '2000.00' }, ['payout_buffer_usd']],
    [{ payout_buffer_usd: '0.00', max_loss_usd: '2000.00' }, []], // no buffer at all is a real rule (TradeDay)
    [{ qualifying_day_min_profit_usd: '150.00' }, ['qualifying_days_required']],
    [{ firm: null }, ['firm']],
  ])('%j blocks %j', (values, errors) => {
    expect(Object.keys(checkRules({ ...NAMED, ...values })).sort()).toEqual(errors)
  })

  it('reads a whole form: a value of the wrong kind says so, not that something is missing', () => {
    const fields = [
      field('firm', 'text', 'Firm'),
      MAX_LOSS,
      field('drawdown_type', 'choice', 'Drawdown type', ['static']),
    ]
    const { values, errors } = readRuleForm(fields, { firm: 'Apex', max_loss_usd: '-5', drawdown_type: 'static' })
    expect(values).toEqual({ firm: 'Apex', max_loss_usd: null, drawdown_type: 'static' })
    expect(errors['max_loss_usd']).toBe('Max loss cannot be negative.')
  })
})

describe('the red warnings and the net of a payout', () => {
  it('joins the research’s flag and the source’s own words', () => {
    const flags = [{ field: 'price_usd', reason: 'conflict: ETF says $99; PTV says $129', kind: 'conflict' as const }]
    expect(warningOf('price_usd', flags, { price_usd: '79.99 (59.99 with DLL)' })).toBe(
      'conflict: ETF says $99; PTV says $129 · The source says: 79.99 (59.99 with DLL)',
    )
    expect(warningOf('max_loss_usd', flags, {})).toBeNull()
  })

  it.each([
    ['2000.00', '90', '1800.00'],
    ['333.33', '90', '300.00'],
    ['2.25', '50', '1.13'], // half up, as the database checks it
    ['1000.00', '33.333', '333.30'], // the split is kept to two decimals first
    ['1000.00', '0', null],
    ['0', '90', null],
  ])('%s at %s%% is %s', (gross, split, net) => {
    expect(netOf(gross, split)).toBe(net)
  })
})
