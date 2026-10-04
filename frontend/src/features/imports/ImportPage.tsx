import { useState } from 'react'
import type { ImportRecord } from '../../lib/data/model'
import { HistoryCard } from './HistoryCard'
import { ImportWorkspace } from './ImportWorkspace'

/**
 * Import trades: choose an account and a Tradovate Performance CSV, see exactly what would happen (nothing is saved
 * yet), confirm, and later undo from the history. The working part is `ImportWorkspace`, shared with the first-run
 * setup; the history, which can undo, is only here.
 */
export function ImportPage({ timeZone, csvTimeZone }: { timeZone: string; csvTimeZone: string }) {
  const [notice, setNotice] = useState<string | null>(null)
  return (
    <>
      <div>
        <h1 className="m-0 text-[22px] font-semibold">Import trades</h1>
        <p className="mt-1 mb-0 text-muted">
          Upload a Tradovate Performance CSV. Nothing is saved until you confirm the preview, and every import can be
          undone.
        </p>
      </div>

      <ImportWorkspace timeZone={csvTimeZone} notice={notice} onNotice={setNotice} />

      <HistoryCard
        timeZone={timeZone}
        onUndone={(record: ImportRecord) =>
          setNotice(`Undid ${record.file_name}: its fill pairs and trades are removed.`)
        }
      />
    </>
  )
}
