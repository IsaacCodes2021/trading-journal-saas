/**
 * The columns a Tradovate "Performance" CSV must have. The backend's parser (`REQUIRED_COLUMNS` in
 * `backend/tradejournal/parser.py`) is the only one that reads a real file; this list is for telling a person what to
 * look for, and a backend test fails if the two ever differ.
 */
export const REQUIRED_COLUMNS = [
  'symbol',
  '_priceFormat',
  '_priceFormatType',
  '_tickSize',
  'buyFillId',
  'sellFillId',
  'qty',
  'buyPrice',
  'sellPrice',
  'pnl',
  'boughtTimestamp',
  'soldTimestamp',
  'duration',
] as const

/** The largest Performance CSV the server accepts (a real export is a few hundred KB). */
export const MAX_CSV_MB = 10
