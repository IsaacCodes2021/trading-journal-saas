import { useState } from 'react'
import { Modal } from '../../components/ui/Modal'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { ImportRecord } from '../../lib/data/model'
import { useImports, useUndoImport, useUndoPreview } from '../../lib/data/queries'
import { formatTradeTime, pluralize } from '../../lib/format'

function UndoDialog({
  record,
  onClose,
  onUndone,
}: {
  record: ImportRecord
  onClose: () => void
  onUndone: () => void
}) {
  const preview = useUndoPreview(record.id)
  const undo = useUndoImport()
  const data = preview.data
  return (
    <Modal label="Undo import" onClose={undo.isPending ? () => undefined : onClose} className="max-w-[520px]">
      <h2 className="m-0 text-base font-semibold">Undo this import?</h2>
      <p className="m-0 text-muted">
        {record.file_name} into {record.account.name}
      </p>
      {preview.isPending ? <Spinner label="Checking what this would remove" /> : null}
      {preview.isError ? (
        <p role="alert" className="m-0 text-neg">
          This import can no longer be undone.
        </p>
      ) : null}
      {data ? (
        <div className="flex flex-col gap-2 text-[13px]">
          <p className="m-0">
            This removes {pluralize(data.rows_removed, 'fill pair')} and {pluralize(data.trades_removed, 'trade')}.
            {data.rows_kept > 0 ? ` ${pluralize(data.rows_kept, 'fill pair')} also in another import stay.` : ''}
          </p>
          {data.trades_with_journal_removed > 0 ? (
            <p role="alert" className="m-0 text-warn">
              {pluralize(data.trades_with_journal_removed, 'of those trade')} has notes, tags or screenshots. They are
              deleted with it.
            </p>
          ) : null}
        </div>
      ) : null}
      {undo.isError ? (
        <p role="alert" className="m-0 text-neg">
          The import could not be undone. Nothing was changed.
        </p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        <Button data-autofocus onClick={onClose} disabled={undo.isPending}>
          Keep it
        </Button>
        <Button
          className="border-neg text-neg"
          disabled={!data || undo.isPending}
          onClick={() => undo.mutate(record.id, { onSuccess: onUndone })}
        >
          {undo.isPending ? 'Undoing…' : 'Undo this import'}
        </Button>
      </div>
    </Modal>
  )
}

/** Past imports, newest first, each with a way to undo it. Undoing always shows what it would remove first. */
export function HistoryCard({ timeZone, onUndone }: { timeZone: string; onUndone: (record: ImportRecord) => void }) {
  const imports = useImports()
  const [undoing, setUndoing] = useState<ImportRecord | null>(null)
  const th = 'border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted'
  const td = 'border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap'
  return (
    <Card className="p-0" role="region" aria-label="Import history">
      <h2 className="m-0 border-b border-line px-5 py-3.5 text-base font-semibold">Import history</h2>
      {imports.isPending ? <Spinner label="Loading the history" /> : null}
      {imports.isError ? (
        <p role="alert" className="m-0 p-5">
          The history could not be loaded.
        </p>
      ) : null}
      {imports.data && imports.data.length === 0 ? (
        <p className="m-0 p-5 text-muted">No imports yet. The first one you confirm will appear here.</p>
      ) : null}
      {imports.data && imports.data.length > 0 ? (
        <div className="scroll-x">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th scope="col" className={`${th} text-left`}>
                  Imported
                </th>
                <th scope="col" className={`${th} text-left`}>
                  File
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Account
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Pairs
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Trades
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Status
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {imports.data.map((record) => {
                const undone = record.undone_at !== null
                return (
                  <tr key={record.id}>
                    <td className={`${td} num`}>{formatTradeTime(record.imported_at, timeZone)}</td>
                    <td className={td}>{record.file_name}</td>
                    <td className={td}>{record.account.name}</td>
                    <td className={`${td} num text-right`}>{record.rows_added}</td>
                    <td className={`${td} num text-right`}>{record.trades_built}</td>
                    <td className={td}>
                      <span
                        className={cx(
                          'inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium',
                          undone ? 'bg-raised text-muted' : 'bg-long-bg text-pos',
                        )}
                      >
                        {undone ? 'Undone' : 'Active'}
                      </span>
                    </td>
                    <td className={`${td} text-right`}>
                      {undone ? (
                        <span className="text-muted">&mdash;</span>
                      ) : (
                        <Button
                          className="h-[30px]"
                          aria-label={`Undo ${record.file_name}`}
                          onClick={() => setUndoing(record)}
                        >
                          Undo…
                        </Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {undoing ? (
        <UndoDialog
          record={undoing}
          onClose={() => setUndoing(null)}
          onUndone={() => {
            onUndone(undoing)
            setUndoing(null)
          }}
        />
      ) : null}
    </Card>
  )
}
