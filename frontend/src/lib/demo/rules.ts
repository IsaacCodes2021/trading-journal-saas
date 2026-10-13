import Decimal from 'decimal.js'
import {
  AccountRefused,
  RulesRefused,
  type AccountRow,
  type AccountsOverview,
  type LedgerEntry,
  type PayoutInput,
  type Payouts,
  type RuleField,
  type RuleFields,
  type RuleFlag,
  type RuleSet,
  type RuleSetInput,
  type RuleTemplate,
  type RuleTemplateSummary,
  type RuleValues,
} from '../data/model'
import { checkRules, coerceRule, flagKind } from '../rules'
import { evaluateSample, splitShare, type SampleDay } from './compliance'
import { SAMPLE_TRADES } from './generate'
import { accountById, addPayoutEntry, allAccounts, ledgerEntries } from './accounts'

/*
 * The firms' rule templates, each account's verified rules, and the Accounts tab, for the SAMPLE DATA. The templates are
 * a few rows of the research CSV (`docs/prop-rules/`), trimmed of their long descriptive text; the checks and messages
 * follow `prop_rules.py`; the arithmetic is `compliance.ts`. In memory only: a reload puts it all back.
 */

/** "Today" for the sample: the Monday after the last sample trading day, so the numbers never drift with the clock. */
export const SAMPLE_TODAY = '2026-10-05'

export const GROUPS = [
  'Rule set',
  'Cost',
  'Pass / target',
  'Drawdown',
  'Daily loss',
  'Contracts & scaling',
  'Consistency',
  'Qualifying days',
  'Payout',
  'Restrictions',
  'Inactivity',
  'Source',
]

