import { afterEach, describe, expect, it } from 'vitest'
import { ImportRefused } from '../data/model'
import { csv, csvFile, manyRows } from '../../test-csv'
import { MAX_CSV_BYTES, PREVIEW_ROWS_SHOWN, resetDemoImports } from './imports'
import { demoSource } from './source'

afterEach(resetDemoImports)

const APEX_50K = 1
const APEX_150K = 2

describe('the sample import preview', () => {
  it('counts what a file would do, and saves nothing', async () => {
    const file = csvFile(csv(manyRows(5)))
    const preview = await demoSource.previewImport(APEX_50K, file)
    expect(preview).toMatchObject({
      file_name: 'Performance_2026-09-30.csv',
      fatal: null,
      rows_parsed: 5,
      rows_added: 5,
      duplicates: 0,
      duplicates_in_file: 0,
      trades_built: 5,
    })
    expect(preview.rows.map((r) => r.status)).toEqual(['new', 'new', 'new', 'new', 'new'])
    expect((await demoSource.imports()).length).toBe(4) // the preview did not add to the history
    expect((await demoSource.previewImport(APEX_50K, file)).rows_added).toBe(5) // and still looks new
  })

  it('shows a file as already imported once it has been imported, to the row', async () => {
    const file = csvFile(csv(manyRows(3)))
    await demoSource.commitImport(APEX_50K, file)
    const again = await demoSource.previewImport(APEX_50K, file)
    expect(again).toMatchObject({ rows_parsed: 3, rows_added: 0, duplicates: 3, trades_built: 0 })
    expect(again.rows.every((r) => r.status === 'duplicate')).toBe(true)
  })

  it('reports pairs stored under another account, by that account’s name', async () => {
    const file = csvFile(csv(manyRows(4)))
    await demoSource.commitImport(APEX_50K, file)
    const preview = await demoSource.previewImport(APEX_150K, file)
    expect(preview.rows_added).toBe(0)
    expect(preview.duplicates).toBe(0)
    expect(preview.duplicates_other_accounts).toEqual({ 'Apex 50K #1': 4 })
    expect(preview.rows[0]).toMatchObject({ status: 'other_account', account: 'Apex 50K #1' })
  })

  it('imports only the new part of a file that overlaps an earlier one', async () => {
    await demoSource.commitImport(APEX_50K, csvFile(csv(manyRows(3, 1000))))
    const overlapping = csvFile(csv([...manyRows(3, 1000), ...manyRows(2, 5000)]))
    const preview = await demoSource.previewImport(APEX_50K, overlapping)
    expect(preview).toMatchObject({ rows_parsed: 5, rows_added: 2, duplicates: 3 })
    const record = await demoSource.commitImport(APEX_50K, overlapping)
    expect(record.rows_added).toBe(2)
  })

  it('lists rows it cannot read, and still previews the rest', async () => {
    const preview = await demoSource.previewImport(
      APEX_50K,
      csvFile(csv([...manyRows(2), { ...manyRows(1, 9000)[0], qty: 'x' }])),
    )
    expect(preview.rows_added).toBe(2)
    expect(preview.errors).toEqual([{ line: 4, message: "qty: not a positive whole number: 'x'" }])
  })

  it('reports a wrong kind of file as fatal, with the reason', async () => {
    const preview = await demoSource.previewImport(APEX_50K, csvFile('a,b\n1,2'))
    expect(preview.fatal).toMatch(/^Not a Tradovate Performance export/)
    expect(preview.rows_added).toBe(0)
  })

  it('shows only the first rows, but counts them all', async () => {
    const preview = await demoSource.previewImport(APEX_50K, csvFile(csv(manyRows(PREVIEW_ROWS_SHOWN + 20))))
    expect(preview.rows).toHaveLength(PREVIEW_ROWS_SHOWN)
    expect(preview.rows_parsed).toBe(PREVIEW_ROWS_SHOWN + 20)
    expect(preview.rows_shown_limit).toBe(PREVIEW_ROWS_SHOWN)
  })

  it('refuses a file that is not a .csv, or is too large, before reading it', async () => {
    await expect(demoSource.previewImport(APEX_50K, new File(['x'], 'notes.txt'))).rejects.toThrow(ImportRefused)
    const big = csvFile('x')
    Object.defineProperty(big, 'size', { value: MAX_CSV_BYTES + 1 })
    await expect(demoSource.previewImport(APEX_50K, big)).rejects.toThrow('larger than 10 MB')
  })
})

describe('the sample import and its history', () => {
  it('starts with the four imports of the mockup, newest first, the last one undone', async () => {
    const history = await demoSource.imports()
    expect(history.map((r) => r.id)).toEqual([4, 3, 2, 1])
    expect(history[0]).toMatchObject({ file_name: 'Performance_2026-09-30.csv', rows_added: 41, trades_built: 14 })
    expect(history[3]?.undone_at).not.toBeNull()
  })

  it('adds an import at the top, with the account and the counts', async () => {
    const record = await demoSource.commitImport(APEX_150K, csvFile(csv(manyRows(6)), 'Performance_2026-10-02.csv'))
    expect(record).toMatchObject({
      file_name: 'Performance_2026-10-02.csv',
      rows_added: 6,
      trades_built: 6,
      undone_at: null,
    })
    expect(record.account.name).toBe('Apex 150K')
    expect((await demoSource.imports())[0]).toEqual(record)
  })

  it('refuses to import a file with nothing new, or a wrong kind of file', async () => {
    const file = csvFile(csv(manyRows(2)))
    await demoSource.commitImport(APEX_50K, file)
    await expect(demoSource.commitImport(APEX_50K, file)).rejects.toThrow('Nothing new to import')
    await expect(demoSource.commitImport(APEX_50K, csvFile('a,b\n1,2'))).rejects.toThrow(
      'Not a Tradovate Performance export',
    )
  })

  it('says what an undo would remove before it happens', async () => {
    const record = await demoSource.commitImport(APEX_50K, csvFile(csv(manyRows(10))))
    const preview = await demoSource.undoPreview(record.id)
    expect(preview).toMatchObject({ import_id: record.id, rows_removed: 10, rows_kept: 0, trades_removed: 10 })
    expect(preview.trades_with_journal_removed).toBeGreaterThan(0)
    expect((await demoSource.imports())[0]?.undone_at).toBeNull() // asking changed nothing
  })

  it('undoes an import: it is marked undone, and its rows can be imported again', async () => {
    const file = csvFile(csv(manyRows(3)))
    const record = await demoSource.commitImport(APEX_50K, file)
    await demoSource.undoImport(record.id)
    expect((await demoSource.imports())[0]?.undone_at).not.toBeNull()
    expect((await demoSource.previewImport(APEX_50K, file)).rows_added).toBe(3)
  })

  it('cannot undo twice, or something that does not exist', async () => {
    await expect(demoSource.undoImport(1)).rejects.toThrow('already undone') // the mockup's undone import
    await expect(demoSource.undoImport(999)).rejects.toThrow('does not exist')
    await expect(demoSource.undoPreview(1)).rejects.toThrow('already undone')
  })
})
