import { Link } from '@tanstack/react-router'
import { useRef, useState, type DragEvent } from 'react'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { ImportRecord } from '../../lib/data/model'
import { useAccounts, useCommitImport, usePreviewImport } from '../../lib/data/queries'
import { pluralize } from '../../lib/format'
import { HistoryCard } from './HistoryCard'
import { PreviewSection } from './PreviewSection'

function kilobytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Something went wrong. Nothing was imported.'
}

/**
 * Import trades: choose an account and a Tradovate Performance CSV, see exactly what would happen (nothing is saved
 * yet), confirm, and later undo from the history. The preview is re-worked if the account changes, because what counts
 * as "already imported" depends on the account.
 */
export function ImportPage({ timeZone }: { timeZone: string }) {
  const accounts = useAccounts()
  const preview = usePreviewImport()
  const commit = useCommitImport()
  const [pickedAccount, setPickedAccount] = useState<number | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const accountId = pickedAccount ?? accounts.data?.[0]?.id ?? null
  const account = accounts.data?.find((a) => a.id === accountId)

  const choose = (files: readonly File[]) => {
    const next = files[0]
    if (!next || accountId === null) return // the account list is still loading: there is nothing to import into yet
    setNotice(files.length > 1 ? `One file at a time: using ${next.name}.` : null)
    setFile(next)
    commit.reset()
    preview.mutate({ accountId, file: next })
  }

  const changeAccount = (id: number) => {
    setPickedAccount(id)
    if (file) preview.mutate({ accountId: id, file })
  }

  const clear = () => {
    setFile(null)
    preview.reset()
    commit.reset()
  }

  const doCommit = () => {
    if (!file || accountId === null) return
    commit.mutate(
      { accountId, file },
      {
        onSuccess: (record) => {
          setNotice(
            `Imported ${pluralize(record.rows_added, 'fill pair')} into ${record.account.name}, making ${pluralize(record.trades_built, 'trade')}.`,
          )
          setFile(null)
          preview.reset()
        },
      },
    )
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    choose([...event.dataTransfer.files])
  }

  const data = preview.data
  return (
    <>
      <div>
        <h1 className="m-0 text-[22px] font-semibold">Import trades</h1>
        <p className="mt-1 mb-0 text-muted">
          Upload a Tradovate Performance CSV. Nothing is saved until you confirm the preview, and every import can be
          undone.
        </p>
      </div>

      {notice && !commit.isError ? (
        <Card className="flex flex-wrap items-center gap-3 px-5 py-3" role="status">
          <span>{notice}</span>
          {notice.startsWith('Imported') ? (
            <Link to="/trades" search={(prev) => prev}>
              View the trades
            </Link>
          ) : null}
        </Card>
      ) : null}

      <Card className="flex flex-wrap items-stretch gap-5 p-5">
        <div className="flex flex-[1_1_280px] flex-col gap-1.5">
          <label htmlFor="import-account" className="text-[13px] font-medium">
            Import into account
          </label>
          <select
            id="import-account"
            value={accountId ?? ''}
            disabled={!accounts.data}
            onChange={(e) => changeAccount(Number(e.target.value))}
            className="h-[42px] rounded-lg border border-line bg-raised px-3 font-[inherit] text-ink"
          >
            {accounts.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <div className="mt-0.5 text-xs text-muted">
            CSV timestamps are read as <span className="num">{timeZone}</span>.{' '}
            <Link to="/settings" search={(prev) => prev}>
              Change
            </Link>
          </div>
        </div>

        <div
          onDragOver={(e) => e.preventDefault()}
          onDragEnter={() => setDragging(true)}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cx(
            'flex flex-[2_1_420px] flex-col items-center justify-center gap-1.5 rounded-[10px] border border-dashed p-5 text-center',
            dragging || file ? 'border-chip-selected-line bg-chip-selected-bg' : 'border-line-strong',
          )}
        >
          {file ? (
            <>
              <div className="font-medium">{file.name}</div>
              <div className="text-[13px] text-muted">
                {kilobytes(file.size)}
                {data && !data.fatal
                  ? ` · ${pluralize(data.rows_parsed + data.errors.length + data.duplicates_in_file, 'row')}`
                  : ''}
              </div>
            </>
          ) : (
            <>
              <div className="font-medium">Drop a Performance CSV here</div>
              <div className="text-[13px] text-muted">Up to 5 MB. It is read on your device first.</div>
            </>
          )}
          <Button className="mt-1.5" disabled={accountId === null} onClick={() => input.current?.click()}>
            {file ? 'Replace file' : 'Choose a file'}
          </Button>
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            tabIndex={-1}
            aria-label="Choose a Performance CSV"
            className="sr-only"
            onChange={(event) => {
              choose([...(event.target.files ?? [])])
              event.target.value = '' // so choosing the same file again still works
            }}
          />
        </div>
      </Card>

      {preview.isPending ? <Spinner label="Reading the file" /> : null}
      {preview.isError ? (
        <Card className="p-5" role="alert">
          <p className="m-0 font-semibold text-neg">{messageOf(preview.error)}</p>
          <Button className="mt-3" onClick={clear}>
            Choose another file
          </Button>
        </Card>
      ) : null}
      {data?.fatal ? (
        <Card className="p-5" role="alert">
          <p className="m-0 font-semibold text-neg">{data.fatal}</p>
          <Button className="mt-3" onClick={clear}>
            Choose another file
          </Button>
        </Card>
      ) : null}
      {commit.isError ? (
        <Card className="p-5" role="alert">
          <p className="m-0 font-semibold text-neg">{messageOf(commit.error)}</p>
        </Card>
      ) : null}
      {data && !data.fatal && file ? (
        <PreviewSection
          preview={data}
          accountName={account?.name ?? 'this account'}
          committing={commit.isPending}
          onCancel={clear}
          onCommit={doCommit}
        />
      ) : null}

      <HistoryCard
        timeZone={timeZone}
        onUndone={(record: ImportRecord) =>
          setNotice(`Undid ${record.file_name}: its fill pairs and trades are removed.`)
        }
      />
    </>
  )
}