/** `prop_rules.FIELDS`, in the same order. */
export const FIELDS: RuleField[] = [
  { name: 'firm', kind: 'text', group: 'Rule set', label: 'Firm', choices: [], hint: '' },
  { name: 'program', kind: 'text', group: 'Rule set', label: 'Program', choices: [], hint: '' },
  { name: 'account_size', kind: 'text', group: 'Rule set', label: 'Account size', choices: [], hint: '' },
  {
    name: 'phase',
    kind: 'choice',
    group: 'Rule set',
    label: 'Phase',
    choices: ['evaluation', 'funded', 'live'],
    hint: '',
  },
  { name: 'rule_version', kind: 'text', group: 'Rule set', label: 'Rule version', choices: [], hint: '' },
  { name: 'effective_from', kind: 'date', group: 'Rule set', label: 'Effective from', choices: [], hint: '' },
  { name: 'platform_notes', kind: 'text', group: 'Rule set', label: 'Platform notes', choices: [], hint: '' },
  { name: 'price_usd', kind: 'money', group: 'Cost', label: 'Price', choices: [], hint: '' },
  { name: 'billing', kind: 'choice', group: 'Cost', label: 'Billing', choices: ['one_time', 'monthly'], hint: '' },
  { name: 'activation_fee_usd', kind: 'money', group: 'Cost', label: 'Activation fee', choices: [], hint: '' },
  { name: 'reset_fee_usd', kind: 'money', group: 'Cost', label: 'Reset fee', choices: [], hint: '' },
  {
    name: 'profit_target_usd',
    kind: 'money',
    group: 'Pass / target',
    label: 'Profit target',
    choices: [],
    hint: 'On a funded account: the profit goal for each payout.',
  },
  { name: 'max_loss_usd', kind: 'money', group: 'Drawdown', label: 'Max loss', choices: [], hint: '' },
  {
    name: 'drawdown_type',
    kind: 'choice',
    group: 'Drawdown',
    label: 'Drawdown type',
    choices: ['eod_trailing', 'eod_trailing_realtime_enforced', 'intraday_trailing', 'static'],
    hint: '',
  },
  {
    name: 'drawdown_lock_level',
    kind: 'text',
    group: 'Drawdown',
    label: 'Stops trailing at',
    choices: [],
    hint: 'start_balance, start_balance_plus_100, none, or a balance such as 50100.',
  },
  {
    name: 'drawdown_after_payout',
    kind: 'text',
    group: 'Drawdown',
    label: 'After a payout',
    choices: [],
    hint: 'unchanged, locks_at_start, locks_at_start_plus_100, resets_to_zero, or becomes static.',
  },
  {
    name: 'daily_loss_limit_usd',
    kind: 'money',
    group: 'Daily loss',
    label: 'Daily loss limit',
    choices: [],
    hint: '',
  },
  {
    name: 'daily_loss_type',
    kind: 'choice',
    group: 'Daily loss',
    label: 'Daily loss type',
    choices: ['none', 'soft', 'optional', 'hard'],
    hint: '',
  },
  {
    name: 'daily_loss_basis',
    kind: 'choice',
    group: 'Daily loss',
    label: 'Counts',
    choices: ['realized', 'realized_plus_unrealized'],
    hint: 'Empty: not stated, so open losses may count.',
  },
  {
    name: 'max_contracts_minis',
    kind: 'int',
    group: 'Contracts & scaling',
    label: 'Max contracts (minis)',
    choices: [],
    hint: '',
  },
  {
    name: 'max_contracts_micros',
    kind: 'int',
    group: 'Contracts & scaling',
    label: 'Max contracts (micros)',
    choices: [],
    hint: '',
  },
  { name: 'scaling_plan', kind: 'text', group: 'Contracts & scaling', label: 'Scaling plan', choices: [], hint: '' },
  {
    name: 'min_trading_days',
    kind: 'int',
    group: 'Qualifying days',
    label: 'Minimum trading days',
    choices: [],
    hint: '',
  },
  { name: 'consistency_pct', kind: 'pct', group: 'Consistency', label: 'Consistency %', choices: [], hint: '' },
  {
    name: 'consistency_basis',
    kind: 'choice',
    group: 'Consistency',
    label: 'Measured against',
    choices: [
      'none',
      'none stated',
      'best_day_vs_total_profit',
      'best_day_vs_cycle_profit',
      'best_day_vs_profit_target',
      'best_day_vs_profit_goal',
      'best_day_vs_positive_days_sum',
      'best_day_vs_gross_profit_incl_payouts',
      'all_time_best_day_vs_cycle_profit',
    ],
    hint: '',
  },
  {
    name: 'qualifying_day_min_profit_usd',
    kind: 'money',
    group: 'Qualifying days',
    label: 'A qualifying day makes at least',
    choices: [],
    hint: '',
  },
  {
    name: 'qualifying_days_required',
    kind: 'int',
    group: 'Qualifying days',
    label: 'Qualifying days required',
    choices: [],
    hint: '',
  },
  { name: 'payout_buffer_usd', kind: 'money', group: 'Payout', label: 'Buffer above start', choices: [], hint: '' },
  {
    name: 'min_balance_to_request_usd',
    kind: 'money',
    group: 'Payout',
    label: 'Minimum balance to request',
    choices: [],
    hint: '',
  },
  { name: 'min_payout_usd', kind: 'money', group: 'Payout', label: 'Minimum payout', choices: [], hint: '' },
  { name: 'max_payout_usd', kind: 'money', group: 'Payout', label: 'Maximum payout', choices: [], hint: '' },
  {
    name: 'payout_cap_schedule',
    kind: 'text',
    group: 'Payout',
    label: 'Cap by payout number',
    choices: [],
    hint: '1:1500|2:2000|3+:3000, all:2000, or uncapped.',
  },
  {
    name: 'payout_pct_of_profit_cap',
    kind: 'text',
    group: 'Payout',
    label: 'Percentage cap',
    choices: [],
    hint: '50% of profit, 50% of balance or 200% of cycle profit.',
  },
  {
    name: 'payout_frequency',
    kind: 'text',
    group: 'Payout',
    label: 'Cadence',
    choices: [],
    hint: 'daily, every 7 days, every 10 trading days, or every 5 qualifying days.',
  },
  {
    name: 'payout_days_allowed',
    kind: 'text',
    group: 'Payout',
    label: 'Days a request is allowed',
    choices: [],
    hint: 'any, or business days.',
  },
  {
    name: 'max_payouts_before_live_or_close',
    kind: 'text',
    group: 'Payout',
    label: 'Payouts before live or close',
    choices: [],
    hint: '',
  },
  {
    name: 'profit_split',
    kind: 'text',
    group: 'Payout',
    label: 'Profit split',
    choices: [],
    hint: 'Your share first: 90/10.',
  },
  { name: 'news_trading', kind: 'text', group: 'Restrictions', label: 'News trading', choices: [], hint: '' },
  { name: 'overnight_holding', kind: 'text', group: 'Restrictions', label: 'Overnight holding', choices: [], hint: '' },
  { name: 'flat_by_time', kind: 'text', group: 'Restrictions', label: 'Flat by', choices: [], hint: '' },
  {
    name: 'other_restrictions',
    kind: 'text',
    group: 'Restrictions',
    label: 'Other restrictions',
    choices: [],
    hint: '',
  },
  {
    name: 'inactivity_rule',
    kind: 'text',
    group: 'Inactivity',
    label: 'Inactivity rule',
    choices: [],
    hint: 'For example: 2 days $50+ per rolling 30 days, or one trade every 7 calendar days.',
  },
  { name: 'max_accounts', kind: 'text', group: 'Restrictions', label: 'Max accounts', choices: [], hint: '' },
  {
    name: 'data_confidence',
    kind: 'choice',
    group: 'Source',
    label: 'Data confidence',
    choices: ['official', 'conflicting', 'third_party', 'unknown'],
    hint: '',
  },
  { name: 'source_urls', kind: 'text', group: 'Source', label: 'Sources', choices: [], hint: '' },
  { name: 'notes', kind: 'text', group: 'Source', label: 'Notes', choices: [], hint: '' },
]

