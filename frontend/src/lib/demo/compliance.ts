import Decimal from 'decimal.js'
import type {
  AccountStatus,
  InactivityStatus,
  PayoutReadiness,
  Phase,
  RuleResult,
  RuleStatus,
  RuleValues,
  Spectrum,
} from '../data/model'
import { formatMoney } from '../money'

/*
 * The compliance and payout engine for the SAMPLE DATA only: a small port of the main paths of the backend's
 * `compliance.py` (drawdown floor, daily loss, minimum and qualifying days, consistency, profit target, the payout gates,
 * withdrawable, the spectrum and the status), so the Accounts tab looks real in `pnpm dev`. The server's engine is the
 * one that counts: this file is never used once the app talks to the API, and it skips what the sample data does not
 * need (contracts, flat time, inactivity). Its tests repeat a few of the backend's worked examples to keep the two close.
 */

const ZERO = new Decimal(0)
const HUNDRED = new Decimal(100)
const AT_RISK_SHARE = new Decimal('0.25')

export interface SampleDay {
  day: string // "YYYY-MM-DD", the firm's trading day
  net: Decimal
}

export interface SamplePayout {
  day: string
  gross: string | null // null for a payout logged before the gross was kept
  net: string
}

export interface SampleEvaluation {
  status: AccountStatus
  balance: Decimal
  net_pnl: Decimal
  rules: RuleResult[]
  spectrum: Spectrum
  payout: PayoutReadiness | null
  consistency_headroom: Decimal | null
  inactivity: InactivityStatus | null
  floor: Decimal | null
  progress: string
}

const money = (value: Decimal) => formatMoney(value.toFixed(2))

