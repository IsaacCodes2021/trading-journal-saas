import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { SUPPORT_ADDRESS } from '../../components/RouteError'
import { Card } from '../../components/ui/primitives'
import { MAX_IMAGES_PER_TRADE, MAX_IMAGE_MB } from '../../lib/limits'
import { MAX_CSV_MB, REQUIRED_COLUMNS } from '../../lib/tradovate'

/*
 * The answers to the questions that come up most. Everything here is a statement about what the app does today, kept to
 * what the code does (the definitions are `finance.py`'s); the numbers that the server enforces come from `lib/limits.ts` and
 * `lib/tradovate.ts`, which a backend test pins to the server's own.
 */

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-labelledby={id}>
      <h2 id={id} className="m-0 text-base font-semibold">
        {title}
      </h2>
      {children}
    </Card>
  )
}

const p = 'm-0 text-[13.5px] leading-relaxed'
const list = 'm-0 flex list-disc flex-col gap-1.5 pl-5 text-[13.5px] leading-relaxed'

export function HelpPage() {
  return (
    <>
      <h1 className="sr-only">Help</h1>
      <div className="flex max-w-[860px] flex-col gap-4">
        <p className="m-0 text-muted">
          The short answers to the questions people ask most. If yours is not here, write to{' '}
          <a href={`mailto:${SUPPORT_ADDRESS}`}>{SUPPORT_ADDRESS}</a>.
        </p>

        <Section id="help-start" title="Getting started">
          <ol className={`${list} list-decimal`}>
            <li>
              <strong>Check your timezone.</strong> In{' '}
              <Link to="/settings/preferences">Settings &rsaquo; Preferences</Link>, set the timezone you want times
              shown in, and the timezone your Tradovate files are written in. If the second is wrong, trades land on the
              wrong day.
            </li>
            <li>
              <strong>Add your accounts.</strong> In{' '}
              <Link to="/settings/accounts">Settings &rsaquo; Prop accounts</Link>, add each account with its firm and
              whether it is an evaluation or a funded account. Fees, rules and a ledger of payouts and fees are optional
              and can be added later.
            </li>
            <li>
              <strong>Export from Tradovate.</strong> Export the <em>Performance</em> report as a CSV with all its
              columns (not the orders or fills reports). Menu names change between Tradovate versions, so look for the
              Performance report rather than a fixed path.
            </li>
            <li>
              <strong>Import it.</strong> On the <Link to="/import">Import</Link> page choose the account, drop the file
              and read the preview. Nothing is saved until you confirm. A file can be up to {MAX_CSV_MB} MB.
            </li>
          </ol>
          <details className="text-[13.5px]">
            <summary className="cursor-pointer text-muted">The columns the file must have</summary>
            <p className={`${p} mt-2`}>
              {REQUIRED_COLUMNS.map((column, i) => (
                <span key={column}>
                  {i > 0 ? ', ' : ''}
                  <code>{column}</code>
                </span>
              ))}
              . If one is missing, the preview says which.
            </p>
          </details>
        </Section>

        <Section id="help-numbers" title="How the numbers are worked out">
          <ul className={list}>
            <li>
              <strong>Net result</strong> is what you made after fees. Fees come from each account&rsquo;s Fees tab,
              which holds a rate for each contract you trade.
            </li>
            <li>
              <strong>A win</strong> is a trade with a net result above zero, <strong>a loss</strong> below zero; a
              trade at exactly zero is breakeven and is in neither group.
            </li>
            <li>
              <strong>Win rate</strong> is wins divided by wins plus losses. <strong>Profit factor</strong> is the total
              won divided by the total lost. <strong>Expectancy</strong> is the net result divided by the number of
              trades, so it is what an average trade made.
            </li>
            <li>
              <strong>Max drawdown</strong> is the deepest drop of your running total from its highest point so far,
              counting closed trades only, starting from zero.
            </li>
            <li>
              <strong>A trading day</strong> starts at 18:00 New York time by default, the way futures sessions do, so a
              trade entered on Sunday evening belongs to Monday. You can change the start time and its timezone in
              Preferences, and existing trades are moved to match.
            </li>
            <li>
              <strong>Prop-firm rules</strong> shown on an account are for your information. Roundturn works them out
              from the trades you import, and they may differ from your firm&rsquo;s own calculation: the firm&rsquo;s
              numbers are the ones that count.
            </li>
          </ul>
        </Section>

        <Section id="help-trades" title="Notes, tags and screenshots">
          <ul className={list}>
            <li>
              Open any trade to write a note, give it tags (grouped by Setup, Mistake and Emotion, which you can change
              in <Link to="/settings/tags">Settings &rsaquo; Tags</Link>) and attach screenshots. Notes save by
              themselves a moment after you stop typing.
            </li>
            <li>
              Screenshots can be PNG, JPEG, GIF or WebP, up to {MAX_IMAGE_MB} MB each, and a trade can hold{' '}
              {MAX_IMAGES_PER_TRADE}. There is also a limit on the space your pictures take in all; the page tells you
              when you reach it. Other kinds of file, including SVG, are refused on purpose.
            </li>
            <li>
              The <Link to="/calendar">Calendar</Link> shows each day&rsquo;s result and has a place to write a note for
              the day, with or without trades. The <Link to="/reports">Reports</Link> page breaks your trades down by
              contract, weekday, time of day, hold time, tag and account. A trade with several tags is counted under
              each of them.
            </li>
          </ul>
        </Section>

        <Section id="help-filters" title="Filters">
          <p className={p}>
            The bar at the top of the Dashboard, Trades, Calendar and Reports pages narrows everything on the page: the
            date range, the account type, the firm, the contract and the direction. Accounts you have archived (passed,
            failed or closed) are left out unless you turn on <em>Show archived</em>. The choices are in the address of
            the page, so you can bookmark a view.
          </p>
        </Section>

        <Section id="help-data" title="Your data">
          <ul className={list}>
            <li>
              <strong>Download it all.</strong> <Link to="/settings/privacy">Settings &rsaquo; Data &amp; privacy</Link>{' '}
              has a button that downloads everything as a ZIP: your trades and accounts as CSV files, your notes, your
              screenshots and a list of what is inside.
            </li>
            <li>
              <strong>Delete your account.</strong> The same page can delete your account and everything in it. It
              happens seven days later; signing in again before then cancels it.
            </li>
            <li>
              <strong>Who can see it.</strong> Only you. Roundturn&rsquo;s operator tools show an account&rsquo;s plan
              and counts, never a trade, a note or a screenshot, and they cannot sign in as you.
            </li>
            <li>
              <strong>Your sign-ins.</strong> <Link to="/settings/security">Settings &rsaquo; Security</Link> shows
              where you are signed in and your recent activity, and lets you sign a place out.
            </li>
          </ul>
        </Section>

        <Section id="help-problem" title="When something goes wrong">
          <ul className={list}>
            <li>
              If a page says &ldquo;The server had a problem&rdquo; and gives a <strong>reference</strong>, quote it
              when you write to us: it finds the exact request.
            </li>
            <li>
              Pictures that will not load or save, with a message that says they are not working right now, are a
              temporary problem on our side. Try again in a minute.
            </li>
            <li>
              Numbers that differ from Tradovate are most often a different clock: check the two timezones in
              Preferences first.
            </li>
            <li>
              Write to <a href={`mailto:${SUPPORT_ADDRESS}`}>{SUPPORT_ADDRESS}</a> from the address on your account.
              Never send a password or a one-time code: we will never ask for one.
            </li>
          </ul>
        </Section>
      </div>
    </>
  )
}