const BY_NAME = new Map(FIELDS.map((f) => [f.name, f]))
const KEY_FIELDS = ['firm', 'program', 'account_size', 'phase', 'rule_version']

interface SampleTemplate {
  id: number
  values: RuleValues
  flags: RuleFlag[]
  source_notes: Record<string, string>
}

const TEMPLATES: SampleTemplate[] = [
  {
    id: 1,
    values: {
      firm: 'Apex Trader Funding',
      program: 'EOD Trailing (Apex 4.0)',
      account_size: '50K',
      phase: 'evaluation',
      rule_version: 'Apex 4.0 (new accounts)',
      effective_from: '2026-03-01',
      price_usd: '490.00',
      billing: 'one_time',
      profit_target_usd: '3000.00',
      max_loss_usd: '2000.00',
      drawdown_type: 'eod_trailing_realtime_enforced',
      drawdown_lock_level: 'none (Tradovate); profit_target_balance (Rithmic/WealthCharts)',
      daily_loss_limit_usd: '1000.00',
      daily_loss_type: 'soft',
      daily_loss_basis: 'realized_plus_unrealized',
      max_contracts_minis: 6,
      max_contracts_micros: 60,
      min_trading_days: 0,
      consistency_basis: 'none',
      overnight_holding: 'not_allowed',
      flat_by_time: 'all positions closed before market close (4:59 PM ET per third-party Tanto)',
      inactivity_rule: '30 calendar day evaluation access window',
      data_confidence: 'conflicting',
    },
    flags: [
      {
        field: 'price_usd',
        reason: 'third-party only (PTV list price 2026-08-10; Apex runs 80-92% coupons)',
        kind: 'third-party only',
      },
      {
        field: 'activation_fee_usd',
        reason: 'conflict: ETF (2026-05-27) says $99; PTV (2026-08-10) says $129; both third-party',
        kind: 'conflict',
      },
      {
        field: 'flat_by_time',
        reason: 'third-party only (cell cites a third-party source (at least in part))',
        kind: 'third-party only',
      },
    ],
    source_notes: {
      reset_fee_usd: 'none (resets only on Legacy evals; repurchase)',
    },
  },
  {
    id: 2,
    values: {
      firm: 'Apex Trader Funding',
      program: 'EOD Trailing (Apex 4.0)',
      account_size: '150K',
      phase: 'evaluation',
      rule_version: 'Apex 4.0 (new accounts)',
      effective_from: '2026-03-01',
      price_usd: '1490.00',
      billing: 'one_time',
      profit_target_usd: '9000.00',
      max_loss_usd: '4000.00',
      drawdown_type: 'eod_trailing_realtime_enforced',
      drawdown_lock_level: 'none (Tradovate); profit_target_balance (Rithmic/WealthCharts)',
      daily_loss_limit_usd: '2000.00',
      daily_loss_type: 'soft',
      daily_loss_basis: 'realized_plus_unrealized',
      max_contracts_minis: 12,
      max_contracts_micros: 120,
      min_trading_days: 0,
      consistency_basis: 'none',
      overnight_holding: 'not_allowed',
      flat_by_time: 'all positions closed before market close (4:59 PM ET per third-party Tanto)',
      inactivity_rule: '30 calendar day evaluation access window',
      data_confidence: 'conflicting',
    },
    flags: [
      {
        field: 'price_usd',
        reason: 'third-party only (PTV list price 2026-08-10; Apex runs 80-92% coupons)',
        kind: 'third-party only',
      },
      {
        field: 'activation_fee_usd',
        reason: 'conflict: ETF (2026-05-27) says $149; PTV (2026-08-10) says $159; both third-party',
        kind: 'conflict',
      },
      {
        field: 'flat_by_time',
        reason: 'third-party only (cell cites a third-party source (at least in part))',
        kind: 'third-party only',
      },
    ],
    source_notes: {
      reset_fee_usd: 'none (resets only on Legacy evals; repurchase)',
    },
  },
  {
    id: 3,
    values: {
      firm: 'Apex Trader Funding',
      program: 'EOD Trailing (Apex 4.0)',
      account_size: '50K',
      phase: 'funded',
      rule_version: 'Apex 4.0 (new accounts)',
      effective_from: '2026-03-01',
      activation_fee_usd: '129.00',
      max_loss_usd: '2000.00',
      drawdown_type: 'eod_trailing_realtime_enforced',
      drawdown_lock_level: 'start_balance_plus_100',
      daily_loss_limit_usd: '1000.00',
      daily_loss_type: 'soft',
      daily_loss_basis: 'realized_plus_unrealized',
      max_contracts_minis: 4,
      max_contracts_micros: 40,
      consistency_pct: '50',
      consistency_basis: 'best_day_vs_cycle_profit',
      qualifying_day_min_profit_usd: '250.00',
      qualifying_days_required: 5,
      payout_buffer_usd: '2100.00',
      min_balance_to_request_usd: '52600.00',
      min_payout_usd: '500.00',
      max_payout_usd: '1500.00',
      payout_cap_schedule: '1:1500|2:1500|3:2000|4:2500|5:2500|6:3000',
      payout_frequency: 'every 5 new qualifying days (up to weekly)',
      payout_days_allowed: 'any (no weekday restriction stated)',
      max_payouts_before_live_or_close: '6 (PA closes after 6th)',
      profit_split: '100/0',
      overnight_holding: 'not_allowed',
      flat_by_time: 'all positions closed before market close (4:59 PM ET per third-party Tanto)',
      inactivity_rule:
        'at least 2 trading days with $50+ net profit in every rolling 30-day window; dormant from day 15; closed at day 30',
      data_confidence: 'official',
    },
    flags: [
      {
        field: 'activation_fee_usd',
        reason: 'third-party only (ETF/PTV 2026; Apex prices not published as text)',
        kind: 'third-party only',
      },
      {
        field: 'drawdown_after_payout',
        reason: 'unknown: not stated; threshold already locked at start+100 and safety net prevents breach - inference',
        kind: 'unknown',
      },
      {
        field: 'flat_by_time',
        reason: 'third-party only (cell cites a third-party source (at least in part))',
        kind: 'third-party only',
      },
    ],
    source_notes: {},
  },
  {
    id: 4,
    values: {
      firm: 'Apex Trader Funding',
      program: 'EOD Trailing (Apex 4.0)',
      account_size: '150K',
      phase: 'funded',
      rule_version: 'Apex 4.0 (new accounts)',
      effective_from: '2026-03-01',
      activation_fee_usd: '159.00',
      max_loss_usd: '4000.00',
      drawdown_type: 'eod_trailing_realtime_enforced',
      drawdown_lock_level: 'start_balance_plus_100',
      daily_loss_limit_usd: '2500.00',
      daily_loss_type: 'soft',
      daily_loss_basis: 'realized_plus_unrealized',
      max_contracts_minis: 10,
      max_contracts_micros: 100,
      consistency_pct: '50',
      consistency_basis: 'best_day_vs_cycle_profit',
      qualifying_day_min_profit_usd: '350.00',
      qualifying_days_required: 5,
      payout_buffer_usd: '4100.00',
      min_balance_to_request_usd: '154600.00',
      min_payout_usd: '500.00',
      max_payout_usd: '2500.00',
      payout_cap_schedule: '1:2500|2:3000|3:3000|4:3000|5:4000|6:5000',
      payout_frequency: 'every 5 new qualifying days (up to weekly)',
      payout_days_allowed: 'any (no weekday restriction stated)',
      max_payouts_before_live_or_close: '6 (PA closes after 6th)',
      profit_split: '100/0',
      overnight_holding: 'not_allowed',
      flat_by_time: 'all positions closed before market close (4:59 PM ET per third-party Tanto)',
      inactivity_rule:
        'at least 2 trading days with $50+ net profit in every rolling 30-day window; dormant from day 15; closed at day 30',
      data_confidence: 'official',
    },
    flags: [
      {
        field: 'activation_fee_usd',
        reason: 'third-party only (ETF/PTV 2026; Apex prices not published as text)',
        kind: 'third-party only',
      },
      {
        field: 'drawdown_after_payout',
        reason: 'unknown: not stated; threshold already locked at start+100 and safety net prevents breach - inference',
        kind: 'unknown',
      },
      {
        field: 'flat_by_time',
        reason: 'third-party only (cell cites a third-party source (at least in part))',
        kind: 'third-party only',
      },
    ],
    source_notes: {},
  },
  {
    id: 5,
    values: {
      firm: 'Lucid Trading',
      program: 'LucidPro',
      account_size: '50K',
      phase: 'evaluation',
      rule_version: 'current (bought/reset after 2025-11-28 3:00 PM EST)',
      effective_from: '2025-11-28',
      billing: 'one_time',
      activation_fee_usd: '0.00',
      profit_target_usd: '3000.00',
      max_loss_usd: '2000.00',
      drawdown_type: 'eod_trailing',
      drawdown_lock_level: 'start_balance_plus_100',
      daily_loss_limit_usd: '1200.00',
      daily_loss_type: 'optional',
      max_contracts_minis: 4,
      max_contracts_micros: 40,
      min_trading_days: 0,
      consistency_basis: 'none',
      overnight_holding: 'not_allowed',
      flat_by_time: '4:45 PM EST Mon-Fri (earlier on holidays); auto-close is not a failure; reopens 6 PM EST',
      inactivity_rule: 'no trade with net P&L of at least $1 in 30 calendar days -> account deleted',
      data_confidence: 'official',
    },
    flags: [
      {
        field: 'price_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'reset_fee_usd',
        reason: 'unknown: paid dashboard purchase',
        kind: 'unknown',
      },
      {
        field: 'daily_loss_basis',
        reason: 'unknown',
        kind: 'unknown',
      },
    ],
    source_notes: {},
  },
  {
    id: 6,
    values: {
      firm: 'Lucid Trading',
      program: 'LucidPro',
      account_size: '100K',
      phase: 'evaluation',
      rule_version: 'current (bought/reset after 2025-11-28 3:00 PM EST)',
      effective_from: '2025-11-28',
      billing: 'one_time',
      activation_fee_usd: '0.00',
      profit_target_usd: '6000.00',
      max_loss_usd: '3000.00',
      drawdown_type: 'eod_trailing',
      drawdown_lock_level: 'start_balance_plus_100',
      daily_loss_limit_usd: '1800.00',
      daily_loss_type: 'optional',
      max_contracts_minis: 6,
      max_contracts_micros: 60,
      min_trading_days: 0,
      consistency_basis: 'none',
      overnight_holding: 'not_allowed',
      flat_by_time: '4:45 PM EST Mon-Fri (earlier on holidays); auto-close is not a failure; reopens 6 PM EST',
      inactivity_rule: 'no trade with net P&L of at least $1 in 30 calendar days -> account deleted',
      data_confidence: 'official',
    },
    flags: [
      {
        field: 'price_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'reset_fee_usd',
        reason: 'unknown: paid dashboard purchase',
        kind: 'unknown',
      },
      {
        field: 'daily_loss_basis',
        reason: 'unknown',
        kind: 'unknown',
      },
    ],
    source_notes: {},
  },
  {
    id: 7,
    values: {
      firm: 'Lucid Trading',
      program: 'LucidPro',
      account_size: '50K',
      phase: 'funded',
      rule_version: 'current (bought/reset after 2025-11-28 3:00 PM EST)',
      effective_from: '2025-11-28',
      max_loss_usd: '2000.00',
      drawdown_type: 'eod_trailing',
      drawdown_lock_level: 'start_balance_plus_100 (once EOD balance > 52100)',
      daily_loss_type: 'soft',
      max_contracts_minis: 4,
      max_contracts_micros: 40,
      consistency_pct: '40',
      consistency_basis: 'best_day_vs_cycle_profit',
      payout_buffer_usd: '2100.00',
      min_balance_to_request_usd: '52600.00',
      min_payout_usd: '500.00',
      payout_days_allowed: 'any',
      max_payouts_before_live_or_close: 'live review after payout 5',
      profit_split: '90/10',
      overnight_holding: 'not_allowed',
      flat_by_time: '4:45 PM EST Mon-Fri (earlier on holidays); auto-close is not a failure; reopens 6 PM EST',
      inactivity_rule: 'no trade with net P&L of at least $1 in 30 calendar days -> account deleted',
      data_confidence: 'conflicting',
    },
    flags: [
      {
        field: 'drawdown_after_payout',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'daily_loss_basis',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'qualifying_days_required',
        reason: 'unknown: none found officially',
        kind: 'unknown',
      },
      {
        field: 'max_payout_usd',
        reason: 'conflict: Pro overview says no simulated payout caps; payout page says 2000',
        kind: 'conflict',
      },
      {
        field: 'payout_cap_schedule',
        reason: 'conflict: Pro overview says no simulated payout caps; payout page says 1:2000/2+:2500',
        kind: 'conflict',
      },
      {
        field: 'payout_frequency',
        reason: 'conflict: propfirmmatch says every 3 days; official says no fixed window',
        kind: 'conflict',
      },
    ],
    source_notes: {
      daily_loss_limit_usd: '1200 fixed, then LucidScale = 60% of highest EOD profit once closed above 52100',
    },
  },
  {
    id: 8,
    values: {
      firm: 'Lucid Trading',
      program: 'LucidPro',
      account_size: '50K',
      phase: 'funded',
      rule_version: 'legacy (bought/reset before 2025-11-28 3:00 PM EST)',
      consistency_pct: '35',
      consistency_basis: 'best_day_vs_cycle_profit',
      profit_split: '100% of first $10K, then 90/10',
      overnight_holding: 'not_allowed',
      flat_by_time: '4:45 PM EST Mon-Fri (earlier on holidays); auto-close is not a failure; reopens 6 PM EST',
      inactivity_rule: 'no trade with net P&L of at least $1 in 30 calendar days -> account deleted',
      data_confidence: 'official',
    },
    flags: [
      {
        field: 'max_loss_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'drawdown_type',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'drawdown_lock_level',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'drawdown_after_payout',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'daily_loss_limit_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'daily_loss_type',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'daily_loss_basis',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'max_contracts_minis',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'max_contracts_micros',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'qualifying_day_min_profit_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'qualifying_days_required',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'payout_buffer_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'min_balance_to_request_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'min_payout_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'max_payout_usd',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'payout_cap_schedule',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'payout_pct_of_profit_cap',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'payout_frequency',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'payout_days_allowed',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'max_payouts_before_live_or_close',
        reason: 'unknown',
        kind: 'unknown',
      },
    ],
    source_notes: {
      effective_from: 'before 2025-11-28',
    },
  },
  {
    id: 9,
    values: {
      firm: 'Tradeify',
      program: 'Select Flex',
      account_size: '50K',
      phase: 'funded',
      rule_version: 'bought after 2026-09-01',
      effective_from: '2026-09-01',
      max_loss_usd: '2000.00',
      drawdown_type: 'eod_trailing_realtime_enforced',
      drawdown_lock_level: 'start_balance_plus_100 (once EOD balance reaches 52100)',
      drawdown_after_payout: 'locks_at_start_plus_100 immediately on payout request',
      daily_loss_type: 'none',
      consistency_basis: 'none',
      qualifying_day_min_profit_usd: '150.00',
      qualifying_days_required: 5,
      min_payout_usd: '250.00',
      max_payout_usd: '2500.00',
      payout_cap_schedule: 'all:2500',
      payout_pct_of_profit_cap: '50% of profit',
      payout_frequency: 'every 5 winning days; cycle must show positive net profit',
      payout_days_allowed: 'any',
      max_payouts_before_live_or_close: 'Elite Live eligibility: 3 payouts on one account or 10 across plans',
      profit_split: '90/10',
      overnight_holding: 'not_allowed',
      flat_by_time: '4:45 PM ET (12:59 PM ET on holidays); auto-closed without failing',
      data_confidence: 'official',
    },
    flags: [
      {
        field: 'max_contracts_minis',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'max_contracts_micros',
        reason: 'unknown',
        kind: 'unknown',
      },
      {
        field: 'inactivity_rule',
        reason: 'unknown',
        kind: 'unknown',
      },
    ],
    source_notes: {
      payout_buffer_usd: 'none',
    },
  },
] as SampleTemplate[]

