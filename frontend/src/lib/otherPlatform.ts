import Decimal from 'decimal.js'

/*
 * The "Other platform" import: for every platform Roundturn has no reader for. Roundturn does not read the platform's
 * export. The trader gives it, with the prompt below, to an AI chatbot of their choice, which writes Roundturn's own
 * import file: one row per round turn, in a fixed format with a version. The server's reader
 * (`backend/tradejournal/platforms/other.py`) refuses a file that is not in the format and each row that does not add up,
 * and the trader checks the file's net total against their platform's statement (`checkStatement`) before importing.
 *
 * Backend tests fail if the columns, the version or the contracts here differ from the server's.
 */

export const OTHER_FORMAT_VERSION = 1

/** The columns of Roundturn's import file, version 1, in the order the prompt asks for. */
export const OTHER_COLUMNS = [
  'roundturn_version',
  'symbol',
  'side',
  'qty',
  'entry_time',
  'entry_price',
  'exit_time',
  'exit_price',
  'gross_pnl',
  'fees',
  'net_pnl',
] as const

/** The name the prompt asks the chatbot to give the file. */
export const OTHER_FILE_NAME = 'roundturn-import.csv'

/** The contracts Roundturn can check P&L for, with dollars per point (`backend/tradejournal/contracts.py`). */
export const CONTRACTS: readonly (readonly [root: string, dollarsPerPoint: string])[] = [
  ['ES', '50'],
  ['MES', '5'],
  ['NQ', '20'],
  ['MNQ', '2'],
  ['RTY', '50'],
  ['M2K', '5'],
  ['YM', '5'],
  ['MYM', '0.5'],
  ['EMD', '100'],
  ['NKD', '5'],
  ['CL', '1000'],
  ['MCL', '100'],
  ['QM', '500'],
  ['NG', '10000'],
  ['QG', '2500'],
  ['RB', '42000'],
  ['HO', '42000'],
  ['GC', '100'],
  ['MGC', '10'],
  ['SI', '5000'],
  ['SIL', '1000'],
  ['HG', '25000'],
  ['MHG', '2500'],
  ['PL', '50'],
  ['PA', '100'],
  ['ZT', '2000'],
  ['ZF', '1000'],
  ['ZN', '1000'],
  ['TN', '1000'],
  ['ZB', '1000'],
  ['UB', '1000'],
  ['6E', '125000'],
  ['M6E', '12500'],
  ['6B', '62500'],
  ['M6B', '6250'],
  ['6J', '12500000'],
  ['6A', '100000'],
  ['M6A', '10000'],
  ['6C', '100000'],
  ['6S', '125000'],
  ['6N', '100000'],
  ['6M', '500000'],
  ['ZC', '50'],
  ['ZW', '50'],
  ['KE', '50'],
  ['ZS', '50'],
  ['ZO', '50'],
  ['ZL', '600'],
  ['ZM', '100'],
  ['LE', '400'],
  ['GF', '500'],
  ['HE', '400'],
  ['BTC', '5'],
  ['MBT', '0.1'],
  ['ETH', '50'],
  ['MET', '0.1'],
]

