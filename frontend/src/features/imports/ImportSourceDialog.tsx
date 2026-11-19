import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { SUPPORT_ADDRESS } from '../../components/RouteError'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/primitives'
import type { Platform } from '../../lib/data/model'
import {
  PLATFORMS,
  ROUND_TURN_LOG,
  ROUND_TURN_LOG_FOLDER,
  ROUND_TURN_OVERFLOW,
  TRADESEA_PAGE_ROWS,
  platformInfo,
} from '../../lib/platforms'

const NOT_LISTED = 'not-listed'
type Choice = Platform | typeof NOT_LISTED | ''

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

function NotListed() {
  return (
    <p className="m-0 text-[13px]">
      We import Tradovate, TradeSea and NinjaTrader (through the Round Turn Monitor) today, and more platforms are
      coming. Tell us which one you use: <a href={`mailto:${SUPPORT_ADDRESS}`}>{SUPPORT_ADDRESS}</a>
    </p>
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
 * In the demo (`demo`), only Tradovate files can be read: TradeSea and the Round Turn Monitor say to sign up instead of
 * their reminders.
 */
export function ImportSourceDialog({
  initial,
  timeZone,
  demo,
  onClose,
  onPick,
}: {
  initial: Platform | null
  timeZone: string
  demo: boolean
  onClose: () => void
  onPick: (platform: Platform) => void
}) {
  const [choice, setChoice] = useState<Choice>(initial ?? '')
  const signUpFirst = demo && (choice === 'tradesea' || choice === 'round_turn_monitor')
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
          <option value={NOT_LISTED}>My platform isn’t listed</option>
        </select>
      </div>

      {/* Read out when it appears, so a screen reader hears the reminder for the platform just picked. */}
      <div aria-live="polite" className="empty:hidden">
        {choice === 'tradesea' && demo ? (
          <p className="m-0 text-[13px]">
            Sign up to import TradeSea files. The sample journal reads Tradovate&rsquo;s Performance report only.
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
        {choice === NOT_LISTED ? <NotListed /> : null}
      </div>

      <div className="flex flex-wrap justify-end gap-2">
        {choice === NOT_LISTED ? (
          <Button variant="primary" onClick={onClose}>
            Close
          </Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              disabled={choice === '' || signUpFirst}
              onClick={() => {
                if (choice !== '' && !signUpFirst) onPick(choice)
              }}
            >
              Choose files
            </Button>
          </>
        )}
      </div>
    </Modal>
  )
}