const filled = (values: RuleValues): RuleValues =>
  Object.fromEntries(FIELDS.map((f) => [f.name, values[f.name] ?? null])) as RuleValues

/** The verified rules of the sample accounts: copies of a template, with the person's own changes. */
function verified(
  id: number,
  accountId: number,
  templateId: number,
  changes: RuleValues = {},
  verifiedAt = '2026-09-01T15:00:00Z',
): RuleSet {
  const template = TEMPLATES.find((t) => t.id === templateId)
  if (!template) throw new Error(`no sample template ${templateId}`)
  return {
    id,
    account_id: accountId,
    source_template_id: templateId,
    verified_at: verifiedAt,
    values: filled({ ...template.values, ...changes }),
    flags: template.flags.map((f) => ({ ...f })),
    source_notes: { ...template.source_notes },
  }
}

// The sample does not check inactivity, and the person cleared the texts it cannot read (see compliance.ts).
const INITIAL_RULE_SETS: RuleSet[] = [
  verified(1, 1, 3, { inactivity_rule: null }), // Apex 50K #1: the Apex 4.0 50K PA
  verified(2, 2, 4, { inactivity_rule: null }), // Apex 150K: the Apex 4.0 150K PA
  verified(3, 3, 6, { inactivity_rule: null }), // Lucid 100K: the LucidPro 100K evaluation
  // Apex 50K #2 has no rules yet: the tab says "No rules set".
]

