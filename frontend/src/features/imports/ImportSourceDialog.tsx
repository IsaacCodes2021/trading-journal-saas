import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { SUPPORT_ADDRESS } from '../../components/RouteError'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/primitives'
import type { Platform } from '../../lib/data/model'
import { IMPORT_PROMPT, OTHER_FILE_NAME, parseStatement, statementProblem } from '../../lib/otherPlatform'
import {
  PLATFORMS,
  ROUND_TURN_LOG,
  ROUND_TURN_LOG_FOLDER,
  ROUND_TURN_OVERFLOW,
  TRADESEA_PAGE_ROWS,
  platformInfo,
} from '../../lib/platforms'

type Choice = Platform | ''

function TradeSeaReminder() {
  const guide = platformInfo('tradesea').guide
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3 text-[13px]">
      <p className="m-0">
        In TradeSea, open <strong>Orders</strong>, select <strong>Filled</strong>, and export. TradeSea exports one page
        ({TRADESEA_PAGE_ROWS} orders) at a time, so <strong>export every page</strong>, one right after another, and
        choose all the files here.
      </p>
      <p className="m-0 text-muted">Hold Ctrl (⌘ on a Mac) to choose more than one file.</p>
      <a href={guide} target="_blank" rel="noopener noreferrer" className="self-start">
        How to export from TradeSea
      </a>
    </div>
  )
}

function TradovateReminder({ timeZone }: { timeZone: string }) {
  return (
    <p className="m-0 text-[13px]">
      Export the <strong>Performance</strong> report as a CSV. Times are read as <span className="num">{timeZone}</span>
      .{' '}
      <Link to="/settings" search={(prev) => prev}>
        Change
      </Link>
    </p>
  )
}

function RoundTurnMonitorReminder({ timeZone }: { timeZone: string }) {
  const guide = platformInfo('round_turn_monitor').guide
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3 text-[13px]">
      <p className="m-0">
        Choose a file from the add-on&rsquo;s Export button, or its log: <strong>{ROUND_TURN_LOG}</strong> in{' '}
        {ROUND_TURN_LOG_FOLDER.map((part, i) => (
          <span key={part}>
            {i > 0 ? ' › ' : ''}
            <strong>{part}</strong>
          </span>
        ))}
        . If <strong>{ROUND_TURN_OVERFLOW}</strong> is there too, choose both.
      </p>
      <p className="m-0">
        You can choose the same log every time, or each day&rsquo;s Export file: only new round turns are added.
      </p>
      <p className="m-0 text-muted">
        Times are read as <span className="num">{timeZone}</span>.{' '}
        <Link to="/settings" search={(prev) => prev}>
          Change
        </Link>
      </p>
      <a href={guide} target="_blank" rel="noopener noreferrer" className="self-start">
        How to import from Round Turn Monitor
      </a>
    </div>
  )
}

/**
 * Any other platform: the trader's statement figure, the prompt to copy into an AI chatbot with their export, and what
 * to do with the file it makes (`lib/otherPlatform.ts`).
 */
function OtherPlatformSteps({ statement, onStatement }: { statement: string; onStatement: (text: string) => void }) {
  const [copied, setCopied] = useState<'no' | 'yes' | 'failed'>('no')
  const [showing, setShowing] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(IMPORT_PROMPT)
      setCopied('yes')
    } catch {
      setCopied('failed') // no clipboard (an old browser, or permission refused): the prompt is shown to copy by hand
      setShowing(true)
    }
  }
  return (
    <div className="flex flex-col gap-3 text-[13px]">
      <Field
        id="statement-pnl"
        label="Net P&L on your platform’s statement"
        hint="For the same dates as your export, after fees. Roundturn checks the file against it."
        error={statementProblem(statement)}
      >
        <input
          id="statement-pnl"
          inputMode="decimal"
          autoComplete="off"
          placeholder="-125.50"
          value={statement}
          onChange={(event) => onStatement(event.target.value)}
          className={`${CONTROL} num`}
        />
      </Field>
      <ol className="m-0 flex list-decimal flex-col gap-1.5 pl-5">
        <li>
          Copy the prompt and paste it into ChatGPT, Claude or another AI chatbot. Attach your platform&rsquo;s export,
          and answer its questions.
        </li>
        <li>
          Check that its total net P&amp;L matches your statement, then download the file it makes ({OTHER_FILE_NAME}).
        </li>
        <li>Choose that file here. Roundturn checks every row before anything is saved.</li>
      </ol>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => void copy()}>Copy prompt</Button>
        <Button onClick={() => setShowing((now) => !now)} aria-expanded={showing}>
          {showing ? 'Hide the prompt' : 'See the prompt'}
        </Button>
        <span role="status" className="text-muted empty:hidden">
          {copied === 'yes'
            ? 'Copied.'
            : copied === 'failed'
              ? 'Copying did not work: select the prompt and copy it.'
              : ''}
        </span>
      </div>
      {showing ? (
        <textarea
          readOnly
          aria-label="The prompt"
          value={IMPORT_PROMPT}
          rows={8}
          className="w-full rounded-lg border border-line-strong bg-bg p-3 font-mono text-xs text-ink"
          onFocus={(event) => event.target.select()}
        />
      ) : null}
      <p className="m-0 text-muted">
        Your export goes to the chatbot you choose, under its own terms; Roundturn sends it nowhere. Tell us which
        platform you use, and we may read it directly one day:{' '}
        <a href={`mailto:${SUPPORT_ADDRESS}`}>{SUPPORT_ADDRESS}</a>
      </p>
      <a href={platformInfo('other').guide} target="_blank" rel="noopener noreferrer" className="self-start">
        How to import from another platform
      </a>
    </div>
  )
}

