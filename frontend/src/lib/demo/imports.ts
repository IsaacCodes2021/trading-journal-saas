import type { ImportPreview, ImportRecord, PreviewRow, UndoPreview } from '../data/model'
import { ImportRefused } from '../data/model'
import { countTrades, parsePerformanceCsv, type ParsedPair } from './csv'
import { SAMPLE_ACCOUNTS } from './generate'
import { accountRef } from './accounts'

/*
 * The sample import: reads a real Performance export in the browser (nothing is uploaded), checks it the way the
 * backend will, and keeps the result in memory. Importing the same file twice shows every row as already imported, and
 * a pair stored under another account is reported as such, so the screen can be tried honestly. The real source sends
 * the file to the server, which does all of this with the real parser and the real timezone.
 */

export const MAX_CSV_BYTES = 5 * 1024 * 1024
export const MAX_CSV_ROWS = 50_000
export const PREVIEW_ROWS_SHOWN = 100

const account = (id: number) => accountRef(id) ?? (SAMPLE_ACCOUNTS[0] as (typeof SAMPLE_ACCOUNTS)[number])

// 16:48 in Los Angeles on 30 Sep is 23:48 UTC (daylight time).
const INITIAL: ImportRecord[] = [
  {
    id: 4,
    imported_at: '2026-09-30T23:48:00Z',
    file_name: 'Performance_2026-09-30.csv',
    account: account(1),
    rows_added: 41,
    trades_built: 14,
    undone_at: null,
  },
  {
    id: 3,
    imported_at: '2026-09-26T22:12:00Z',
    file_name: 'Performance_2026-09-26.csv',
    account: account(2),
    rows_added: 22,
    trades_built: 9,
    undone_at: null,
  },
  {
    id: 2,
    imported_at: '2026-09-25T21:03:00Z',
    file_name: 'Performance_2026-09-25.csv',
    account: account(3),
    rows_added: 18,
    trades_built: 7,
    undone_at: null,
  },
  {
    id: 1,
    imported_at: '2026-09-22T20:40:00Z',
    file_name: 'Performance_2026-09-22.csv',
    account: account(4),
    rows_added: 30,
    trades_built: 11,
    undone_at: '2026-09-23T16:05:00Z',
  },
]

let history: ImportRecord[] = INITIAL.map((r) => ({ ...r }))
let stored = new Map<string, number>() // "buyFillId-sellFillId" -> the account it is stored under
let keysOf = new Map<number, string[]>() // import id -> the keys it brought in
let nextId = 5

/** Back to the generated state. For tests; a page reload does the same. */
export function resetDemoImports(): void {
  history = INITIAL.map((r) => ({ ...r }))
  stored = new Map()
  keysOf = new Map()
  nextId = 5
}

const keyOf = (p: ParsedPair) => `${p.buyFillId}-${p.sellFillId}`

function checkFile(file: File): void {
  if (!/\.csv$/i.test(file.name)) throw new ImportRefused(`${file.name || 'That file'} is not a .csv file.`)
  if (file.size > MAX_CSV_BYTES) throw new ImportRefused(`${file.name} is larger than 5 MB.`)
}

async function read(accountId: number, file: File) {
  checkFile(file)
  const parsed = parsePerformanceCsv(await file.text())
  if (parsed.pairs.length + parsed.errors.length > MAX_CSV_ROWS) {
    throw new ImportRefused(`${file.name} has more than ${MAX_CSV_ROWS.toLocaleString('en-US')} rows.`)
  }
  const thisAccount = account(accountId)
  const fresh: ParsedPair[] = []
  const rows: PreviewRow[] = []
  let duplicates = 0
  const otherAccounts: Record<string, number> = {}
  for (const pair of parsed.pairs) {
    const owner = stored.get(keyOf(pair))
    const status = owner === undefined ? 'new' : owner === thisAccount.id ? 'duplicate' : 'other_account'
    if (status === 'new') fresh.push(pair)
    else if (status === 'duplicate') duplicates++
    else {
      const name = account(owner as number).name
      otherAccounts[name] = (otherAccounts[name] ?? 0) + 1
    }
    if (rows.length < PREVIEW_ROWS_SHOWN) {
      rows.push({
        line: pair.line,
        status,
        account: status === 'other_account' ? account(owner as number).name : null,
        symbol: pair.symbol,
        bought_at: pair.boughtAt,
        sold_at: pair.soldAt,
        qty: pair.qty,
        buy_price: pair.buyPrice,
        sell_price: pair.sellPrice,
        pnl: pair.pnl,
      })
    }
  }
  const preview: ImportPreview = {
    file_name: file.name,
    file_bytes: file.size,
    fatal: parsed.fatal,
    rows_parsed: parsed.pairs.length,
    rows_added: fresh.length,
    duplicates,
    duplicates_in_file: parsed.duplicates_in_file,
    duplicates_other_accounts: otherAccounts,
    errors: parsed.errors,
    warnings: [],
    trades_built: countTrades(fresh),
    rows,
    rows_shown_limit: PREVIEW_ROWS_SHOWN,
  }
  return { preview, fresh, account: thisAccount }
}

export async function previewImport(accountId: number, file: File): Promise<ImportPreview> {
  return (await read(accountId, file)).preview
}

export async function commitImport(accountId: number, file: File): Promise<ImportRecord> {
  const { preview, fresh, account: target } = await read(accountId, file)
  if (preview.fatal) throw new ImportRefused(preview.fatal)
  if (fresh.length === 0) throw new ImportRefused('Nothing new to import: every row is already stored.')
  const id = nextId++
  const keys = fresh.map(keyOf)
  for (const key of keys) stored.set(key, target.id)
  keysOf.set(id, keys)
  const record: ImportRecord = {
    id,
    imported_at: new Date().toISOString(),
    file_name: file.name,
    account: target,
    rows_added: fresh.length,
    trades_built: preview.trades_built,
    undone_at: null,
  }
  history = [record, ...history]
  return record
}

export function listImports(): ImportRecord[] {
  return history
}

function active(importId: number): ImportRecord {
  const record = history.find((r) => r.id === importId)
  if (!record) throw new Error('That import does not exist.')
  if (record.undone_at !== null) throw new Error('That import was already undone.')
  return record
}

export function undoPreview(importId: number): UndoPreview {
  const record = active(importId)
  return {
    import_id: importId,
    rows_removed: record.rows_added,
    rows_kept: 0,
    trades_removed: record.trades_built,
    // A few removed trades carry notes, tags or pictures; the dialog says so before anything is deleted.
    trades_with_journal_removed: Math.min(3, Math.floor(record.trades_built / 5)),
    trades_changed: 0,
  }
}

export function undoImport(importId: number): void {
  active(importId)
  for (const key of keysOf.get(importId) ?? []) stored.delete(key)
  keysOf.delete(importId)
  history = history.map((r) => (r.id === importId ? { ...r, undone_at: new Date().toISOString() } : r))
}