let ruleSets: RuleSet[] = INITIAL_RULE_SETS.map((r) => ({ ...r }))
let nextRuleSetId = 4

/** Back to the generated state. For tests; a page reload does the same. */
export function resetDemoRules(): void {
  ruleSets = INITIAL_RULE_SETS.map((r) => ({ ...r }))
  nextRuleSetId = 4
}

// ---- reading -----------------------------------------------------------------------------------------------------

export function ruleFields(): RuleFields {
  return { groups: [...GROUPS], fields: FIELDS.map((f) => ({ ...f, choices: [...f.choices] })) }
}

function summaryOf(t: SampleTemplate, preselect: boolean): RuleTemplateSummary {
  const v = t.values
  return {
    id: t.id,
    firm: String(v['firm']),
    program: String(v['program']),
    account_size: String(v['account_size']),
    phase: v['phase'] as RuleTemplateSummary['phase'],
    rule_version: String(v['rule_version']),
    effective_from: (v['effective_from'] as string | null | undefined) ?? null,
    effective_from_note: t.source_notes['effective_from'] ?? null,
    data_confidence: (v['data_confidence'] as string | null | undefined) ?? null,
    flagged: t.flags.length,
    preselect,
  }
}

/** As `pg/rules.offered_templates`: the versions in force on the purchase date and the undated ones; the latest dated
 * version of each firm + program + size + phase preselected. */
