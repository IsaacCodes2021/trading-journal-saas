import { Card } from '../../components/ui/primitives'
import { EXPORT_URL } from '../../lib/exportData'
import { SectionHeader } from './SettingsLayout'

/** What is stored for the account, a way to take all of it, and (soon) a way to delete it. */
export function PrivacyPage() {
  return (
    <>
      <SectionHeader title="Data & privacy">Everything stored for your account is yours to keep.</SectionHeader>
      <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Download your data">
        <h2 className="m-0 text-base font-semibold">Download your data</h2>
        <p className="m-0 text-muted">
          One ZIP file with everything stored for your account: your prop accounts and their fees, every import, your
          trades and the fills behind them, tags, ledger entries, and your notes (each as a web page and as plain text).
          A manifest lists every file with its size and checksum. Amounts and prices are exact, and times are in UTC.
        </p>
        <p className="m-0 text-muted">
          Screenshots are listed by name and date; the image files are added when screenshots are available.
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
      <Card className="flex flex-col gap-1 px-5 py-4" role="region" aria-label="Delete your account">
        <h2 className="m-0 text-base font-semibold">Delete your account</h2>
        <p className="m-0 text-muted">
          Deleting your account and everything in it arrives before the beta opens. Download your data first if you want
          a copy.
        </p>
      </Card>
    </>
  )
}
