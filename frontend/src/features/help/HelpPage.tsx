import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { SUPPORT_ADDRESS } from '../../components/RouteError'
import { Card } from '../../components/ui/primitives'
import { MAX_IMAGES_PER_TRADE, MAX_IMAGE_MB } from '../../lib/limits'
import {
  ROUND_TURN_LOG,
  ROUND_TURN_LOG_FOLDER,
  ROUND_TURN_OVERFLOW,
  TRADESEA_COLUMNS,
  TRADESEA_PAGE_ROWS,
  platformInfo,
} from '../../lib/platforms'
import { MAX_CSV_MB, REQUIRED_COLUMNS } from '../../lib/tradovate'

/*
 * The answers to the questions that come up most. Everything here is a statement about what the app does today, kept to
 * what the code does (the definitions are `finance.py`'s); the numbers that the server enforces come from `lib/limits.ts`,
 * `lib/tradovate.ts` and `lib/platforms.ts`, which backend tests pin to the server's own.
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
              shown in, and the timezone your Tradovate files (and the Round Turn Monitor&rsquo;s log) are written in.
              If the second is wrong, trades land on the wrong day. TradeSea files name their own timezone, so the
              setting does not apply to them.
            </li>
            <li>
              <strong>Add your accounts.</strong> In{' '}
              <Link to="/settings/accounts">Settings &rsaquo; Prop accounts</Link>, add each account with its firm and
              whether it is an evaluation or a funded account. Fees, rules and prop spend (what it cost, and its
              payouts) are optional and can be added later.
            </li>
            <li>
              <strong>Export your trades.</strong> From Tradovate, export the <em>Performance</em> report as a CSV with
              all its columns (not the orders or fills reports). Menu names change between Tradovate versions, so look
              for the Performance report rather than a fixed path. From TradeSea, see{' '}
              <a href="#help-tradesea">Importing from TradeSea</a>; from NinjaTrader, see{' '}
              <a href="#help-round-turn-monitor">Importing from NinjaTrader (Round Turn Monitor)</a>.
            </li>
            <li>
              <strong>Import it.</strong> On the <Link to="/import">Import</Link> page choose the account, then{' '}
              <em>Choose files</em>: it asks which platform the files are from. Or drop the files on the page and it
              tells from the files. Read the preview: nothing is saved until you confirm. An import can be up to{' '}
              {MAX_CSV_MB} MB.
            </li>
          </ol>
          <details className="text-[13.5px]">
            <summary className="cursor-pointer text-muted">The columns a Tradovate file must have</summary>
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

        <Section id="help-tradesea" title="Importing from TradeSea">
          <ol className={`${list} list-decimal`}>
            <li>
              In TradeSea, open <strong>Orders</strong>, select <strong>Filled</strong>, and export.
            </li>
            <li>
              TradeSea&rsquo;s list shows {TRADESEA_PAGE_ROWS} orders a page and its export saves only the page on
              screen, so <strong>export every page</strong>, one right after another.
            </li>
            <li>
              On the <Link to="/import">Import</Link> page, choose <em>TradeSea: Orders (Filled)</em> and choose all the
              files together (hold Ctrl, or ⌘ on a Mac). They are read as one import, and an order on two pages counts
              once. If you forgot a page, <em>Add files</em> in the preview adds it.
            </li>
          </ol>
          <ul className={list}>
            <li>
              <strong>One account per export.</strong> Export each account on its own and import it into its own account
              here. If you copy trades, the same orders can go into several accounts; the preview points it out.
            </li>
            <li>
              <strong>Times</strong> are read in the timezone the file names (PST, EST and so on), not your CSV timezone
              setting. The setting is used only for a time whose zone is missing or unknown, and the preview says so.
            </li>
            <li>
              <strong>Commissions</strong> come from the account&rsquo;s own Fees tab, as for Tradovate, not from the
              file. The preview compares the two and offers to use the file&rsquo;s rate. If the preview&rsquo;s
              commission total matches TradeSea&rsquo;s Total Charges for the day, no order is missing.
            </li>
            <li>
              <strong>A position still open</strong> at the end of the files is kept until a later import closes it. If
              you were flat, a page is missing: export every page again.
            </li>
            <li>
              The full guide on our site:{' '}
              <a href={platformInfo('tradesea').guide} target="_blank" rel="noopener noreferrer">
                How to export from TradeSea
              </a>
              .
            </li>
          </ul>
          <details className="text-[13.5px]">
            <summary className="cursor-pointer text-muted">The columns a TradeSea file must have</summary>
            <p className={`${p} mt-2`}>
              {TRADESEA_COLUMNS.map((column, i) => (
                <span key={column}>
                  {i > 0 ? ', ' : ''}
                  <code>{column}</code>
                </span>
              ))}
              . If one is missing, the preview says which.
            </p>
          </details>
        </Section>

        <Section id="help-round-turn-monitor" title="Importing from NinjaTrader (Round Turn Monitor)">
          <p className={p}>
            The Round Turn Monitor is an add-on for NinjaTrader 8. It writes a log with one row for every round turn,
            and Roundturn reads that log.
          </p>
          <ol className={`${list} list-decimal`}>
            <li>
              On the <Link to="/import">Import</Link> page, choose <em>{platformInfo('round_turn_monitor').label}</em>.
            </li>
            <li>
              Choose <strong>{ROUND_TURN_LOG}</strong> in {ROUND_TURN_LOG_FOLDER.join(' › ')}. If{' '}
              <strong>{ROUND_TURN_OVERFLOW}</strong> is there too (the add-on writes to it while the log is open in
              Excel), choose both.
            </li>
            <li>
              Choose the same log every time, as it grows: round turns already imported are left alone, and only the new
              ones are added.
            </li>
          </ol>
          <ul className={list}>
            <li>
              <strong>Not the Export button&rsquo;s file.</strong> It has no contract (NQ or MNQ) and no commission, so
              it cannot be read safely. The preview says so if you choose it.
            </li>
            <li>
              <strong>One NinjaTrader account per import.</strong> The log holds every account NinjaTrader tracks,
              Sim101 too. The preview lists them and picks the one this account already holds, or the only real one; you
              can choose another. Practice accounts are never picked for you.
            </li>
            <li>
              <strong>Times</strong> are read in your CSV timezone setting, because the log writes NinjaTrader&rsquo;s
              clock without a timezone.
            </li>
            <li>
              <strong>Commissions</strong> come from the account&rsquo;s own Fees tab, as for every import. The preview
              shows the rate the add-on charges and offers to use it.
            </li>
            <li>
              The full guide on our site:{' '}
              <a href={platformInfo('round_turn_monitor').guide} target="_blank" rel="noopener noreferrer">
                How to import from Round Turn Monitor
              </a>
              .
            </li>
          </ul>
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
              Preferences first. Numbers that differ from TradeSea are most often a missing page or a different
              commission rate: the import preview checks both.
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