export function ruleTemplates(purchasedOn: string | null): RuleTemplateSummary[] {
  const offered = TEMPLATES.filter((t) => {
    const from = t.values['effective_from'] as string | undefined
    return purchasedOn === null || !from || from <= purchasedOn
  })
  const best = new Map<string, SampleTemplate>()
  if (purchasedOn !== null) {
    for (const t of offered) {
      const key = KEY_FIELDS.slice(0, 4)
        .map((k) => t.values[k])
        .join('|')
      const current = best.get(key)
      if (!current || later(t, current)) best.set(key, t)
    }
  }
  const chosen = new Set([...best.values()].map((t) => t.id))
  return offered.map((t) => summaryOf(t, chosen.has(t.id)))
}

function later(a: SampleTemplate, b: SampleTemplate): boolean {
  const da = (a.values['effective_from'] as string | undefined) ?? null
  const db = (b.values['effective_from'] as string | undefined) ?? null
  if (da && db && da !== db) return da > db
  if ((da === null) !== (db === null)) return da !== null
  return a.id > b.id
}

export function ruleTemplate(id: number): RuleTemplate {
  const t = TEMPLATES.find((x) => x.id === id)
  if (!t) throw new RulesRefused('That rule template does not exist.')
  return {
    id: t.id,
    values: filled(t.values),
    flags: t.flags.map((f) => ({ ...f })),
    source_notes: { ...t.source_notes },
    updated_at: '2026-10-01T00:00:00Z',
  }
}

