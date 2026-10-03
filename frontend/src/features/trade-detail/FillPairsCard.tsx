import { Card, Pnl } from '../../components/ui/primitives'
import type { TradeDetail } from '../../lib/data/model'
import { formatClock, formatPnl, formatPrice } from '../../lib/format'

const th = 'border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted'
const td = 'num border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap'

/** The broker's rows behind the trade, with the arithmetic that turns their results into the net figure above. */
export function FillPairsCard({ trade, timeZone }: { trade: TradeDetail; timeZone: string }) {
  return (
    <Card className="p-0" role="region" aria-label="Fill pairs">
      <h2 className="m-0 border-b border-line px-5 py-3.5 text-base font-semibold">Fill pairs</h2>
      <div className="scroll-x">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th scope="col" className={`${th} text-left`}>
                #
              </th>
              <th scope="col" className={`${th} text-left`}>
                Bought
              </th>
              <th scope="col" className={`${th} text-left`}>
                Sold
              </th>
              <th scope="col" className={`${th} text-right`}>
                Qty
              </th>
              <th scope="col" className={`${th} text-right`}>
                Buy
              </th>
              <th scope="col" className={`${th} text-right`}>
                Sell
              </th>
              <th scope="col" className={`${th} text-right`}>
                P&amp;L
              </th>
            </tr>
          </thead>
          <tbody>
            {trade.fill_pairs.map((pair, i) => (
              <tr key={pair.id}>
                <td className={td}>{i + 1}</td>
                <td className={td}>{formatClock(pair.bought_at, timeZone)}</td>
                <td className={td}>{formatClock(pair.sold_at, timeZone)}</td>
                <td className={`${td} text-right`}>{pair.qty}</td>
                <td className={`${td} text-right`}>{formatPrice(pair.buy_price)}</td>
                <td className={`${td} text-right`}>{formatPrice(pair.sell_price)}</td>
                <td className="border-b border-line-soft px-3.5 py-2.5 text-right whitespace-nowrap">
                  <Pnl value={pair.pnl} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" colSpan={6} className="px-3.5 pt-3 pb-1 text-right text-xs font-medium text-muted">
                Gross
              </th>
              <td className="num px-3.5 pt-3 pb-1 text-right">{formatPnl(trade.gross_pnl)}</td>
            </tr>
            <tr>
              <th scope="row" colSpan={6} className="px-3.5 py-1 text-right text-xs font-medium text-muted">
                Fees
              </th>
              <td className="num px-3.5 py-1 text-right">{formatPnl(`-${trade.fees}`)}</td>
            </tr>
            <tr>
              <th scope="row" colSpan={6} className="px-3.5 pt-1 pb-3 text-right text-xs font-medium text-muted">
                Net
              </th>
              <td className="px-3.5 pt-1 pb-3 text-right font-semibold">
                <Pnl value={trade.net_pnl} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  )
}
