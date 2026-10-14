import Decimal from 'decimal.js'
import { afterEach, describe, expect, it } from 'vitest'
import { AccountRefused, type AccountDetailsInput } from '../data/model'
import { SAMPLE_TRADES } from './generate'
import { demoSource } from './source'
import { accountRef, activeAccountRefs, resetDemoAccounts } from './accounts'

afterEach(resetDemoAccounts)

const details = (over: Partial<AccountDetailsInput> = {}): AccountDetailsInput => ({
  name: 'Tradeify 100K',
  firm: 'Tradeify',
  account_type: 'evaluation',
  account_size: '100000.00',
  starting_balance: '100000.00',
  purchase_date: '2026-09-01',
  phase: null,
  ...over,
})

const rules = {
  profit_target: '3000.00',
  max_drawdown: '2500.00',
  trailing_type: 'eod' as const,
  trail_lock_enabled: false,
  trail_lock_level: null,
  daily_loss_limit: null,
}

async function overview(id: number) {
  const found = (await demoSource.accountOverviews()).find((o) => o.account.id === id)
  if (!found) throw new Error(`no account ${id}`)
  return found
}

describe('accounts and their progress', () => {
  it('lists the accounts of the mockup and the archived ones behind them', async () => {
    const all = await demoSource.accountOverviews()
    expect(all.map((o) => o.account.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(all.filter((o) => o.account.outcome === null).map((o) => o.account.name)).toEqual([
      'Apex 50K #1',
      'Apex 150K',
      'Lucid 100K',
      'Apex 50K #2',
    ])
    expect(all.filter((o) => o.account.outcome !== null)).toHaveLength(5)
  })

  it('takes each account’s result from its own trades, exactly', async () => {
    for (const id of [1, 2, 3, 4]) {
      const trades = SAMPLE_TRADES.filter((t) => t.account.id === id)
      const net = trades.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2)
      const { progress } = await overview(id)
      expect(progress.net_pnl).toBe(net)
      expect(progress.trade_count).toBe(trades.length)
      expect(progress.last_trading_day).toBe(
        trades
          .map((t) => t.trade_day)
          .sort()
          .at(-1),
      )
    }
  })

  it('shows progress to the profit target as a share, never past the ends', async () => {
    const { account, progress } = await overview(4)
    const net = new Decimal(progress.net_pnl)
    const expected = Math.min(1, Math.max(0, net.div(account.profit_target ?? '1').toNumber()))
    expect(progress.target_progress).toBeCloseTo(expected)
    expect((await overview(1)).progress.target_progress).toBeNull() // no target on a funded account
  })

  it('shows what is left of the drawdown: the allowance minus how far the account sits below its best', async () => {
    const trades = SAMPLE_TRADES.filter((t) => t.account.id === 2).sort((a, b) => (a.exit_at < b.exit_at ? -1 : 1))
    let net = new Decimal(0)
    let peak = new Decimal(0)
    for (const t of trades) {
      net = net.plus(t.net_pnl)
      peak = Decimal.max(peak, net)
    }
    const expected = Decimal.max(0, new Decimal('5000').minus(peak.minus(net))).toFixed(2)
    expect((await overview(2)).progress.drawdown_left).toBe(expected)
    expect((await overview(5)).progress.drawdown_left).toBeNull() // an account with no limit
  })

  it('gives an archived account with no trades a zero result', async () => {
    expect((await overview(5)).progress).toMatchObject({ net_pnl: '0.00', trade_count: 0, last_trading_day: null })
  })
})

describe('the prop business', () => {
  it('counts passed and failed evaluations for a pass rate, and adds up money in and out', async () => {
    const totals = await demoSource.businessTotals()
    expect(totals).toMatchObject({ evaluations_passed: 3, evaluations_failed: 2, payouts: '3150.00' })
    expect(totals.pass_rate).toBeCloseTo(0.6)
    expect(new Decimal(totals.payouts).minus(totals.costs).toFixed(2)).toBe(totals.net)
    expect(totals.accounts_without_cost).toEqual([])
  })

  it('does not count a closed evaluation or a funded account in the pass rate', async () => {
    await demoSource.archiveAccount(3, { outcome: 'closed', ended_on: '2026-10-01', note: '' })
    await demoSource.archiveAccount(1, { outcome: 'failed', ended_on: '2026-10-01', note: '' })
    const totals = await demoSource.businessTotals()
    expect(totals).toMatchObject({ evaluations_passed: 3, evaluations_failed: 2 })
  })

  it('has no pass rate until an evaluation has been decided', async () => {
    for (const id of [5, 6, 7, 8, 9]) await demoSource.restoreAccount(id)
    expect((await demoSource.businessTotals()).pass_rate).toBeNull()
  })

  it('names the accounts that have no cost recorded', async () => {
    for (const entry of await demoSource.ledger(6)) await demoSource.deleteLedgerEntry(6, entry.id)
    expect((await demoSource.businessTotals()).accounts_without_cost).toEqual(['Lucid 50K'])
  })
})

describe('adding and editing an account', () => {
  it('adds an account, tidying its name', async () => {
    const created = await demoSource.createAccount(details({ name: '  Tradeify   100K ' }))
    expect(created).toMatchObject({ name: 'Tradeify 100K', firm: 'Tradeify', outcome: null, profit_target: null })
    expect((await demoSource.accountOverviews()).at(-1)?.account.id).toBe(created.id)
  })

  it.each([
    [{ name: '   ' }, 'Give the account a name.'],
    [
      { firm: 'Nowhere Capital' },
      'Choose a firm: Apex, Bulenox, Lucid, My Funded Futures, Take Profit, Tradeify, Other.',
    ],
    [{ account_type: 'demo' as never }, 'Choose evaluation or funded.'],
    [{ name: 'apex 150k' }, 'You already have an account called apex 150k.'],
  ] as [Partial<AccountDetailsInput>, string][])('refuses %j in words', async (over, message) => {
    await expect(demoSource.createAccount(details(over))).rejects.toThrow(message)
  })

  it('changes an account’s details and keeps its rules', async () => {
    const updated = await demoSource.updateAccount(4, details({ name: 'Apex 50K #2 (reset)', firm: 'Apex' }))
    expect(updated).toMatchObject({
      id: 4,
      name: 'Apex 50K #2 (reset)',
      profit_target: '3000.00',
      max_drawdown: '2500.00',
    })
  })

  it('lets an account keep its own name when other details change', async () => {
    await expect(demoSource.updateAccount(4, details({ name: 'Apex 50K #2' }))).resolves.toMatchObject({ id: 4 })
  })

  it('will not make an account a type that cannot have its outcome', async () => {
    // Lucid 50K passed as an evaluation; a funded account cannot have passed.
    await expect(
      demoSource.updateAccount(6, details({ name: 'Lucid 50K', firm: 'Lucid', account_type: 'funded' })),
    ).rejects.toThrow('A funded account cannot have the outcome Passed: restore it first.')
  })

  it('refuses to change an account that does not exist', async () => {
    await expect(demoSource.updateAccount(999, details())).rejects.toThrow('That account does not exist.')
  })

  it('offers only active accounts to import into, in the order they were made, including a new one', async () => {
    const created = await demoSource.createAccount(details())
    expect((await demoSource.accounts()).map((a) => a.name)).toEqual([
      'Apex 50K #1',
      'Apex 150K',
      'Lucid 100K',
      'Apex 50K #2',
      'Tradeify 100K',
    ])
    await demoSource.archiveAccount(created.id, { outcome: 'closed', ended_on: '2026-10-02', note: '' })
    expect(activeAccountRefs().map((a) => a.id)).not.toContain(created.id)
    expect(accountRef(created.id)?.name).toBe('Tradeify 100K') // still found by id, for the history
    expect(accountRef(12345)).toBeUndefined()
  })
})

describe('rules', () => {
  it('saves the firm’s rules', async () => {
    const updated = await demoSource.updateRules(1, {
      ...rules,
      trailing_type: 'intraday',
      daily_loss_limit: '1000.00',
    })
    expect(updated).toMatchObject({ profit_target: '3000.00', trailing_type: 'intraday', daily_loss_limit: '1000.00' })
    expect((await overview(1)).progress.target_progress).not.toBeNull() // progress follows the new rule
  })

  it('needs a level when the trailing stops at one, and forgets the level when it does not', async () => {
    await expect(
      demoSource.updateRules(1, { ...rules, trail_lock_enabled: true, trail_lock_level: null }),
    ).rejects.toThrow('Give the level at which the trailing stops')
    const locked = await demoSource.updateRules(1, { ...rules, trail_lock_enabled: true, trail_lock_level: '52600.00' })
    expect(locked.trail_lock_level).toBe('52600.00')
    const unlocked = await demoSource.updateRules(1, {
      ...rules,
      trail_lock_enabled: false,
      trail_lock_level: '52600.00',
    })
    expect(unlocked).toMatchObject({ trail_lock_enabled: false, trail_lock_level: null })
  })
})

describe('archiving', () => {
  it('archives an evaluation as passed, with the end date and a trimmed note', async () => {
    const archived = await demoSource.archiveAccount(4, {
      outcome: 'passed',
      ended_on: '2026-10-02',
      note: '  Took the payout rule seriously ',
    })
    expect(archived).toMatchObject({
      outcome: 'passed',
      ended_on: '2026-10-02',
      archive_note: 'Took the payout rule seriously',
    })
    expect((await demoSource.businessTotals()).evaluations_passed).toBe(4)
  })

  it('lets a funded account end only as failed or closed', async () => {
    await expect(demoSource.archiveAccount(1, { outcome: 'passed', ended_on: '2026-10-02', note: '' })).rejects.toThrow(
      'A funded account ends as Failed or Closed.',
    )
    await expect(
      demoSource.archiveAccount(1, { outcome: 'failed', ended_on: '2026-10-02', note: '' }),
    ).resolves.toMatchObject({ outcome: 'failed' })
  })

  it('restores an account, clearing how it ended', async () => {
    const restored = await demoSource.restoreAccount(5)
    expect(restored).toMatchObject({ outcome: null, ended_on: null, archive_note: '' })
    expect(activeAccountRefs().map((a) => a.id)).toContain(5)
  })
})

describe('fees', () => {
  it('starts with the fees the sample trades were built on', async () => {
    expect(await demoSource.fees(1)).toEqual({
      NQ: '2.20',
      MNQ: '0.74',
      ES: '2.20',
      GC: '2.50',
      CL: '2.50',
      '*': '2.50',
    })
    expect(await demoSource.fees(5)).toEqual({}) // an account with none set
  })

  it('sets a fee by contract, in capitals, in cents, and a blank contract means any other', async () => {
    await demoSource.setFee(5, ' nq ', '2.2')
    await demoSource.setFee(5, '', '3')
    expect(await demoSource.fees(5)).toEqual({ NQ: '2.20', '*': '3.00' })
    await demoSource.setFee(5, 'NQ', '2.5')
    expect((await demoSource.fees(5))['NQ']).toBe('2.50')
  })

  it('refuses a negative fee, and removes one', async () => {
    await expect(demoSource.setFee(1, 'NQ', '-1')).rejects.toThrow('A fee cannot be negative.')
    await demoSource.removeFee(1, 'cl')
    expect(await demoSource.fees(1)).not.toHaveProperty('CL')
    await demoSource.removeFee(1, '')
    expect(await demoSource.fees(1)).not.toHaveProperty('*')
  })

  it('refuses fees for an account that does not exist', async () => {
    await expect(demoSource.fees(999)).rejects.toThrow('That account does not exist.')
  })
})

describe('the ledger', () => {
  it('lists an account’s entries newest first', async () => {
    const entries = await demoSource.ledger(2)
    expect(entries.map((e) => e.kind)).toEqual(['payout', 'activation_fee', 'evaluation_fee'])
    expect(entries.map((e) => e.entry_date)).toEqual(['2026-09-22', '2026-08-12', '2026-07-30'])
  })

  it('adds an entry, trimming its note, putting it in order, and counting it in the business', async () => {
    const before = new Decimal((await demoSource.businessTotals()).costs)
    const added = await demoSource.addLedgerEntry(4, {
      kind: 'reset_fee',
      amount: '80.00',
      entry_date: '2026-10-01',
      note: '  second reset ',
    })
    expect(added).toMatchObject({ account_id: 4, kind: 'reset_fee', amount: '80.00', note: 'second reset' })
    expect((await demoSource.ledger(4))[0]?.id).toBe(added.id)
    expect((await demoSource.businessTotals()).costs).toBe(before.plus('80.00').toFixed(2))
  })

  it('refuses an amount that is not more than zero, and a missing date', async () => {
    await expect(
      demoSource.addLedgerEntry(4, { kind: 'payout', amount: '0.00', entry_date: '2026-10-01', note: '' }),
    ).rejects.toThrow('The amount must be more than zero.')
    await expect(
      demoSource.addLedgerEntry(4, { kind: 'payout', amount: '5.00', entry_date: '', note: '' }),
    ).rejects.toThrow('Choose a date.')
    await expect(
      demoSource.addLedgerEntry(4, { kind: 'bribe' as never, amount: '5.00', entry_date: '2026-10-01', note: '' }),
    ).rejects.toThrow('Choose what the money was for.')
  })

  it('deletes an entry, but only from its own account', async () => {
    const [first] = await demoSource.ledger(2)
    await expect(demoSource.deleteLedgerEntry(1, first?.id ?? 0)).rejects.toThrow('That entry does not exist.')
    await demoSource.deleteLedgerEntry(2, first?.id ?? 0)
    expect(await demoSource.ledger(2)).toHaveLength(2)
  })

  it('keeps money in and out apart from trading results', async () => {
    const before = (await overview(4)).progress.net_pnl
    await demoSource.addLedgerEntry(4, { kind: 'reset_fee', amount: '80.00', entry_date: '2026-10-01', note: '' })
    expect((await overview(4)).progress.net_pnl).toBe(before)
  })

  it('is an AccountRefused error, so a page can tell a refusal from a crash', async () => {
    await expect(demoSource.fees(999)).rejects.toBeInstanceOf(AccountRefused)
  })
})
