import Decimal from 'decimal.js'
import {
  AccountRefused,
  FIRMS,
  LEDGER_KINDS,
  OUTCOMES_BY_TYPE,
  OUTCOME_LABELS,
  PHASES_BY_TYPE,
  TRAILING_TYPES,
  type AccountDetail,
  type AccountRef,
  type AccountDetailsInput,
  type AccountOverview,
  type AccountRulesInput,
  type ArchiveInput,
  type BusinessTotals,
  type FeeSchedule,
  type LedgerEntry,
  type NewLedgerEntry,
  type Phase,
} from '../data/model'
import { ACCOUNT_TYPES } from '../filters'
import { SAMPLE_TRADES } from './generate'

/*
 * The sample accounts: what a person can set up, change, archive, and the money they put in and take out. It follows
 * the backend's rules and messages (`accounts.py`, `ledger.py`), and is in memory only: a reload puts it all back.
 * Progress against the rules (profit target, drawdown left) is worked out here from the sample trades; the real
 * rule tracker is a feature for after the beta (doc 05), and the server will own that arithmetic.
 */

const ZERO = new Decimal(0)

const base = (
  over: Partial<AccountDetail> & Pick<AccountDetail, 'id' | 'name' | 'firm' | 'account_type'>,
): AccountDetail => ({
  account_size: null,
  starting_balance: '0.00',
  max_drawdown: null,
  trailing_type: null,
  trail_lock_enabled: false,
  trail_lock_level: null,
  daily_loss_limit: null,
  profit_target: null,
  created_at: '2026-08-01T15:00:00Z',
  outcome: null,
  ended_on: null,
  archive_note: '',
  purchase_date: null,
  pass_prompt_dismissed_at: null,
  ...over,
  phase: over.phase ?? over.account_type,
})

const INITIAL_ACCOUNTS: AccountDetail[] = [
  base({
    id: 1,
    name: 'Apex 50K #1',
    firm: 'Apex',
    account_type: 'funded',
    account_size: '50000.00',
    starting_balance: '50000.00',
    max_drawdown: '2500.00',
    trailing_type: 'eod',
    purchase_date: '2026-08-18',
  }),
  base({
    id: 2,
    name: 'Apex 150K',
    firm: 'Apex',
    account_type: 'funded',
    account_size: '150000.00',
    starting_balance: '150000.00',
    max_drawdown: '5000.00',
    trailing_type: 'eod',
    purchase_date: '2026-07-28',
  }),
  base({
    id: 3,
    name: 'Lucid 100K',
    firm: 'Lucid',
    account_type: 'evaluation',
    account_size: '100000.00',
    starting_balance: '100000.00',
    max_drawdown: '3000.00',
    trailing_type: 'intraday',
    profit_target: '6000.00',
    purchase_date: '2026-09-01',
  }),
  base({
    id: 4,
    name: 'Apex 50K #2',
    firm: 'Apex',
    account_type: 'evaluation',
    account_size: '50000.00',
    starting_balance: '50000.00',
    max_drawdown: '2500.00',
    trailing_type: 'eod',
    profit_target: '3000.00',
    purchase_date: '2026-08-24',
  }),
  base({
    id: 5,
    name: 'Apex 50K (old)',
    firm: 'Apex',
    account_type: 'evaluation',
    account_size: '50000.00',
    starting_balance: '50000.00',
    outcome: 'failed',
    ended_on: '2026-08-14',
    archive_note: 'Hit the trailing drawdown on CPI day.',
  }),
  base({
    id: 6,
    name: 'Lucid 50K',
    firm: 'Lucid',
    account_type: 'evaluation',
    account_size: '50000.00',
    starting_balance: '50000.00',
    outcome: 'passed',
    ended_on: '2026-07-22',
  }),
  base({
    id: 7,
    name: 'Apex 25K',
    firm: 'Apex',
    account_type: 'evaluation',
    account_size: '25000.00',
    starting_balance: '25000.00',
    outcome: 'passed',
    ended_on: '2026-07-02',
  }),
  base({
    id: 8,
    name: 'Tradeify 50K',
    firm: 'Tradeify',
    account_type: 'evaluation',
    account_size: '50000.00',
    starting_balance: '50000.00',
    outcome: 'passed',
    ended_on: '2026-06-18',
  }),
  base({
    id: 9,
    name: 'Bulenox 100K',
    firm: 'Bulenox',
    account_type: 'evaluation',
    account_size: '100000.00',
    starting_balance: '100000.00',
    outcome: 'failed',
    ended_on: '2026-06-04',
  }),
]

