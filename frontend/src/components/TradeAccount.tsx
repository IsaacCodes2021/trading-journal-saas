import type { TradeRow } from '../lib/data/model'
import { truncatedId } from '../lib/copyTrading'

/** A trade's account in a list. A copied trade shows once, under its group's name, with the leader's shortened id
 * below it (decision 13 of docs/new-feature-copy-trading/decisions.csv). */
export function TradeAccount({ trade }: { trade: Pick<TradeRow, 'account' | 'copy_group'> }) {
  if (!trade.copy_group) return <>{trade.account.name}</>
  return (
    <span
      className="flex flex-col leading-tight"
      title={`Copy group ${trade.copy_group.name}, led by ${trade.account.name}`}
    >
      <span>{trade.copy_group.name}</span>
      <span className="num text-[12px] text-muted">{truncatedId(trade.account.name)}</span>
    </span>
  )
}