export function ruleSetOf(accountId: number): RuleSet | null {
  accountById(accountId)
  return ruleSets.find((r) => r.account_id === accountId) ?? null
}

// ---- checking and saving a rule set (the form's own checks, `lib/rules.ts`, as the server's) ------------------------------

export function saveRuleSet(accountId: number, input: RuleSetInput): RuleSet {
  accountById(accountId)
  const fields: Record<string, string> = {}
  const values: RuleValues = {}
  for (const name of Object.keys(input.values)) if (!BY_NAME.has(name)) fields[`values.${name}`] = 'Not a rule field.'
  for (const field of FIELDS) {
    try {
      values[field.name] = coerceRule(field, input.values[field.name])
    } catch (error) {
      values[field.name] = null
      fields[`values.${field.name}`] = error instanceof Error ? error.message : 'Not valid.'
    }
  }
  const labels = Object.fromEntries(FIELDS.map((f) => [f.name, f.label]))
  for (const [name, message] of Object.entries(checkRules(values, labels))) fields[`values.${name}`] ??= message
  input.flags.forEach((flag, n) => {
    if (!BY_NAME.has(flag.field)) fields[`flags.${n}.field`] = 'Not a rule field.'
  })
  if (Object.keys(fields).length) throw new RulesRefused('The request is not valid.', fields)
  if (input.source_template_id !== null && !TEMPLATES.some((t) => t.id === input.source_template_id)) {
    throw new RulesRefused('That rule template does not exist.')
  }
  const saved: RuleSet = {
    id: nextRuleSetId++,
    account_id: accountId,
    source_template_id: input.source_template_id,
    verified_at: new Date().toISOString(),
    values,
    flags: input.flags.map((f) => ({ field: f.field, reason: f.reason, kind: flagKind(f.reason) })),
    source_notes: { ...input.source_notes },
  }
  ruleSets = [...ruleSets.filter((r) => r.account_id !== accountId), saved]
  return saved
}

// ---- payouts -------------------------------------------------------------------------------------------------------

/** The split the account's rules promise, in percent with cents ("90.00"), or null. */
function rulesSplit(accountId: number): string | null {
  const text = ruleSetOf(accountId)?.values['profit_split']
  const share = typeof text === 'string' ? splitShare(text) : null
  return share ? share.toFixed(2) : null
}