const INITIAL_FEES: [number, FeeSchedule][] = [1, 2, 3, 4].map((id) => [
  id,
  { NQ: '2.20', MNQ: '0.74', ES: '2.20', GC: '2.50', CL: '2.50', '*': '2.50' },
])

const entry = (
  id: number,
  account_id: number,
  kind: LedgerEntry['kind'],
  amount: string,
  entry_date: string,
  note = '',
  payout: Pick<LedgerEntry, 'payout_number' | 'gross_amount' | 'profit_split_pct' | 'payout_fee'> | null = null,
): LedgerEntry => ({
  id,
  account_id,
  kind,
  amount,
  entry_date,
  note,
  payout_number: payout?.payout_number ?? null,
  gross_amount: payout?.gross_amount ?? null,
  profit_split_pct: payout?.profit_split_pct ?? null,
  payout_fee: payout?.payout_fee ?? null,
})

/** Apex pays 100% of the profit, so these payouts' net is their gross. */
const FULL_SPLIT = (gross: string) => ({
  payout_number: 1,
  gross_amount: gross,
  profit_split_pct: '100.00',
  payout_fee: '0.00',
})

const INITIAL_LEDGER: LedgerEntry[] = [
  entry(1, 1, 'activation_fee', '130.00', '2026-08-20'),
  entry(2, 1, 'payout', '1500.00', '2026-09-18', 'First payout', FULL_SPLIT('1500.00')),
  entry(3, 2, 'evaluation_fee', '167.00', '2026-07-30'),
  entry(4, 2, 'activation_fee', '130.00', '2026-08-12'),
  entry(5, 2, 'payout', '1650.00', '2026-09-22', '', FULL_SPLIT('1650.00')),
  entry(6, 3, 'evaluation_fee', '149.00', '2026-09-08'),
  entry(7, 4, 'evaluation_fee', '167.00', '2026-08-25'),
  entry(8, 4, 'reset_fee', '80.00', '2026-09-12', 'Reset after the CPI move'),
  entry(9, 5, 'evaluation_fee', '167.00', '2026-06-10'),
  entry(10, 6, 'evaluation_fee', '149.00', '2026-07-08'),
  entry(11, 7, 'evaluation_fee', '99.00', '2026-06-20'),
  entry(12, 8, 'evaluation_fee', '139.00', '2026-06-01'),
  entry(13, 9, 'evaluation_fee', '199.00', '2026-05-20'),
]

let accounts: AccountDetail[] = INITIAL_ACCOUNTS.map((a) => ({ ...a }))
let fees = new Map<number, FeeSchedule>(INITIAL_FEES.map(([id, schedule]) => [id, { ...schedule }]))
let ledger: LedgerEntry[] = INITIAL_LEDGER.map((e) => ({ ...e }))
let nextAccountId = 10
let nextEntryId = 14

/** Back to the generated state. For tests; a page reload does the same. */
export function resetDemoAccounts(): void {
  accounts = INITIAL_ACCOUNTS.map((a) => ({ ...a }))
  fees = new Map(INITIAL_FEES.map(([id, schedule]) => [id, { ...schedule }]))
  ledger = INITIAL_LEDGER.map((e) => ({ ...e }))
  nextAccountId = 10
  nextEntryId = 14
}

function find(id: number): AccountDetail {
  const account = accounts.find((a) => a.id === id)
  if (!account) throw new AccountRefused('That account does not exist.')
  return account
}

