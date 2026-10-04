import { Card } from '../../components/ui/primitives'
import { EXPORT_URL } from '../../lib/exportData'
import { DeleteAccount } from './DeleteAccount'
import { SectionHeader } from './SettingsLayout'

/**
 * What is stored for the account, a way to take all of it, and a way to delete it. `restored` is set when signing in has
 * just cancelled a scheduled deletion (the server says so by sending the person here); `deleteOpen` opens the deletion
 * dialog, which is where signing in again for it brings them back.
 */
export function PrivacyPage({
  deleteOpen = false,
  restored = false,
  onDeleteClosed = () => undefined,
}: {
  deleteOpen?: boolean
  restored?: boolean
  onDeleteClosed?: () => void
}) {
  return (
    <>
      <SectionHeader title="Data & privacy">Everything stored for your account is yours to keep.</SectionHeader>
      {restored ? (
        <Card className="px-5 py-3" role="status">
          <strong>Welcome back.</strong> Your account was scheduled for deletion, and signing in cancelled that. Nothing
          was deleted.
        </Card>
      ) : null}
      <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Download your data">
        <h2 className="m-0 text-base font-semibold">Download your data</h2>
        <p className="m-0 text-muted">
          One ZIP file with everything stored for your account: your prop accounts and their fees, every import, your
          trades and the fills behind them, tags, ledger entries, and your notes (each as a web page and as plain text).
          A manifest lists every file with its size and checksum. Amounts and prices are exact, and times are in UTC.
        </p>
        <p className="m-0 text-muted">
          Your screenshots come with it as image files, named by the id in the screenshots list. If a file could not be
          found, the manifest says which one.
        </p>
        <div>
          <a
            href={EXPORT_URL}
            download
            className="inline-flex h-9 items-center justify-center rounded-lg border border-accent bg-accent px-3 font-semibold text-accent-on no-underline hover:bg-accent-hover hover:text-accent-on"
          >
            Download my data
          </a>
        </div>
        <p className="m-0 text-[13px] text-muted">
          It is made when you ask, from a single moment of your data, and can take a few seconds for a long history.
        </p>
      </Card>
      <DeleteAccount startOpen={deleteOpen} onClosed={onDeleteClosed} />
    </>
  )
}