/** What the Import page copies for the trader to paste into an AI chatbot, with their export attached. */
export const IMPORT_PROMPT = `I want to import my futures trades into Roundturn, a trading journal. I am attaching an export from my trading platform. Convert it into Roundturn's import file, version ${OTHER_FORMAT_VERSION}, exactly as described below.

STEP 1: ASK ME FIRST
Read the export. Then ask me, in one message, only what you cannot tell for certain from the file:
- Which time zone the export's times are in (for example America/New_York), if the file does not say.
- Whether each row is one fill, one order, or one whole trade with its entry and exit.
- Whether the export includes commissions and fees. If it does not, what I pay per contract for a round turn (entry and exit together), or 0 to leave fees out.
- If the export has more than one account, which one to convert.
Wait for my answers before you go on.

STEP 2: BUILD ROUND TURNS
A round turn is one position in one contract, from flat back to flat.
- Use filled quantities only. Leave out cancelled, rejected and working orders.
- Pair fills first in, first out. A fill that reverses the position (for example, selling 3 while long 1) closes this round turn and opens the next one with the rest.
- qty: the contracts bought in the round turn, which equals the contracts sold.
- entry_time: the first opening fill. exit_time: the fill that made the position flat.
- entry_price: the average price of the opening fills. exit_price: the average price of the closing fills. Never round an average: when it does not come out exact, write at least 8 decimal places.
- A position still open at the end of the export is not a round turn yet: leave it out, and tell me.
- Do not invent, merge, split or drop trades. If anything is unclear, ask me.

STEP 3: THE FILE
A CSV file with this header row and these ${OTHER_COLUMNS.length} columns, and nothing else:
${OTHER_COLUMNS.join(',')}
- roundturn_version: ${OTHER_FORMAT_VERSION} on every row.
- symbol: the contract root followed by the month code and the year, as Tradovate writes it: NQZ6 is the December 2026 E-mini Nasdaq-100, MESH7 the March 2027 Micro E-mini S&P 500. If the export does not show the month, write the root alone (NQ). No exchange prefix, spaces or slashes. Use the exchange's root, one of these (root and dollars per point): ${CONTRACTS.map(([root, value]) => `${root} ${value}`).join(', ')}. Some platforms use other codes (CQG writes EP for ES and ENQ for NQ); write the exchange's root.
- side: long or short.
- qty: a whole number.
- entry_time and exit_time: ISO 8601 with the UTC offset that applied on that date, to the second, for example 2026-11-18T09:31:05-05:00.
- entry_price and exit_price: plain numbers in the exchange's own units: index points, dollars per barrel, cents per bushel for grains, and Treasuries in decimal points (110'16.5 is 110.515625). No dollar signs or thousands separators.
- gross_pnl: dollars before fees, with two decimals: (exit_price minus entry_price) times qty times the contract's dollars per point for a long, and (entry_price minus exit_price) times the same for a short. Negative for a loss.
- fees: the round turn's commissions and fees, both sides together, as a positive number with two decimals (0 if there are none).
- net_pnl: gross_pnl minus fees, with two decimals.
Use a dot for decimals and commas only between columns. No other columns, no notes and no totals row.

STEP 4: CHECK WITH ME
Before you give me the file, show me the number of round turns, the first and last dates, and the total gross P&L, total fees and total net P&L. Ask me to compare the total net P&L with my platform's statement for the same dates. If they differ, help me find out why and fix the rows before going on.

STEP 5: GIVE ME THE FILE
Give me the file to download, named ${OTHER_FILE_NAME}. If you cannot make files, put the whole CSV in one code block so I can save it as ${OTHER_FILE_NAME}.`

/** The trader's statement figure against the file's own net P&L. */
export type StatementCheck =
  | { state: 'empty' }
  | { state: 'invalid' }
  | { state: 'match'; statement: string }
  | { state: 'differs'; statement: string; difference: string }

/**
 * What a person typed as their statement's net P&L ("$1,234.50", "-125", "(125.00)", "−125") as an exact decimal string
 * with cents, or null when it is not an amount. Unlike `parseAmount`, a loss is negative.
 */
export function parseStatement(text: string): string | null {
  const cleaned = text.trim().replaceAll(',', '').replaceAll(' ', '').replace('−', '-')
  const match = /^(\()?(-)?\$?(-)?(\d+(?:\.\d*)?|\.\d+)(\))?$/.exec(cleaned)
  if (!match) return null
  const [, open, minusBefore, minusAfter, digits, close] = match
  const signs = [open && close, minusBefore, minusAfter].filter(Boolean).length
  if (Boolean(open) !== Boolean(close) || signs > 1) return null // "(5", "--5", "(-5)"
  const value = new Decimal(digits as string)
  return (signs === 1 ? value.negated() : value).toFixed(2)
}

/** Why the typed statement figure cannot be used, or undefined when it can (or nothing is typed yet). */
export function statementProblem(text: string): string | undefined {
  return text.trim() !== '' && parseStatement(text) === null
    ? 'Type an amount like 1234.50, or -125 for a loss.'
    : undefined
}

/** Whether the file's net P&L is the statement's, to the cent. */
export function checkStatement(fileNet: string, entered: string): StatementCheck {
  if (entered.trim() === '') return { state: 'empty' }
  const statement = parseStatement(entered)
  if (statement === null) return { state: 'invalid' }
  const difference = new Decimal(fileNet).minus(statement)
  if (difference.abs().lessThan('0.005')) return { state: 'match', statement }
  return { state: 'differs', statement, difference: difference.toFixed(2) }
}