/** For the sample rules and the Accounts tab (`rules.ts`). */
export const allAccounts = (): readonly AccountDetail[] => accounts
export const accountById = (id: number): AccountDetail => find(id)
export const ledgerEntries = (): readonly LedgerEntry[] => ledger

function replace(next: AccountDetail): AccountDetail {
  accounts = accounts.map((a) => (a.id === next.id ? next : a))
  return next
}

// ---- progress ---------------------------------------------------------------------------------------------------

function progressOf(account: AccountDetail) {
  const trades = SAMPLE_TRADES.filter((t) => t.account.id === account.id).sort((a, b) =>
    a.exit_at < b.exit_at ? -1 : a.exit_at > b.exit_at ? 1 : 0,
  )
  let net = ZERO
  let peak = ZERO
  for (const trade of trades) {
    net = net.plus(trade.net_pnl)
    peak = Decimal.max(peak, net)
  }
  const target = account.profit_target ? new Decimal(account.profit_target) : null
  const allowed = account.max_drawdown ? new Decimal(account.max_drawdown) : null
  const used = peak.minus(net)
  return {
    net_pnl: net.toFixed(2),
    trade_count: trades.length,
    target_progress: target && target.greaterThan(0) ? Math.min(1, Math.max(0, net.div(target).toNumber())) : null,
    drawdown_left: allowed ? Decimal.max(ZERO, allowed.minus(used)).toFixed(2) : null,
    last_trading_day: trades.length ? trades.map((t) => t.trade_day).reduce((a, b) => (a > b ? a : b)) : null,
  }
}

const refOf = (a: AccountDetail): AccountRef => ({ id: a.id, name: a.name, firm: a.firm, account_type: a.account_type })

/** The accounts a person can import into: the active ones, in the order they were made. */
export function activeAccountRefs(): AccountRef[] {
  return accounts.filter((a) => a.outcome === null).map(refOf)
}

/** One account by id, archived or not (the import history names accounts that have since been archived). */
export function accountRef(id: number): AccountRef | undefined {
  const found = accounts.find((a) => a.id === id)
  return found ? refOf(found) : undefined
}

export function overviews(): AccountOverview[] {
  return accounts.map((account) => ({ account, progress: progressOf(account) }))
}

export function businessTotals(): BusinessTotals {
  let costs = ZERO
  let payouts = ZERO
  for (const e of ledger) {
    if (e.kind === 'payout') payouts = payouts.plus(e.amount)
    else costs = costs.plus(e.amount)
  }
  const evaluations = accounts.filter((a) => a.account_type === 'evaluation')
  const passed = evaluations.filter((a) => a.outcome === 'passed').length
  const failed = evaluations.filter((a) => a.outcome === 'failed').length
  const withCost = new Set(ledger.filter((e) => e.kind !== 'payout').map((e) => e.account_id))
  return {
    evaluations_passed: passed,
    evaluations_failed: failed,
    pass_rate: passed + failed > 0 ? passed / (passed + failed) : null,
    costs: costs.toFixed(2),
    payouts: payouts.toFixed(2),
    net: payouts.minus(costs).toFixed(2),
    accounts_without_cost: accounts.filter((a) => !withCost.has(a.id)).map((a) => a.name),
  }
}

// ---- accounts ---------------------------------------------------------------------------------------------------

/** The phase to store, as `accounts.check_phase`: the type's own phase, or live for a funded account. */
function checkPhase(input: AccountDetailsInput): Phase {
  const allowed = PHASES_BY_TYPE[input.account_type]
  if (input.phase !== null && !allowed.includes(input.phase)) {
    throw new AccountRefused(`The phase must be ${allowed.join(' or ')} for ${input.account_type} accounts.`)
  }
  return input.phase ?? input.account_type
}

/** As `accounts.check_purchase_date`: a real day, not in the future (a day of slack for time zones). */
function checkPurchaseDate(day: string | null): string | null {
  if (day === null) return null
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || Number.isNaN(Date.parse(day))) {
    throw new AccountRefused('Choose the purchase date.')
  }
  if (Date.parse(day) > Date.now() + 86_400_000) throw new AccountRefused('The purchase date cannot be in the future.')
  if (day < '2000-01-01') throw new AccountRefused('The purchase date is too long ago.')
  return day
}

