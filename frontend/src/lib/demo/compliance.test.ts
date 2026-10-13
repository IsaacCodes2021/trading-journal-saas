import Decimal from 'decimal.js'
import { describe, expect, it } from 'vitest'
import type { RuleValues } from '../data/model'
import { evaluateSample, splitShare } from './compliance'

/*
 * The sample engine against the backend's own worked examples (`tests/test_compliance.py`): the same rule sets, trades
 * and answers, so the sample data in `pnpm dev` never drifts far from what the server says.
 */

const weekdays = (first: string, count: number): string[] => {
  const out: string[] = []
  for (let d = new Date(`${first}T12:00:00Z`); out.length < count; d = new Date(d.getTime() + 86_400_000)) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(d.toISOString().slice(0, 10))
  }
  return out
}

const days = (first: string, amounts: string[]) =>
  weekdays(first, amounts.length).map((day, n) => ({ day, net: new Decimal(amounts[n] ?? '0') }))

const run = (values: RuleValues, input: Partial<Parameters<typeof evaluateSample>[0]> = {}) =>
  evaluateSample({ values, start: '50000', phase: 'funded', days: [], payouts: [], today: '2026-10-12', ...input })

const result = (evaluation: ReturnType<typeof evaluateSample>, key: string) =>
  evaluation.rules.find((r) => r.key === key)

const LUCID_PRO_50K: RuleValues = {
  firm: 'Lucid Trading',
  program: 'LucidPro',
  max_loss_usd: '2000',
  drawdown_type: 'eod_trailing',
  drawdown_lock_level: 'start_balance_plus_100 (once EOD balance > 52100)',
  daily_loss_type: 'soft',
  consistency_pct: '40',
  consistency_basis: 'best_day_vs_cycle_profit',
  payout_buffer_usd: '2100',
  min_balance_to_request_usd: '52600',
  min_payout_usd: '500',
  payout_days_allowed: 'any',
  max_payouts_before_live_or_close: 'live review after payout 5',
  profit_split: '90/10',
}
const LUCID_DAYS = days('2026-09-28', ['700', '800', '600', '900', '500', '500']) // 54,000

const TRADEIFY_SELECT_FLEX_50K: RuleValues = {
  firm: 'Tradeify',
  max_loss_usd: '2000',
  drawdown_type: 'eod_trailing_realtime_enforced',
  drawdown_lock_level: 'start_balance_plus_100 (once EOD balance reaches 52100)',
  drawdown_after_payout: 'locks_at_start_plus_100 immediately on payout request',
  daily_loss_type: 'none',
  consistency_basis: 'none',
  qualifying_day_min_profit_usd: '150',
  qualifying_days_required: 5,
  min_payout_usd: '250',
  max_payout_usd: '2500',
  payout_cap_schedule: 'all:2500',
  payout_pct_of_profit_cap: '50% of profit',
  payout_days_allowed: 'any',
  profit_split: '90/10',
}

const APEX_EOD_50K_EVAL: RuleValues = {
  firm: 'Apex Trader Funding',
  profit_target_usd: '3000',
  max_loss_usd: '2000',
  drawdown_type: 'eod_trailing_realtime_enforced',
  drawdown_lock_level: 'none (Tradovate); profit_target_balance (Rithmic/WealthCharts)',
  daily_loss_limit_usd: '1000',
  daily_loss_type: 'soft',
  daily_loss_basis: 'realized_plus_unrealized',
  min_trading_days: 0,
  consistency_basis: 'none',
}

describe('the sample engine repeats the backend’s worked examples', () => {
  it('LucidPro is not payout ready while the cap is unknown', () => {
    const e = run(LUCID_PRO_50K, { days: LUCID_DAYS })
    expect(result(e, 'payout_cap')?.status).toBe('not_verifiable')
    expect(e.status).toBe('ok')
    expect(e.payout?.ready).toBe(false)
  })

  it('LucidPro with the person’s cap pays the least of cap and buffer room at 90%', () => {
    const e = run({ ...LUCID_PRO_50K, payout_cap_schedule: 'all:1500' }, { days: LUCID_DAYS })
    expect(e.status).toBe('payout_ready')
    expect([e.payout?.gross, e.payout?.net, e.payout?.split_pct]).toEqual(['1500.00', '1350.00', '90.00'])
    expect(result(e, 'drawdown')?.status).toBe('pass')
    expect(e.floor?.toFixed(2)).toBe('50100.00')
    expect(result(e, 'consistency')?.current).toBe('22.5%')
  })

  it('Tradeify Select Flex pays half the profit', () => {
    const e = run(TRADEIFY_SELECT_FLEX_50K, { days: days('2026-09-28', ['600', '600', '600', '600', '600']) })
    expect(e.status).toBe('payout_ready')
    expect([e.payout?.gross, e.payout?.net]).toEqual(['1500.00', '1350.00'])
    expect(result(e, 'consistency')?.status).toBe('n/a')
    expect(e.consistency_headroom).toBeNull()
  })

  it('Tradeify’s floor locks at start + 100 on the first payout, and qualifying days count again', () => {
    const e = run(TRADEIFY_SELECT_FLEX_50K, {
      days: days('2026-09-28', ['600', '600', '600', '600', '600']),
      payouts: [{ day: '2026-10-05', gross: '1500', net: '1350' }],
    })
    expect(e.floor?.toFixed(2)).toBe('50100.00')
    expect(e.balance.toFixed(2)).toBe('51500.00')
    expect(e.payout?.next_number).toBe(2)
    expect(result(e, 'qualifying_days')?.current).toBe('0')
  })

  it('an evaluation that reaches its target has passed', () => {
    const e = run(APEX_EOD_50K_EVAL, {
      phase: 'evaluation',
      days: [
        { day: '2026-10-01', net: new Decimal('1200') },
        { day: '2026-10-02', net: new Decimal('900') },
        { day: '2026-10-05', net: new Decimal('1100') },
      ],
    })
    expect(e.status).toBe('passed')
    expect(e.payout).toBeNull()
    expect(e.balance.toFixed(2)).toBe('53200.00')
    expect([e.spectrum.target_kind, e.spectrum.target_level, e.spectrum.to_target, e.spectrum.position]).toEqual([
      'profit_target',
      '53000.00',
      '0.00',
      1,
    ])
    expect(e.progress).toBe('target reached')
    expect(e.floor?.toFixed(2)).toBe('51200.00') // never stops trailing on Tradovate
  })

  it('a static floor breached by a closed day is a breach, whatever else holds', () => {
    const e = run(
      { max_loss_usd: '2000', drawdown_type: 'static' },
      { days: days('2026-10-05', ['-1500', '-600', '900']) },
    )
    expect(e.status).toBe('breached')
    expect(result(e, 'drawdown')?.status).toBe('fail')
  })

  it('fills the bar once the balance is past the payout balance and today’s loss limit sits above it', () => {
    const e = run(
      { ...LUCID_PRO_50K, daily_loss_limit_usd: '1000', payout_cap_schedule: 'all:1500' },
      { days: LUCID_DAYS },
    )
    expect([e.spectrum.breach_kind, e.spectrum.breach_level, e.spectrum.target_level]).toEqual([
      'daily_loss',
      '53000.00',
      '52600.00',
    ])
    expect(e.spectrum.position).toBe(1)
  })

  it('reads the split the way the backend does', () => {
    expect(splitShare('90/10')?.toString()).toBe('90')
    expect(splitShare('100% of first $10K lifetime then 90/10')?.toString()).toBe('90')
    expect(splitShare('up to 100%')).toBeNull()
  })
})
