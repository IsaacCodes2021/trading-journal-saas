import { Link } from '@tanstack/react-router'
import { useRef, useState, type DragEvent } from 'react'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { ImportPlatform, ImportRecord, Platform } from '../../lib/data/model'
import { useAccounts, useCommitImport, useImports, usePreviewImport } from '../../lib/data/queries'
import { isDemo } from '../../lib/demoMode'
import { pluralize } from '../../lib/format'
import { platformInfo } from '../../lib/platforms'
import { MAX_CSV_MB } from '../../lib/tradovate'
import { AddAccountDialog } from '../accounts/AccountDialogs'
import { CopyTradingSection } from './CopyTradingSection'
import { ImportSourceDialog } from './ImportSourceDialog'
import { LogAccountPicker } from './LogAccountPicker'
import { PreviewSection } from './PreviewSection'

function kilobytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Something went wrong. Nothing was imported.'
}

/** The files chosen so far plus the new ones; a file chosen twice (same name, size and time) counts once. */
function together(chosen: File[], more: File[]): File[] {
  const key = (file: File) => `${file.name}|${file.size}|${file.lastModified}`
  const seen = new Set(chosen.map(key))
  return [...chosen, ...more.filter((file) => !seen.has(key(file)))]
}

/** What the import added, in words, for the line above the form. */
function importedText(record: ImportRecord): string {
  const [one, many] = platformInfo(record.platform).row
  const rows = pluralize(record.rows_added, one, many)
  const from =
    record.platform === 'tradovate'
      ? ''
      : record.platform === 'other'
        ? ' from your converted file'
        : ` from ${platformInfo(record.platform).name}`
  return `Imported ${rows}${from} into ${record.account.name}, making ${pluralize(record.trades_built, 'trade')}.`
}

/**
 * The working part of importing: choose an account and the export files, see exactly what would happen (nothing is
 * saved yet), and confirm. Used by the Import screen and by the first-run setup.
 *
 * **Choose files** first asks where the files come from (`ImportSourceDialog`), with a reminder of how to export from
 * that platform, and then opens the computer's file window: one file for Tradovate, several for TradeSea (one per page of
 * its Orders list) and for the Round Turn Monitor (its log and overflow file). For any other platform, the dialog
 * takes the trader's statement figure and gives them the prompt for an AI chatbot, which makes Roundturn's own import
 * file; the preview checks the file's net P&L against that figure. Files dropped onto the page skip the question: the
 * server tells the platform from their header row (the statement figure is then typed in the preview). The Round Turn Monitor's log holds every NinjaTrader
 * account: the server picks one when it can, and `LogAccountPicker` lets the trader choose.
 * The preview is worked out again if the account changes, because what counts as "already imported" depends on it.
 *
 * Only active accounts are offered (an archived one is finished). A new account can be added from here without
 * leaving the page; once added, it is the one picked.
 *
 * `notice` is the line shown above the form (what was just imported or undone); the caller owns it so that another part
 * of the page (the history) can set it too. `onImported` is told when files have been imported.
 */