function checkDetails(input: AccountDetailsInput, except?: number): string {
  const name = input.name.split(/\s+/).filter(Boolean).join(' ')
  if (!name) throw new AccountRefused('Give the account a name.')
  if (!(FIRMS as readonly string[]).includes(input.firm))
    throw new AccountRefused(`Choose a firm: ${FIRMS.join(', ')}.`)
  if (!(ACCOUNT_TYPES as readonly string[]).includes(input.account_type)) {
    throw new AccountRefused('Choose evaluation or funded.')
  }
  if (accounts.some((a) => a.id !== except && a.name.toLowerCase() === name.toLowerCase())) {
    throw new AccountRefused(`You already have an account called ${name}.`)
  }
  return name
}

export function createAccount(input: AccountDetailsInput): AccountDetail {
  const name = checkDetails(input)
  if (input.purchase_date === null) throw new AccountRefused('Choose the purchase date.')
  const purchase_date = checkPurchaseDate(input.purchase_date)
  const phase = checkPhase(input)
  const account = base({
    id: nextAccountId++,
    name,
    firm: input.firm,
    account_type: input.account_type,
    account_size: input.account_size,
    starting_balance: input.starting_balance,
    created_at: new Date().toISOString(),
    purchase_date,
    phase,
  })
  accounts = [...accounts, account]
  return account
}

export function updateAccount(id: number, input: AccountDetailsInput): AccountDetail {
  const current = find(id)
  const name = checkDetails(input, id)
  if (current.outcome && !OUTCOMES_BY_TYPE[input.account_type].includes(current.outcome)) {
    throw new AccountRefused(
      `A ${input.account_type} account cannot have the outcome ${OUTCOME_LABELS[current.outcome]}: restore it first.`,
    )
  }
  return replace({
    ...current,
    name,
    firm: input.firm,
    account_type: input.account_type,
    account_size: input.account_size,
    starting_balance: input.starting_balance,
    purchase_date: checkPurchaseDate(input.purchase_date),
    phase: checkPhase(input),
  })
}

export function updateRules(id: number, input: AccountRulesInput): AccountDetail {
  const current = find(id)
  if (input.trailing_type !== null && !TRAILING_TYPES.includes(input.trailing_type)) {
    throw new AccountRefused(`Trailing type must be one of ${TRAILING_TYPES.join(', ')}.`)
  }
  if (input.trail_lock_enabled && input.trail_lock_level === null) {
    throw new AccountRefused('Give the level at which the trailing stops, or switch the lock off.')
  }
  return replace({ ...current, ...input, trail_lock_level: input.trail_lock_enabled ? input.trail_lock_level : null })
}

export function archiveAccount(id: number, input: ArchiveInput): AccountDetail {
  const current = find(id)
  const allowed = OUTCOMES_BY_TYPE[current.account_type]
  if (!allowed.includes(input.outcome)) {
    throw new AccountRefused(
      `A ${current.account_type} account ends as ${allowed.map((o) => OUTCOME_LABELS[o]).join(' or ')}.`,
    )
  }
  return replace({ ...current, outcome: input.outcome, ended_on: input.ended_on, archive_note: input.note.trim() })
}

export function restoreAccount(id: number): AccountDetail {
  return replace({ ...find(id), outcome: null, ended_on: null, archive_note: '' })
}

export function dismissPassPrompt(id: number): AccountDetail {
  const current = find(id)
  return replace({ ...current, pass_prompt_dismissed_at: current.pass_prompt_dismissed_at ?? new Date().toISOString() })
}

// ---- fees -------------------------------------------------------------------------------------------------------

export function feesOf(id: number): FeeSchedule {
  find(id)
  return { ...(fees.get(id) ?? {}) }
}

