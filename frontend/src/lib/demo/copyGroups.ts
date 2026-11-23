import Decimal from 'decimal.js'
import type {
  CopyGroup,
  CopyGroupMember,
  MembershipChangeInput,
  MembershipEvent,
  NewCopyGroupInput,
} from '../data/model'
import { allAccountRefs } from './accounts'
import { SAMPLE_TRADES } from './generate'

/*
 * Copy-trading groups in the sample journal: held in memory like the rest of its changes. A follower's numbers are the
 * leader's trades in the days it was in the group, as on the server (`pg/copy_groups.py`); the sample dashboard does
 * not add the copies to its totals.
 */

interface Stored {
  id: number
  name: string
  leaderId: number
  createdAt: string
  events: MembershipEvent[]
}

let groups: Stored[] = []
let nextId = 1
let nextEventId = 1

const today = () => new Date().toISOString().slice(0, 10)

function account(id: number) {
  const found = allAccountRefs().find((a) => a.id === id)
  if (!found) throw new Error('That account does not exist.')
  return found
}

function covers(events: MembershipEvent[], day: string): boolean {
  let inside = false
  for (const event of events) {
    const from = event.effective_date ?? '0000-00-00'
    if (day < from) break
    inside = event.event_type === 'added'
  }
  return inside
}

function active(group: Stored, accountId: number): boolean {
  const own = group.events.filter((e) => e.account_id === accountId)
  return own.at(-1)?.event_type === 'added'
}

function view(group: Stored): CopyGroup {
  const leaderTrades = SAMPLE_TRADES.filter((t) => t.account.id === group.leaderId)
  const followerIds = [...new Set(group.events.map((e) => e.account_id))]
  const current = (accountId: number) =>
    groups
      .filter((g) => g.id !== group.id && (g.leaderId === accountId || active(g, accountId)))
      .map((g) => ({ id: g.id, name: g.name }))
  const member = (accountId: number, role: 'leader' | 'follower'): CopyGroupMember => {
    const own = group.events.filter((e) => e.account_id === accountId)
    const counted = role === 'leader' ? leaderTrades : leaderTrades.filter((t) => covers(own, t.trade_day))
    return {
      account: account(accountId),
      role,
      active: role === 'leader' || active(group, accountId),
      archived: false,
      trade_count: counted.length,
      net_pnl: counted.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2),
      other_groups: current(accountId),
    }
  }
  const followers = followerIds
    .map((id) => member(id, 'follower'))
    .sort((a, b) => a.account.name.localeCompare(b.account.name))
  const members = [member(group.leaderId, 'leader'), ...followers]
  return {
    id: group.id,
    name: group.name,
    leader: account(group.leaderId),
    leader_archived: false,
    created_at: group.createdAt,
    fills_acknowledged_at: group.createdAt,
    members,
    net_pnl: members.reduce((sum, m) => sum.plus(m.net_pnl), new Decimal(0)).toFixed(2),
    events: group.events,
  }
}

function event(accountId: number, type: 'added' | 'removed', day: string | null, note: string): MembershipEvent {
  return {
    id: nextEventId++,
    account_id: accountId,
    event_type: type,
    effective_date: day,
    note: note.trim(),
    created_at: new Date().toISOString(),
  }
}

function stored(id: number): Stored {
  const group = groups.find((g) => g.id === id)
  if (!group) throw new Error('That group does not exist.')
  return group
}

export function list(): CopyGroup[] {
  return [...groups].sort((a, b) => a.name.localeCompare(b.name)).map(view)
}

export function create(input: NewCopyGroupInput): CopyGroup {
  const name = input.name.trim().replace(/\s+/g, ' ')
  if (!name) throw new Error('Give the group a name.')
  if (groups.some((g) => g.name.toLowerCase() === name.toLowerCase()))
    throw new Error('You already have a group with that name.')
  if (groups.some((g) => g.leaderId === input.leader_account_id))
    throw new Error('That account already leads a group; add followers to that group instead.')
  if (input.follower_account_ids.length === 0) throw new Error('Choose at least one follower account.')
  if (input.follower_account_ids.includes(input.leader_account_id))
    throw new Error('The leader cannot also be one of its own followers.')
  account(input.leader_account_id)
  const group: Stored = {
    id: nextId++,
    name,
    leaderId: input.leader_account_id,
    createdAt: new Date().toISOString(),
    events: input.follower_account_ids.map(
      (id) => (account(id), event(id, 'added', input.copying_since, input.note ?? '')),
    ),
  }
  groups = [...groups, group]
  return view(group)
}

export function rename(id: number, name: string): CopyGroup {
  const group = stored(id)
  const clean = name.trim().replace(/\s+/g, ' ')
  if (!clean) throw new Error('Give the group a name.')
  group.name = clean
  return view(group)
}

export function remove(id: number): void {
  stored(id)
  groups = groups.filter((g) => g.id !== id)
}

function forward(group: Stored, change: MembershipChangeInput): string {
  const day = change.effective_date ?? today()
  if (day < today()) throw new Error('A change takes effect today or later; past trades keep the followers they had.')
  const last = group.events
    .filter((e) => e.account_id === change.account_id && e.effective_date)
    .map((e) => e.effective_date as string)
    .sort()
    .at(-1)
  if (last && day < last)
    throw new Error(`This account's last change in the group is dated ${last}; pick that day or later.`)
  return day
}

export function addFollower(id: number, change: MembershipChangeInput): CopyGroup {
  const group = stored(id)
  account(change.account_id)
  if (change.account_id === group.leaderId) throw new Error('The leader cannot also be one of its own followers.')
  if (active(group, change.account_id)) throw new Error('That account is already in this group.')
  group.events = [...group.events, event(change.account_id, 'added', forward(group, change), change.note)]
  return view(group)
}

export function removeFollower(id: number, change: MembershipChangeInput): CopyGroup {
  const group = stored(id)
  if (!active(group, change.account_id)) throw new Error('That follower does not exist.')
  group.events = [...group.events, event(change.account_id, 'removed', forward(group, change), change.note)]
  return view(group)
}

/** Tests start from no groups. */
export function resetDemoCopyGroups(): void {
  groups = []
  nextId = 1
  nextEventId = 1
}
