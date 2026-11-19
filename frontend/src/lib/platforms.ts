import type { Platform } from './data/model'

/*
 * The platforms Roundturn imports from, as data: the Import page's "Where are these trades from?" list, its export
 * reminders, the preview and the history read from here. A new platform is one more entry here plus its reader on the
 * server (`backend/tradejournal/platforms/`). Tradovate's columns and the size limit stay in `tradovate.ts`, which a
 * backend test pins to the server's; TradeSea's columns are pinned the same way.
 */

/**
 * Where each platform's export guide lives on the marketing site (docs/design/tradesea-import-plan.md, section 2.1).
 * Until the site's docs section is published, these open its "page not found" page; the owner accepted that.
 */
export const DOCS_IMPORT = 'https://roundturn.app/docs/import/'

export interface PlatformInfo {
  id: Platform
  /** The platform's name, as people write it. */
  name: string
  /** The entry in the drop-down list: the platform and the export it reads. */
  label: string
  /** Several files make one import (TradeSea saves one page of its list per file; the Round Turn Monitor may have an
   * overflow file beside its log). */
  multiple: boolean
  /** What one row of its export is, singular and plural. */
  row: readonly [string, string]
  /** The export guide on roundturn.app. */
  guide: string
}

export const PLATFORMS: readonly PlatformInfo[] = [
  {
    id: 'tradovate',
    name: 'Tradovate',
    label: 'Tradovate: Performance report',
    multiple: false,
    row: ['fill pair', 'fill pairs'],
    guide: `${DOCS_IMPORT}tradovate/`,
  },
  {
    id: 'tradesea',
    name: 'TradeSea',
    label: 'TradeSea: Orders (Filled)',
    multiple: true,
    row: ['order', 'orders'],
    guide: `${DOCS_IMPORT}tradesea/`,
  },
  {
    id: 'round_turn_monitor',
    name: 'Round Turn Monitor',
    label: 'NinjaTrader: Round Turn Monitor log',
    multiple: true, // its log, and the overflow file it writes while the log is open in Excel
    row: ['round turn', 'round turns'],
    guide: `${DOCS_IMPORT}round-turn-monitor/`,
  },
]

/** Where the Round Turn Monitor (an add-on for NinjaTrader 8) keeps its log, and the files' names. */
export const ROUND_TURN_LOG_FOLDER = ['Documents', 'NinjaTrader 8', 'RoundTurnLog'] as const
export const ROUND_TURN_LOG = 'RoundTurnLog.csv'
export const ROUND_TURN_OVERFLOW = 'RoundTurnLog_overflow.csv'

export function platformInfo(id: Platform): PlatformInfo {
  return PLATFORMS.find((p) => p.id === id) as PlatformInfo
}

/** The columns of TradeSea's Orders export. The server's reader (`COLUMNS` in `platforms/tradesea.py`) is the only one
 * that reads a real file; a backend test fails if the two ever differ. */
export const TRADESEA_COLUMNS = [
  'Time',
  'Symbol',
  'Qty',
  'Side',
  'Order Type',
  'Limit Price',
  'Stop Price',
  'Avg Price',
  'Commission',
  'Status',
] as const

/** Orders on one page of TradeSea's Orders list: its export saves one page. */
export const TRADESEA_PAGE_ROWS = 10