export function setFee(id: number, symbolRoot: string, rate: string): void {
  find(id)
  if (new Decimal(rate).isNegative()) throw new AccountRefused('A fee cannot be negative.')
  const root = symbolRoot.trim().toUpperCase() || '*'
  fees.set(id, { ...(fees.get(id) ?? {}), [root]: new Decimal(rate).toFixed(2) })
}

export function removeFee(id: number, symbolRoot: string): void {
  find(id)
  const root = symbolRoot.trim().toUpperCase() || '*'
  fees.set(id, Object.fromEntries(Object.entries(fees.get(id) ?? {}).filter(([key]) => key !== root)))
}

// ---- ledger -----------------------------------------------------------------------------------------------------

export function ledgerOf(id: number): LedgerEntry[] {
  find(id)
  return ledger
    .filter((e) => e.account_id === id)
    .sort((a, b) => (a.entry_date < b.entry_date ? 1 : a.entry_date > b.entry_date ? -1 : b.id - a.id))
}

export function addEntry(id: number, input: NewLedgerEntry): LedgerEntry {
  find(id)
  if (!(input.kind in LEDGER_KINDS)) throw new AccountRefused('Choose what the money was for.')
  if (!new Decimal(input.amount).greaterThan(0)) throw new AccountRefused('The amount must be more than zero.')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.entry_date)) throw new AccountRefused('Choose a date.')
  const created = entry(nextEntryId++, id, input.kind, input.amount, input.entry_date, input.note.trim())
  ledger = [...ledger, created]
  renumberPayouts(id)
  return ledger.find((e) => e.id === created.id) ?? created
}

/** As `pg/ledger.add_payout`: the gross, the split (the net is gross x split, half up), and a fee. */
export function addPayoutEntry(
  id: number,
  input: { entry_date: string; gross: string; split_pct: string; fee: string; note: string },
): LedgerEntry {
  find(id)
  const gross = new Decimal(input.gross).toDecimalPlaces(2, Decimal.ROUND_HALF_UP)
  const split = new Decimal(input.split_pct).toDecimalPlaces(2, Decimal.ROUND_HALF_UP)
  const fee = new Decimal(input.fee).toDecimalPlaces(2, Decimal.ROUND_HALF_UP)
  if (!gross.greaterThan(0)) throw new AccountRefused('The gross amount must be more than zero.')
  if (!split.greaterThan(0) || split.greaterThan(100)) {
    throw new AccountRefused('The split must be more than 0% and at most 100%.')
  }
  if (fee.isNegative()) throw new AccountRefused('The fee cannot be negative.')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.entry_date)) throw new AccountRefused('Choose a date.')
  const net = gross.times(split).div(100).toDecimalPlaces(2, Decimal.ROUND_HALF_UP)
  if (!net.greaterThan(0)) throw new AccountRefused('The net amount (gross x split) must be at least one cent.')
  const created = entry(nextEntryId++, id, 'payout', net.toFixed(2), input.entry_date, input.note.trim(), {
    payout_number: null,
    gross_amount: gross.toFixed(2),
    profit_split_pct: split.toFixed(2),
    payout_fee: fee.toFixed(2),
  })
  ledger = [...ledger, created]
  renumberPayouts(id)
  return ledger.find((e) => e.id === created.id) ?? created
}

/** Payouts are numbered 1, 2, 3... by date, then by when they were logged. */
function renumberPayouts(id: number): void {
  const ordered = ledger
    .filter((e) => e.account_id === id && e.kind === 'payout')
    .sort((a, b) => a.entry_date.localeCompare(b.entry_date) || a.id - b.id)
  const numbers = new Map(ordered.map((e, n) => [e.id, n + 1]))
  ledger = ledger.map((e) => (numbers.has(e.id) ? { ...e, payout_number: numbers.get(e.id) ?? null } : e))
}

export function deleteEntry(id: number, entryId: number): void {
  find(id)
  if (!ledger.some((e) => e.id === entryId && e.account_id === id))
    throw new AccountRefused('That entry does not exist.')
  ledger = ledger.filter((e) => e.id !== entryId)
  renumberPayouts(id)
}
