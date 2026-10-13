import Decimal from 'decimal.js'
import type { AccountStatus, RuleField, RuleFlag, RuleStatus, RuleValue, RuleValues } from './data/model'

/*
 * Prop-firm rule sets in the form: typing what was entered, and the checks that block saving. They repeat the server's
 * `prop_rules.coerce` and `prop_rules.check`, in the same words, so a person sees the problem before sending; the server
 * checks again and has the last word.
 */

export const KEY_FIELDS = ['firm', 'program', 'account_size', 'phase', 'rule_version'] as const

export const STATUS_LABELS: Record<AccountStatus, string> = {
  ok: 'OK',
  at_risk: 'At risk',
  breached: 'Breached',
  passed: 'Passed',
  payout_ready: 'Payout ready',
}

export const RULE_STATUS_LABELS: Record<RuleStatus, string> = {
  pass: 'Pass',
  fail: 'Fail',
  'n/a': 'n/a',
  not_verifiable: 'Not verifiable from imports',
}

const NUMBER = /^-?\d+(\.\d+)?$/
const INTEGER = /^\d+$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const MAX_TEXT = 4000

/** A value from the form as the field's type: money and percentages as decimal strings, whole numbers as numbers. Throws
 * an Error with the server's sentence when it is not one. */
export function coerceRule(field: RuleField, value: unknown): RuleValue {
  if (value === null || value === undefined || (typeof value === 'string' && !value.trim())) return null
  const label = field.label
  if (field.kind === 'text' || field.kind === 'choice') {
    if (typeof value !== 'string') throw new Error(`${label} must be text.`)
    const text = value.trim()
    if (text.length > MAX_TEXT) throw new Error(`${label} must be at most ${MAX_TEXT} characters.`)
    if (field.kind === 'choice' && !field.choices.includes(text)) {
      throw new Error(`${label} must be one of: ${field.choices.join(', ')}.`)
    }
    return text
  }
  if (field.kind === 'date') {
    const text = typeof value === 'string' ? value.trim() : ''
    if (DATE.test(text) && !Number.isNaN(Date.parse(text))) return text
    throw new Error(`${label} must be a date (YYYY-MM-DD).`)
  }
  if (field.kind === 'int') {
    const text = typeof value === 'number' ? String(value) : typeof value === 'string' ? value.trim() : ''
    if (!INTEGER.test(text) || Number(text) > 1_000_000) throw new Error(`${label} must be a whole number, 0 or more.`)
    return Number(text)
  }
  const text = typeof value === 'string' ? value.trim().replaceAll(',', '') : ''
  if (!NUMBER.test(text)) throw new Error(`${label} must be a number.`)
  const number = new Decimal(text)
  if (field.kind === 'money') {
    if (number.isNegative()) throw new Error(`${label} cannot be negative.`)
    return number.toFixed(2)
  }
  if (number.lessThanOrEqualTo(0) || number.greaterThan(100)) {
    throw new Error(`${label} must be more than 0 and at most 100.`)
  }
  return number.toFixed(2)
}

/** What stops a person's rule set from being saved: a missing name, and a rule switched on without what it needs. */
export function checkRules(values: RuleValues, labels: Record<string, string> = {}): Record<string, string> {
  const errors: Record<string, string> = {}
  const has = (name: string) => values[name] !== null && values[name] !== undefined && values[name] !== ''
  const num = (name: string) => (has(name) ? new Decimal(String(values[name])) : null)
  for (const name of KEY_FIELDS) if (!has(name)) errors[name] = `${labels[name] ?? name} is required.`
  if (has('drawdown_type') && !has('max_loss_usd')) {
    errors['max_loss_usd'] = 'Give the max loss, or clear the drawdown type.'
  }
  if (['soft', 'hard'].includes(String(values['daily_loss_type'])) && !has('daily_loss_limit_usd')) {
    errors['daily_loss_limit_usd'] = 'Give the daily loss limit, or set the type to none.'
  }
  const basis = values['consistency_basis']
  const noBasis = !basis || basis === 'none' || basis === 'none stated'
  if (!noBasis && !has('consistency_pct')) {
    errors['consistency_pct'] = 'Give the consistency percentage, or set what it is measured against to none.'
  }
  if (has('consistency_pct') && noBasis) {
    errors['consistency_basis'] = 'Choose what the consistency percentage is measured against, or clear it.'
  }
  const buffer = num('payout_buffer_usd')
  const maxLoss = num('max_loss_usd')
  if (buffer && maxLoss && buffer.greaterThan(0) && buffer.lessThan(maxLoss)) {
    errors['payout_buffer_usd'] = 'The buffer is below the max loss. Check it: it is usually the max loss plus $100.'
  }
  if (has('qualifying_day_min_profit_usd') && !has('qualifying_days_required')) {
    errors['qualifying_days_required'] = 'Give how many qualifying days are needed, or clear the daily minimum.'
  }
  return errors
}

/** The form's text for every field, typed, and the fields that cannot be read or do not make sense together. */
export function readRuleForm(
  fields: readonly RuleField[],
  entered: Record<string, string>,
): { values: RuleValues; errors: Record<string, string> } {
  const values: RuleValues = {}
  const errors: Record<string, string> = {}
  for (const field of fields) {
    try {
      values[field.name] = coerceRule(field, entered[field.name] ?? '')
    } catch (error) {
      values[field.name] = null
      errors[field.name] = error instanceof Error ? error.message : 'Not valid.'
    }
  }
  const labels = Object.fromEntries(fields.map((f) => [f.name, f.label]))
  for (const [name, message] of Object.entries(checkRules(values, labels))) errors[name] ??= message
  return { values, errors }
}

/** A stored value as the form shows it. */
export function ruleText(value: RuleValue | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

export function flagKind(reason: string): RuleFlag['kind'] {
  const lower = reason.trim().toLowerCase()
  if (lower.startsWith('conflict')) return 'conflict'
  if (lower.startsWith('third-party only')) return 'third-party only'
  return 'unknown'
}

/** What the red tooltip of a field says: the research's flag, and the source's own words for a cell it could not read. */
export function warningOf(name: string, flags: readonly RuleFlag[], notes: Record<string, string>): string | null {
  const parts = flags.filter((f) => f.field === name).map((f) => f.reason)
  const note = notes[name]
  if (note) parts.push(`The source says: ${note}`)
  return parts.length ? parts.join(' · ') : null
}

/** What a payout pays at a split: gross x split, to the cent, half up (as the server keeps it). */
export function netOf(gross: string, splitPct: string): string | null {
  try {
    const g = new Decimal(gross)
    const s = new Decimal(splitPct).toDecimalPlaces(2, Decimal.ROUND_HALF_UP)
    if (!g.greaterThan(0) || !s.greaterThan(0) || s.greaterThan(100)) return null
    return g.times(s).div(100).toDecimalPlaces(2, Decimal.ROUND_HALF_UP).toFixed(2)
  } catch {
    return null
  }
}