export function payoutsOf(accountId: number): Payouts {
  accountById(accountId)
  const payouts = ledgerEntries()
    .filter((e) => e.account_id === accountId && e.kind === 'payout')
    .sort((a, b) => a.entry_date.localeCompare(b.entry_date) || a.id - b.id)
  const sum = (values: string[]) => values.reduce((s, v) => s.plus(v), new Decimal(0)).toFixed(2)
  return {
    payouts: payouts.map((p) => ({ ...p })),
    split_pct: rulesSplit(accountId),
    next_number: payouts.length + 1,
    lifetime_gross: sum(payouts.map((p) => p.gross_amount ?? p.amount)),
    lifetime_net: sum(payouts.map((p) => p.amount)),
    lifetime_fees: sum(payouts.map((p) => p.payout_fee ?? '0')),
  }
}

export function addPayout(accountId: number, input: PayoutInput): LedgerEntry {
  accountById(accountId)
  const split = input.split_pct ?? rulesSplit(accountId)
  if (split === null) throw new AccountRefused("This account's rules give no profit split: enter the split.")
  return addPayoutEntry(accountId, { ...input, split_pct: split, fee: input.fee ?? '0' })
}

// ---- the Accounts tab --------------------------------------------------------------------------------------------

function daysOf(accountId: number): SampleDay[] {
  const byDay = new Map<string, Decimal>()
  for (const t of SAMPLE_TRADES) {
    if (t.account.id !== accountId) continue
    byDay.set(t.trade_day, (byDay.get(t.trade_day) ?? new Decimal(0)).plus(t.net_pnl))
  }
  return [...byDay].map(([day, net]) => ({ day, net }))
}

export function accountsOverview(): AccountsOverview {
  const rows: AccountRow[] = []
  for (const account of allAccounts().filter((a) => a.outcome === null)) {
    const payouts = ledgerEntries().filter((e) => e.account_id === account.id && e.kind === 'payout')
    const withdrawnGross = payouts.reduce((s, p) => s.plus(p.gross_amount ?? p.amount), new Decimal(0))
    const withdrawnNet = payouts.reduce((s, p) => s.plus(p.amount), new Decimal(0))
    const rules = ruleSetOf(account.id)
    const days = daysOf(account.id)
    const common = {
      account,
      phase: account.phase,
      withdrawn_gross: withdrawnGross.toFixed(2),
      withdrawn_net: withdrawnNet.toFixed(2),
    }
    if (!rules) {
      const net = days.reduce((s, d) => s.plus(d.net), new Decimal(0))
      rows.push({
        ...common,
        program: null,
        rules_set: false,
        balance: new Decimal(account.starting_balance).plus(net).minus(withdrawnGross).toFixed(2),
        net_pnl: net.toFixed(2),
        status: null,
        rules: [],
        spectrum: null,
        payout: null,
        consistency_headroom: null,
        inactivity: null,
        floor: null,
        progress: '',
        pass_prompt: false,
      })
      continue
    }
    const result = evaluateSample({
      values: rules.values,
      start: account.starting_balance,
      phase: account.phase,
      days,
      payouts: payouts.map((p) => ({ day: p.entry_date, gross: p.gross_amount, net: p.amount })),
      today: SAMPLE_TODAY,
    })
    rows.push({
      ...common,
      program: (rules.values['program'] as string | null) ?? null,
      rules_set: true,
      balance: result.balance.toFixed(2),
      net_pnl: result.net_pnl.toFixed(2),
      status: result.status,
      rules: result.rules,
      spectrum: result.spectrum,
      payout: result.payout,
      consistency_headroom: result.consistency_headroom?.toFixed(2) ?? null,
      inactivity: result.inactivity,
      floor: result.floor?.toFixed(2) ?? null,
      progress: result.progress,
      pass_prompt:
        result.status === 'passed' &&
        account.account_type === 'evaluation' &&
        account.pass_prompt_dismissed_at === null,
    })
  }
  const sum = (values: (string | null)[]) => values.reduce((s, v) => s.plus(v ?? 0), new Decimal(0)).toFixed(2)
  const ready = rows.filter((r) => r.payout?.ready)
  return {
    accounts: rows,
    totals: {
      balance: sum(rows.map((r) => r.balance)),
      net_pnl: sum(rows.map((r) => r.net_pnl)),
      withdrawable_gross: sum(ready.map((r) => r.payout?.gross ?? null)),
      withdrawable_net: sum(ready.map((r) => r.payout?.net ?? null)),
      withdrawn_gross: sum(rows.map((r) => r.withdrawn_gross)),
      withdrawn_net: sum(rows.map((r) => r.withdrawn_net)),
    },
  }
}
