import Decimal from 'decimal.js'
import {
  AccountRefused,
  RulesRefused,
  type AccountRow,
  type AccountsOverview,
  type LedgerEntry,
  type PayoutInput,
  type Payouts,
  type RuleFields,
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
import { FIELDS, GROUPS, TEMPLATES, type SampleTemplate } from './ruleData'

/*
 * The firms' rule templates, each account's verified rules, and the Accounts tab, for the SAMPLE DATA. The templates are
 * a few rows of the research CSV (`docs/prop-rules/`), trimmed of their long descriptive text; the checks and messages
 * follow `prop_rules.py`; the arithmetic is `compliance.ts`. In memory only: a reload puts it all back.
 */

/** "Today" for the sample: the Monday after the last sample trading day, so the numbers never drift with the clock. */
export const SAMPLE_TODAY = '2026-10-05'

export { FIELDS, GROUPS } from './ruleData'

const BY_NAME = new Map(FIELDS.map((f) => [f.name, f]))
const KEY_FIELDS = ['firm', 'program', 'account_size', 'phase', 'rule_version']

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