function pct(value: Decimal): string {
  const rounded = value.toDecimalPlaces(1)
  return `${rounded.isInteger() ? rounded.toFixed(0) : rounded.toString()}%`
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

function dec(values: RuleValues, name: string): Decimal | null {
  const value = values[name]
  return value === null || value === undefined || value === '' ? null : new Decimal(value)
}

function text(values: RuleValues, name: string): string | null {
  const value = values[name]
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function int(values: RuleValues, name: string): number | null {
  const value = values[name]
  return typeof value === 'number' ? value : null
}

// ---- the descriptive columns ----------------------------------------------------------------------------------------

function lockLevel(raw: string | null, start: Decimal): Decimal | null {
  if (!raw) return null
  const value = raw.trim().toLowerCase()
  if (/^\d+(\.\d+)?$/.test(value)) return new Decimal(value)
  if (value.startsWith('start_balance_plus_100')) return start.plus(100)
  if (value.startsWith('start_balance')) return start
  return null // "none" or text this does not read: the floor keeps trailing (higher, so safer)
}

type AfterPayout = 'unchanged' | 'lock_start' | 'lock_start_plus_100' | 'reset_zero'

function afterPayout(raw: string | null): AfterPayout {
  const value = (raw ?? '').trim().toLowerCase()
  if (value.startsWith('locks_at_start_plus_100')) return 'lock_start_plus_100'
  if (value.startsWith('locks_at_start')) return 'lock_start'
  if (value.startsWith('resets_to_zero')) return 'reset_zero'
  return 'unchanged'
}

/** The cap for payout number `n`: an amount, "uncapped", or not known. */
function capFor(schedule: string | null, n: number): Decimal | 'uncapped' | null {
  if (!schedule) return null
  const value = schedule.trim().toLowerCase()
  if (value.startsWith('uncapped')) return 'uncapped'
  for (const part of value.split('|')) {
    const match = /^\s*(all|\d+\+?)\s*:\s*(\d+(?:\.\d+)?)\s*$/.exec(part)
    if (!match) continue
    const [, spec = '', amount = '0'] = match
    const covers = spec === 'all' || (spec.endsWith('+') ? n >= Number(spec.slice(0, -1)) : n === Number(spec))
    if (covers) return new Decimal(amount)
  }
  return null
}

function percentageCap(raw: string | null): [Decimal, 'profit' | 'cycle profit' | 'balance'] | null {
  const match = /(\d+(?:\.\d+)?)%\s+of\s+(cycle profit|profit|balance)/i.exec(raw ?? '')
  if (!match) return null
  return [new Decimal(match[1] ?? '0'), (match[2] ?? 'profit').toLowerCase() as 'profit' | 'cycle profit' | 'balance']
}

/** The trader's share in percent ("90/10" is 90); the lowest one the text promises; "up to" promises nothing. */
export function splitShare(raw: string | null): Decimal | null {
  if (!raw || raw.trim().toLowerCase().startsWith('up to')) return null
  const value = raw.toLowerCase().replace(/\d+(?:\.\d+)?% processing fee/g, '')
  const shares: Decimal[] = []
  for (const [, a = '0', b = '0'] of value.matchAll(/(\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)/g)) {
    if (new Decimal(a).plus(b).equals(100)) shares.push(new Decimal(a))
  }
  for (const [, n = '0'] of value.matchAll(/(\d+(?:\.\d+)?)%/g)) shares.push(new Decimal(n))
  const valid = shares.filter((s) => s.greaterThan(0) && s.lessThanOrEqualTo(100))
  return valid.length ? Decimal.min(...valid) : null
}

const daysBetween = (from: string, to: string) => Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000)

// ---- the engine -------------------------------------------------------------------------------------------------------

export function evaluateSample(input: {
  values: RuleValues
  start: string
  phase: Phase
  days: SampleDay[]
  payouts: SamplePayout[]
  today: string
}): SampleEvaluation {
  const r = input.values
  const start = new Decimal(input.start)
  const funded = input.phase !== 'evaluation'
  const days = [...input.days].sort((a, b) => a.day.localeCompare(b.day))
  const payouts = [...input.payouts].sort((a, b) => a.day.localeCompare(b.day))
  const withdrawnOf = (p: SamplePayout) => new Decimal(p.gross ?? p.net)
  const lastPayout = payouts.length ? (payouts[payouts.length - 1]?.day ?? null) : null
  const netPnl = days.reduce((sum, d) => sum.plus(d.net), ZERO)
  const withdrawn = payouts.reduce((sum, p) => sum.plus(withdrawnOf(p)), ZERO)
  const balance = start.plus(netPnl).minus(withdrawn)
  const cycle = days.filter((d) => lastPayout === null || d.day > lastPayout)
  const cycleProfit = cycle.reduce((sum, d) => sum.plus(d.net), ZERO)
  const scope = funded ? cycle : days
  const target = dec(r, 'profit_target_usd')
  const results = new Map<string, RuleResult>()
  const atRisk: string[] = []
  let breached = false
  let floor: Decimal | null = null
  let headroom: Decimal | null = null
  let effectiveTarget: Decimal | null = null

  const add = (key: string, group: string, label: string, status: RuleStatus, more: Partial<RuleResult> = {}) => {
    results.set(key, { key, group, label, status, current: null, limit: null, message: '', ...more })
  }

  // Drawdown: a breach is a closed trade's balance at or below the floor (here: a day's close, the sample has no more).
  {
    const kind = text(r, 'drawdown_type')
    const maxLoss = dec(r, 'max_loss_usd')
    const label = 'Max loss (drawdown)'
    if (!kind || maxLoss === null) {
      add('drawdown', 'Drawdown', label, 'n/a', { message: 'No drawdown rule is set.' })
    } else {
      const initial = start.minus(maxLoss)
      const lock = kind === 'static' ? null : lockLevel(text(r, 'drawdown_lock_level'), start)
      const mode = afterPayout(text(r, 'drawdown_after_payout'))
      const trailed = (mark: Decimal) => {
        let level = mark.minus(maxLoss)
        if (lock !== null && level.greaterThan(lock)) level = Decimal.max(lock, initial)
        return Decimal.max(level, initial)
      }
      let level = initial
      let frozen = false
      let mark = start
      let running = start
      let paid = ZERO
      let breach: string | null = null
      const allDays = [...new Set([...days.map((d) => d.day), ...payouts.map((p) => p.day)])].sort()
      for (const day of allDays) {
        for (const p of payouts.filter((x) => x.day === day)) {
          running = running.minus(withdrawnOf(p))
          paid = paid.plus(withdrawnOf(p))
          if (!frozen && mode !== 'unchanged') {
            level = { lock_start: start, lock_start_plus_100: start.plus(100), reset_zero: ZERO }[mode]
            frozen = true
          }
        }
        const today = days.find((d) => d.day === day)
        if (today) running = running.plus(today.net)
        if (breach === null && today && running.lessThanOrEqualTo(level)) breach = day
        if (kind !== 'static' && !frozen && day < input.today) {
          mark = Decimal.max(mark, running.plus(paid))
          level = trailed(mark)
        }
      }
      floor = level
      const room = balance.minus(level)
      const details = { current: money(balance), limit: money(level) }
      if (breach !== null) {
        breached = true
        add('drawdown', 'Drawdown', label, 'fail', { ...details, message: `Breached on ${breach}.` })
      } else {
        if (room.lessThanOrEqualTo(maxLoss.times(AT_RISK_SHARE))) atRisk.push(`${money(room)} above the floor`)
        const exact = kind === 'eod_trailing' || kind === 'static'
        add('drawdown', 'Drawdown', label, exact ? 'pass' : 'not_verifiable', {
          ...details,
          message: exact
            ? `${money(room)} above the floor.`
            : `No breach in closed trades; ${money(room)} above the floor. The firm checks it during the day.`,
        })
      }
    }
  }

  // Daily loss: today's result against the limit.
  let dailyFloor: Decimal | null = null
  {
    const kind = text(r, 'daily_loss_type')
    const limit = dec(r, 'daily_loss_limit_usd')
    const label = 'Daily loss limit'
    if (!kind || kind === 'none' || limit === null) {
      add('daily_loss', 'Daily loss', label, 'n/a', { message: 'No daily loss limit applies.' })
    } else {
      const todayNet = days.find((d) => d.day === input.today)?.net ?? ZERO
      dailyFloor = balance.minus(todayNet).minus(limit)
      const hit = todayNet.lessThanOrEqualTo(limit.negated())
      const details = { current: money(todayNet), limit: money(limit.negated()) }
      if (hit) {
        if (kind === 'hard') breached = true
        else atRisk.push('the daily loss limit was hit today')
        add('daily_loss', 'Daily loss', label, 'fail', { ...details, message: 'Hit today.' })
      } else {
        if (todayNet.negated().greaterThanOrEqualTo(limit.times(1 - AT_RISK_SHARE.toNumber())))
          atRisk.push('close to the daily loss limit')
        const exact = text(r, 'daily_loss_basis') === 'realized'
        add('daily_loss', 'Daily loss', label, exact ? 'pass' : 'not_verifiable', {
          ...details,
          message: exact ? 'Not hit today.' : 'Not hit in closed trades; open losses may count.',
        })
      }
    }
  }

  // Minimum trading days, counted since the last payout on a funded account.
  {
    const needed = int(r, 'min_trading_days')
    const label = 'Minimum trading days'
    if (!needed) add('min_trading_days', 'Qualifying days', label, 'n/a', { message: 'No minimum is set.' })
    else
      add('min_trading_days', 'Qualifying days', label, scope.length >= needed ? 'pass' : 'fail', {
        current: String(scope.length),
        limit: String(needed),
        message: `${plural(scope.length, 'trading day')} so far.`,
      })
  }

  // Consistency: strict ("less than"), on the bases the sample uses.
  let consistencyNumbers: [Decimal, Decimal] | null = null
  {
    const basis = text(r, 'consistency_basis')
    const share = dec(r, 'consistency_pct')
    const label = 'Consistency'
    const best = (list: SampleDay[]) => list.reduce((m, d) => Decimal.max(m, d.net), ZERO)
    const total = netPnl
    const numbers: Record<string, [Decimal, Decimal]> = {
      best_day_vs_total_profit: [best(days), total],
      best_day_vs_cycle_profit: [best(cycle), cycleProfit],
      best_day_vs_profit_target: [best(days), Decimal.max(target ?? ZERO, total)],
      best_day_vs_positive_days_sum: [
        best(days),
        days.filter((d) => d.net.greaterThan(0)).reduce((s, d) => s.plus(d.net), ZERO),
      ],
    }
    const found = basis && share !== null ? numbers[basis] : undefined
    if (!found || share === null) {
      add('consistency', 'Consistency', label, 'n/a', { message: 'No consistency rule is set.' })
    } else {
      const [top, against] = found
      consistencyNumbers = found
      const limit = share.div(HUNDRED)
      if (against.lessThanOrEqualTo(0)) {
        add('consistency', 'Consistency', label, 'fail', {
          current: money(top),
          limit: pct(share),
          message: `Needs a net profit first (it is ${money(against)}).`,
        })
      } else {
        const ratio = top.div(against).times(HUNDRED)
        if (top.lessThan(limit.times(against))) {
          // The largest day tomorrow that keeps it passing: x < limit x (against + x), so x < limit x against / (1 - limit).
          if (limit.lessThan(1)) {
            const bound = limit.times(against).div(new Decimal(1).minus(limit))
            headroom = Decimal.max(top, bound.minus('0.01')).toDecimalPlaces(2, Decimal.ROUND_DOWN)
          }
          add('consistency', 'Consistency', label, 'pass', {
            current: pct(ratio),
            limit: `under ${pct(share)}`,
            message: `Best day ${money(top)} is ${pct(ratio)} of ${money(against)}.`,
          })
        } else {
          add('consistency', 'Consistency', label, 'fail', {
            current: pct(ratio),
            limit: `under ${pct(share)}`,
            message: `Best day ${money(top)} is ${pct(ratio)} of ${money(against)}.`,
          })
        }
      }
    }
  }

  // Qualifying days: a day of at least the minimum (or any profit), since the last payout on a funded account.
  const minimumDay = dec(r, 'qualifying_day_min_profit_usd')
  const qualifies = (d: SampleDay) =>
    minimumDay !== null ? d.net.greaterThanOrEqualTo(minimumDay) : d.net.greaterThan(0)
  {
    const needed = int(r, 'qualifying_days_required')
    const label = 'Qualifying days'
    if (!needed)
      add('qualifying_days', 'Qualifying days', label, 'n/a', { message: 'No qualifying days are required.' })
    else {
      const counted = scope.filter(qualifies).length
      add('qualifying_days', 'Qualifying days', label, counted >= needed ? 'pass' : 'fail', {
        current: String(counted),
        limit: String(needed),
        message: `${counted} of ${needed} ${minimumDay !== null ? `days of ${money(minimumDay)}+` : 'profitable days'}.`,
      })
    }
  }

  // Profit target (evaluation) or the profit goal for each payout (funded).
  {
    const label = funded ? 'Profit goal for this payout' : 'Profit target'
    if (target === null) add('profit_target', 'Pass / target', label, 'n/a', { message: 'No target is set.' })
    else {
      const made = funded ? cycleProfit : balance.minus(start)
      if (!funded) effectiveTarget = target
      add('profit_target', 'Pass / target', label, made.greaterThanOrEqualTo(target) ? 'pass' : 'fail', {
        current: money(made),
        limit: money(target),
        message: made.greaterThanOrEqualTo(target) ? 'Target reached.' : `${money(target.minus(made))} to go.`,
      })
    }
  }

  add('inactivity', 'Inactivity', 'Inactivity', 'n/a', {
    message: text(r, 'inactivity_rule') ? 'The sample data does not check this rule.' : 'No inactivity rule is set.',
  })

  // Payout readiness (funded and live accounts).
  let payout: PayoutReadiness | null = null
  const buffer = dec(r, 'payout_buffer_usd') ?? ZERO
  const minPayout = dec(r, 'min_payout_usd')
  const minBalance = dec(r, 'min_balance_to_request_usd')
  const readyBalance = () => {
    let level = start.plus(buffer).plus(minPayout ?? ZERO)
    if (minBalance !== null) level = Decimal.max(level, minBalance)
    if (target !== null) level = Decimal.max(level, balance.minus(cycleProfit).plus(target))
    return level
  }
  if (funded) {
    const group = 'Payout'
    const next = payouts.length + 1
    const aboveBuffer = balance.minus(start.plus(buffer))
    if (minBalance === null) add('min_balance', group, 'Minimum balance to request', 'n/a', { message: 'Not set.' })
    else
      add(
        'min_balance',
        group,
        'Minimum balance to request',
        balance.greaterThanOrEqualTo(minBalance) ? 'pass' : 'fail',
        {
          current: money(balance),
          limit: money(minBalance),
        },
      )
    add('buffer', group, 'Above start + buffer', aboveBuffer.greaterThan(0) ? 'pass' : 'fail', {
      current: money(balance),
      limit: money(start.plus(buffer)),
    })
    let cap = capFor(text(r, 'payout_cap_schedule'), next)
    const maxPayout = dec(r, 'max_payout_usd')
    if (cap === null && maxPayout !== null) cap = maxPayout
    if (cap instanceof Decimal) add('payout_cap', group, `Cap for payout #${next}`, 'pass', { limit: money(cap) })
    else if (cap === 'uncapped') add('payout_cap', group, `Cap for payout #${next}`, 'n/a', { message: 'Uncapped.' })
    else add('payout_cap', group, `Cap for payout #${next}`, 'not_verifiable', { message: 'No cap is set.' })

    const cadence = text(r, 'payout_frequency')?.toLowerCase() ?? null
    const everyDays = cadence ? /every (\d+) (?:new )?(?:qualifying|winning) days/.exec(cadence) : null
    const weekly = cadence ? /weekly|every 7 (?:calendar )?days/.test(cadence) : false
    if (!cadence) add('payout_cadence', group, 'Payout cadence', 'n/a', { message: 'No cadence is set.' })
    else if (everyDays || weekly) {
      const enough = !everyDays || cycle.filter(qualifies).length >= Number(everyDays[1])
      const spaced = !weekly || lastPayout === null || daysBetween(lastPayout, input.today) >= 7
      add('payout_cadence', group, 'Payout cadence', enough && spaced ? 'pass' : 'fail', { message: cadence })
    } else add('payout_cadence', group, 'Payout cadence', 'not_verifiable', { message: cadence })

    const allowed = text(r, 'payout_days_allowed')?.toLowerCase() ?? null
    if (!allowed) add('payout_days', group, 'Days a request is allowed', 'n/a', { message: 'Any day.' })
    else if (allowed.startsWith('any'))
      add('payout_days', group, 'Days a request is allowed', 'pass', { message: allowed })
    else add('payout_days', group, 'Days a request is allowed', 'not_verifiable', { message: allowed })

    const limitText = text(r, 'max_payouts_before_live_or_close')
    const limitCount = limitText ? /^\s*(\d+)\b/.exec(limitText) : null
    add(
      'max_payouts',
      group,
      'Payouts before live or close',
      limitCount ? (payouts.length < Number(limitCount[1]) ? 'pass' : 'fail') : 'n/a',
      {
        current: String(payouts.length),
        ...(limitCount ? { limit: limitCount[1] ?? null } : {}),
        message: limitText ?? '',
      },
    )

    const terms = [aboveBuffer]
    if (cap instanceof Decimal) terms.push(cap)
    const share = percentageCap(text(r, 'payout_pct_of_profit_cap'))
    if (share) {
      const [percent, basis] = share
      const eligible = { profit: balance.minus(start), 'cycle profit': cycleProfit, balance }[basis]
      terms.push(eligible.times(percent).div(HUNDRED))
    }
    const gross = Decimal.max(Decimal.min(...terms), ZERO).toDecimalPlaces(2, Decimal.ROUND_DOWN)
    const enough = minPayout !== null ? gross.greaterThanOrEqualTo(minPayout) : gross.greaterThan(0)
    add('withdrawable', group, 'Withdrawable now', enough ? 'pass' : 'fail', {
      current: money(gross),
      limit: minPayout !== null ? `at least ${money(minPayout)}` : 'more than $0.00',
    })

    const gates = [
      'qualifying_days',
      'min_trading_days',
      'consistency',
      'profit_target',
      'min_balance',
      'buffer',
      'payout_cap',
      'payout_cadence',
      'payout_days',
      'inactivity',
      'max_payouts',
      'withdrawable',
    ]
    const blockers = gates
      .map((key) => results.get(key))
      .filter((result): result is RuleResult => !!result && result.status !== 'pass' && result.status !== 'n/a')
      .map((result) => result.label)
    if (breached) blockers.unshift('Breached')
    const ready = blockers.length === 0
    const split = splitShare(text(r, 'profit_split'))
    const parts: string[] = []
    const qualifying = results.get('qualifying_days')
    if (qualifying && qualifying.status !== 'n/a')
      parts.push(`${qualifying.current}/${qualifying.limit} qualifying days`)
    if (consistencyNumbers && consistencyNumbers[1].greaterThan(0) && dec(r, 'consistency_pct')) {
      parts.push(
        `best day ${pct(consistencyNumbers[0].div(consistencyNumbers[1]).times(HUNDRED))} of ${pct(dec(r, 'consistency_pct') ?? ZERO)} max`,
      )
    }
    const short = readyBalance().minus(balance)
    if (short.greaterThan(0)) parts.push(`${money(short)} to buffer`)
    payout = {
      ready,
      gross: ready ? gross.toFixed(2) : null,
      net: ready && split ? gross.times(split).div(HUNDRED).toFixed(2) : null,
      split_pct: split ? split.toFixed(2) : null,
      next_number: next,
      progress: parts.join(' · '),
      blockers,
    }
  }

  // The spectrum: breach on the left, the target or the payout-ready balance on the right.
  let left: Decimal | null = floor
  let leftKind: Spectrum['breach_kind'] = floor !== null ? 'drawdown' : null
  if (dailyFloor !== null && (left === null || dailyFloor.greaterThan(left))) {
    left = dailyFloor
    leftKind = 'daily_loss'
  }
  let right: Decimal | null = null
  let rightKind: Spectrum['target_kind'] = null
  if (funded) {
    if (
      ['payout_buffer_usd', 'min_balance_to_request_usd', 'min_payout_usd', 'profit_target_usd'].some((n) => dec(r, n))
    ) {
      right = readyBalance()
      rightKind = 'payout'
    }
  } else if (effectiveTarget !== null) {
    right = start.plus(effectiveTarget)
    rightKind = 'profit_target'
  }
  let position: number | null = null
  if (left !== null && right !== null) {
    if (right.greaterThan(left))
      position = Math.min(1, Math.max(0, balance.minus(left).div(right.minus(left)).toNumber()))
    else if (balance.greaterThanOrEqualTo(right)) position = 1 // past the goal, with today's loss limit above it
  }
  const spectrum: Spectrum = {
    breach_level: left?.toFixed(2) ?? null,
    breach_kind: leftKind,
    target_level: right?.toFixed(2) ?? null,
    target_kind: rightKind,
    balance: balance.toFixed(2),
    to_breach: left !== null ? balance.minus(left).toFixed(2) : null,
    to_target: right !== null ? Decimal.max(ZERO, right.minus(balance)).toFixed(2) : null,
    position,
  }

  let status: AccountStatus = atRisk.length ? 'at_risk' : 'ok'
  if (breached) status = 'breached'
  else if (!funded) {
    const met =
      results.get('profit_target')?.status === 'pass' &&
      ['min_trading_days', 'consistency', 'qualifying_days'].every((key) =>
        ['pass', 'n/a'].includes(results.get(key)?.status ?? 'n/a'),
      )
    if (met) status = 'passed'
  } else if (payout?.ready) status = 'payout_ready'

  let progress = payout?.progress ?? ''
  if (!funded) {
    const parts: string[] = []
    if (effectiveTarget !== null) {
      const short = start.plus(effectiveTarget).minus(balance)
      parts.push(short.greaterThan(0) ? `${money(short)} to target` : 'target reached')
    }
    progress = parts.join(' · ')
  }

  return {
    status,
    balance,
    net_pnl: netPnl,
    rules: [...results.values()],
    spectrum,
    payout,
    consistency_headroom: headroom,
    inactivity: null,
    floor,
    progress,
  }
}
