# Feature: Copy-Trading Groups

## Goal
Users who copy trade (one leader account mirrored to many follower accounts) should not have to import or journal the same trade once per account. Store each trade ONCE, on the leader. Followers are linked accounts whose P&L is derived at read time. Minimize database writes and rows: a user copying across 20 accounts must still have exactly one trade row per trade.

## Core principles
- Import is leader-only. Followers never get their own imported data.
- Follower P&L is computed at read time, never stored.
- All followers trade the same size as the leader (no multiplier).
- Follower numbers are estimates. Copy trading does not guarantee identical fills, so slight variances are expected and the user must acknowledge this.
- Desktop-first. Mobile support is out of scope for this feature.

## 1. Import flow
On the existing "new account / import to account" form, add a "Copy trading" section.
1. User picks the account being imported to. Ask: "Is this account the leader of a copy-trading group?"
2. If yes, show a dropdown to select follower accounts (multi-select). Each selected account appears as a small label/chip.
3. Show an acknowledgment the user must accept before continuing: copy trading does not guarantee identical fills across accounts, so follower results may differ slightly from the leader's.
4. Prompt the user to name the group (free text).
5. Save the group. Import proceeds as normal for the leader only.

## 2. Data model (suggested, adapt to the existing schema)
- `copy_groups`: id, user_id, name, leader_account_id, created_at
- `copy_group_membership_events`: id, group_id, account_id, event_type (`added` | `removed`), effective_date, note (optional), created_at
- Trades table: unchanged. Trades are stored against the leader account only.
- Do NOT create per-follower trade rows or per-follower P&L rows.
- An account may belong to multiple groups. This is allowed and is the user's responsibility to track.
- Membership changes are forward-only. A follower's derived P&L counts only leader trades that occurred while that account was an active member (derive active windows from the event log). Past trades keep the follower set they actually had.

## 3. Derived follower P&L
- Follower P&L for a trade = the leader's P&L for that trade, counted only if the follower was an active member at the trade's time.
- Group cumulative P&L = sum across all member accounts, leader included.
- Compute at read time with a join between leader trades and membership windows. If performance becomes an issue, cache small aggregates (per group per day), never per-trade-per-follower rows.
- A follower's derived P&L rolls into that account's own totals (Individual accounts tab, dashboard, equity/payout tracking).
- If an account is in multiple groups, its derived P&L stacks from each group's leader, since it is receiving both sets of trades.

## 4. Trades page
- A grouped trade shows once, not once per account.
- In the account column, show the group name as the primary label, with the leader's truncated ID (first 3 chars, "...", last 3 chars) as secondary text.
- The journal entry (notes, tags, screenshots) attaches to the leader's trade and applies to the whole group. No per-follower notes.
- Clicking the trade opens the detail view. It lists the follower accounts the trade was copied to.
- Clicking a follower account in that list opens a popup with a brief analytics view for that account. Reuse the existing dashboard analytics code/components, scoped to that account.
- The account filter must include groups as options, alongside individual accounts (including followers).

## 4b. Accounts tab
- Add two sub-tabs: **Individual** (the existing view, unchanged) and **Groups**. Only show the tabs if the user has at least one group.
- Groups view: one section per group with columns: group name, account name, account P&L (derived for followers), and a cumulative P&L row for the group.
- If an account belongs to more than one group, highlight it in light yellow. Hover or click shows which other groups it belongs to.
- Provide controls to add or remove a follower, with an optional note. Changes are forward-only and recorded as dated membership events.

## 5. Edge cases the agent should handle
- Leader removed or deleted: ask or surface a clear warning rather than silently orphaning the group.
- Follower removed: stops counting from the effective date; historical derived P&L stays intact.
- A follower account that is also a leader of another group is valid.
- A trade appearing only on a follower (manual trade on that account) is out of scope; the user would import that account normally as a standalone account.

## 6. Out of scope
- Migrating existing duplicate trades (no existing copy-trade data).
- Per-follower multipliers or different position sizes.
- Per-follower real fills or variance storage.
- Mobile layout.

## 7. Decisions file (required)
Create and maintain `decisions.csv` for this feature, seeded from the project's `decisions.csv` (see the seed file provided). Treat it as the source of truth. Whenever you make or change a design decision, add or update a row with: id, date, area, decision, rationale, status. Check it before making any design choice, and flag conflicts instead of silently overriding.

## 8. Testing
The owner has real copy-traded accounts for testing once it ships. Add tests for: forward-only membership windows, account in multiple groups (stacking + yellow highlight), group cumulative P&L, and that importing a leader creates no per-follower rows.