export function ImportWorkspace({
  timeZone,
  notice,
  onNotice,
  onImported,
}: {
  timeZone: string
  notice: string | null
  onNotice: (text: string | null) => void
  onImported?: (record: ImportRecord) => void
}) {
  const accounts = useAccounts()
  const history = useImports()
  const preview = usePreviewImport()
  const commit = useCommitImport()
  const [pickedAccount, setPickedAccount] = useState<number | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [sentAs, setSentAs] = useState<ImportPlatform>('auto') // what the chosen files were last read as
  const [sentLogAccount, setSentLogAccount] = useState<string | null>(null) // the log's account chosen for them
  const [picked, setPicked] = useState<Platform | null>(null) // the dialog's last answer on this page
  const [statement, setStatement] = useState('') // Other platform: the net P&L on the trader's statement
  const [asking, setAsking] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [adding, setAdding] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const picking = useRef<{ platform: Platform; more: boolean } | null>(null)

  const accountId = pickedAccount ?? accounts.data?.[0]?.id ?? null
  const account = accounts.data?.find((a) => a.id === accountId)
  // The dialog opens on the platform picked here, else the one this account last imported from (newest first).
  const lastPlatform = history.data?.find((record) => record.account.id === accountId)?.platform ?? null

  /** Works the preview out for these files. `logAccount` is the NinjaTrader account chosen in the Round Turn
   * Monitor's log; none lets the server pick (the one this account already holds, or the only real one). */
  const read = (
    next: File[],
    platform: ImportPlatform,
    { id = accountId, logAccount = null }: { id?: number | null; logAccount?: string | null } = {},
  ) => {
    if (next.length === 0 || id === null) return // the account list is still loading: there is nothing to import into
    setFiles(next)
    setSentAs(platform)
    setSentLogAccount(logAccount)
    commit.reset()
    preview.mutate({ accountId: id, files: next, platform, logAccount })
  }

  /** Opens the computer's file window. Called straight from a click: browsers refuse to open it any other way. */
  const openFileWindow = (platform: Platform, more: boolean) => {
    const element = input.current
    if (!element) return
    picking.current = { platform, more }
    element.multiple = platformInfo(platform).multiple
    element.click()
  }

  const onFilesChosen = (chosen: File[]) => {
    const pick = picking.current
    picking.current = null
    if (!pick || chosen.length === 0) return // the file window was cancelled: nothing changes
    onNotice(null)
    // Files added to the same import keep the log's account chosen for them; new files start again.
    read(pick.more ? together(files, chosen) : chosen, pick.platform, {
      logAccount: pick.more ? sentLogAccount : null,
    })
  }

  // Another Roundturn account may hold another of the log's accounts: the server picks again for it.
  const changeAccount = (id: number) => {
    setPickedAccount(id)
    if (files.length > 0) read(files, sentAs, { id })
  }

  const clear = () => {
    setFiles([])
    preview.reset()
    commit.reset()
  }

  const data = preview.data
  const doCommit = () => {
    if (!data || files.length === 0 || accountId === null) return
    commit.mutate(
      // The log's account the preview read, so the import is exactly what the preview showed.
      { accountId, files, platform: data.platform, logAccount: data.log_account },
      {
        onSuccess: (record) => {
          onNotice(importedText(record))
          setFiles([])
          preview.reset()
          onImported?.(record)
        },
      },
    )
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    onNotice(null)
    read([...event.dataTransfer.files], 'auto')
  }

  const totalBytes = files.reduce((sum, file) => sum + file.size, 0)
  return (
    <>
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
          <div className="flex gap-2">
            <select
              id="import-account"
              value={accountId ?? ''}
              disabled={!accounts.data || accounts.data.length === 0}
              onChange={(e) => changeAccount(Number(e.target.value))}
              className="h-[42px] min-w-0 flex-1 rounded-lg border border-line bg-raised px-3 font-[inherit] text-ink"
            >
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            <Button className="h-[42px] shrink-0" disabled={!accounts.data} onClick={() => setAdding(true)}>
              + New account
            </Button>
          </div>
          {accounts.data?.length === 0 ? (
            <div role="status" className="text-[13px] text-muted">
              You have no active accounts. Add one to import into.
            </div>
          ) : null}
          <div className="mt-0.5 text-xs text-muted">
            We read Tradovate&rsquo;s Performance report, TradeSea&rsquo;s Orders and the Round Turn Monitor&rsquo;s log
            from NinjaTrader. Any other platform&rsquo;s export can come in through an AI chatbot: choose Other
            platform.
          </div>
        </div>

        <div
          onDragOver={(e) => e.preventDefault()}
          onDragEnter={() => setDragging(true)}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cx(
            'flex flex-[2_1_420px] flex-col items-center justify-center gap-1.5 rounded-[10px] border border-dashed p-5 text-center',
            dragging || files.length > 0 ? 'border-chip-selected-line bg-chip-selected-bg' : 'border-line-strong',
          )}
        >
          {files.length > 0 ? (
            <>
              <div className="font-medium break-all">
                {files.length === 1 ? files[0]?.name : `${files.length} files`}
              </div>
              <div className="text-[13px] text-muted">
                {kilobytes(totalBytes)}
                {data && !data.fatal
                  ? data.platform === 'tradesea'
                    ? ` · ${pluralize(data.rows_parsed, 'filled order')}`
                    : data.platform === 'round_turn_monitor' || data.platform === 'other'
                      ? ` · ${pluralize(data.rows_parsed, 'round turn')}`
                      : ` · ${pluralize(data.rows_parsed + data.errors.length + data.duplicates_in_file, 'row')}`
                  : ''}
              </div>
            </>
          ) : (
            <>
              <div className="font-medium">Drop your export here</div>
              <div className="text-[13px] text-muted">
                Up to {MAX_CSV_MB} MB. Nothing is saved until you confirm the preview.
              </div>
            </>
          )}
          <Button className="mt-1.5" disabled={accountId === null} onClick={() => setAsking(true)}>
            {files.length > 0 ? 'Replace files' : 'Choose files'}
          </Button>
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            tabIndex={-1}
            aria-label="Export files"
            className="sr-only"
            onChange={(event) => {
              onFilesChosen([...(event.target.files ?? [])])
              event.target.value = '' // so choosing the same file again still works
            }}
          />
        </div>
      </Card>

      <CopyTradingSection account={account} accounts={accounts.data ?? []} />

      {preview.isPending ? <Spinner label="Reading the files" /> : null}
      {preview.isError ? (
        <Card className="p-5" role="alert">
          <p className="m-0 font-semibold text-neg">{messageOf(preview.error)}</p>
          <Button className="mt-3" onClick={clear}>
            Choose another file
          </Button>
        </Card>
      ) : null}
      {data?.fatal && data.log_accounts.length > 0 && accountId !== null ? (
        // The log holds several NinjaTrader accounts and none could be picked for this one: the trader chooses.
        <LogAccountPicker
          accounts={data.log_accounts}
          chosen={null}
          accountName={account?.name ?? 'this account'}
          message={data.fatal}
          onChoose={(name) => read(files, sentAs, { logAccount: name })}
        >
          <Button className="h-[42px]" onClick={clear}>
            Choose another file
          </Button>
        </LogAccountPicker>
      ) : null}
      {data?.fatal && data.log_accounts.length === 0 ? (
        <Card className="p-5" role="alert">
          <p className="m-0 font-semibold text-neg">{data.fatal}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {data.suggested_platform ? (
              <Button
                variant="primary"
                onClick={() => {
                  const suggested = data.suggested_platform as Platform
                  setPicked(suggested)
                  read(files, suggested)
                }}
              >
                Read {files.length === 1 ? 'it' : 'them'} as {platformInfo(data.suggested_platform).name}
              </Button>
            ) : null}
            <Button onClick={clear}>Choose another file</Button>
          </div>
        </Card>
      ) : null}
      {commit.isError ? (
        <Card className="p-5" role="alert">
          <p className="m-0 font-semibold text-neg">{messageOf(commit.error)}</p>
        </Card>
      ) : null}
      {data && !data.fatal && data.log_accounts.length > 1 && files.length > 0 ? (
        <LogAccountPicker
          accounts={data.log_accounts}
          chosen={data.log_account}
          accountName={account?.name ?? 'this account'}
          onChoose={(name) => read(files, sentAs, { logAccount: name })}
        />
      ) : null}
      {data && !data.fatal && files.length > 0 && accountId !== null ? (
        <PreviewSection
          preview={data}
          accountId={accountId}
          accountName={account?.name ?? 'this account'}
          committing={commit.isPending}
          statement={statement}
          onStatement={setStatement}
          onCancel={clear}
          onCommit={doCommit}
          onAddFiles={() => openFileWindow(data.platform, true)}
          onRecheck={(done) => {
            onNotice(done)
            read(files, sentAs, { logAccount: data.log_account })
          }}
        />
      ) : null}

      {asking ? (
        <ImportSourceDialog
          initial={picked ?? lastPlatform}
          initialStatement={statement}
          timeZone={timeZone}
          demo={isDemo()}
          onClose={() => setAsking(false)}
          onPick={(platform, typed) => {
            setPicked(platform)
            if (platform === 'other') setStatement(typed)
            openFileWindow(platform, false)
            setAsking(false)
          }}
        />
      ) : null}
      {adding ? (
        <AddAccountDialog
          onClose={() => setAdding(false)}
          onCreated={(created) => {
            setAdding(false)
            onNotice(`Added ${created.name}. Files you import now go into it.`)
            changeAccount(created.id)
          }}
        />
      ) : null}
    </>
  )
}
