/*
 * Copy trading on screen: the decisions are in docs/new-feature-copy-trading/decisions.csv. A leader's trades are stored
 * once; a follower's numbers are worked out from them, so they are estimates (copies do not always fill the same).
 */

/** A leader's account as the trade log's second line: the first 3 and last 3 characters ("LEA...-01"). A name of 7
 * characters or fewer is shown whole, since shortening it would hide nothing. */
export function truncatedId(name: string): string {
  const chars = [...name.trim()]
  return chars.length <= 7 ? chars.join('') : `${chars.slice(0, 3).join('')}...${chars.slice(-3).join('')}`
}

/** What the person accepts before a group is made, and what every follower figure is read against. */
export const FILLS_DISCLAIMER =
  'Copy trading does not guarantee identical fills across accounts, so follower results may differ slightly from the leader’s. Roundturn shows each follower the leader’s results.'