/**
 * "Where are these trades from?": the question the Import page asks before the computer's file window opens, with a
 * reminder of how to export from the platform chosen (docs/design/tradesea-import-plan.md, section 2).
 *
 * `initial` is the platform this account last imported from, or the one already picked on this page; with neither,
 * nothing is chosen and the main button stays off until the trader picks, so nobody imports a TradeSea file as
 * Tradovate by accident. The main button calls `onPick` **inside its own click handler**: browsers open the file
 * window only straight from a click, so the caller opens it there and then.
 *
 * **Other platform** is every platform Roundturn has no reader for: the trader types their statement's net P&L, copies
 * the prompt for an AI chatbot, and chooses the file the chatbot makes. `onPick` hands the statement figure on, for the
 * preview to check the file against; the main button stays off until it is an amount.
 *
 * In the demo (`demo`), only Tradovate files can be read: the other platforms say to sign up instead of their
 * reminders.
 */
export function ImportSourceDialog({
  initial,
  initialStatement = '',
  timeZone,
  demo,
  onClose,
  onPick,
}: {
  initial: Platform | null
  /** The statement figure already typed on this page, for Other platform. */
  initialStatement?: string
  timeZone: string
  demo: boolean
  onClose: () => void
  onPick: (platform: Platform, statement: string) => void
}) {
  const [choice, setChoice] = useState<Choice>(initial ?? '')
  const [statement, setStatement] = useState(initialStatement)
  const signUpFirst = demo && choice !== 'tradovate' && choice !== ''
  const needsStatement = choice === 'other' && !signUpFirst && parseStatement(statement) === null
  return (
    <Modal label="Where are these trades from?" onClose={onClose} className="max-w-[520px]">
      <h2 className="m-0 text-base font-semibold">Where are these trades from?</h2>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="import-platform" className="text-[13px] font-medium">
          Platform
        </label>
        <select
          id="import-platform"
          data-autofocus
          value={choice}
          onChange={(event) => setChoice(event.target.value as Choice)}
          className="h-[42px] min-w-0 rounded-lg border border-line bg-raised px-3 font-[inherit] text-ink"
        >
          {choice === '' ? (
            <option value="" disabled>
              Choose a platform
            </option>
          ) : null}
          {PLATFORMS.map((platform) => (
            <option key={platform.id} value={platform.id}>
              {platform.label}
            </option>
          ))}
        </select>
      </div>

      {/* Read out when it appears, so a screen reader hears the reminder for the platform just picked. */}
      <div aria-live="polite" className="empty:hidden">
        {choice === 'tradesea' && demo ? (
          <p className="m-0 text-[13px]">
            Sign up to import TradeSea files. The sample journal reads Tradovate&rsquo;s Performance report only.
          </p>
        ) : null}
        {choice === 'other' && demo ? (
          <p className="m-0 text-[13px]">
            Sign up to import from another platform. The sample journal reads Tradovate&rsquo;s Performance report only.
          </p>
        ) : null}
        {choice === 'round_turn_monitor' && demo ? (
          <p className="m-0 text-[13px]">
            Sign up to import the Round Turn Monitor&rsquo;s log. The sample journal reads Tradovate&rsquo;s Performance
            report only.
          </p>
        ) : null}
        {choice === 'tradesea' && !demo ? <TradeSeaReminder /> : null}
        {choice === 'round_turn_monitor' && !demo ? <RoundTurnMonitorReminder timeZone={timeZone} /> : null}
        {choice === 'tradovate' ? <TradovateReminder timeZone={timeZone} /> : null}
      </div>
      {/* Outside the live region: typing in it should not make a screen reader read the steps again. */}
      {choice === 'other' && !demo ? <OtherPlatformSteps statement={statement} onStatement={setStatement} /> : null}

      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          disabled={choice === '' || signUpFirst || needsStatement}
          onClick={() => {
            if (choice !== '' && !signUpFirst && !needsStatement) onPick(choice, statement)
          }}
        >
          {choice === 'other' ? 'Choose file' : 'Choose files'}
        </Button>
      </div>
    </Modal>
  )
}
