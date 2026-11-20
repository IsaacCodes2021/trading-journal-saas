import '@fontsource/ibm-plex-serif/latin-500.css'
import '@fontsource/ibm-plex-serif/latin-400-italic.css'
import './share.css'

import { useMemo, useRef, useState } from 'react'
import { Modal } from '../../components/ui/Modal'
import { Button, Spinner } from '../../components/ui/primitives'
import { dayLabel, weekLabel } from '../../lib/calendar'
import type { SharePeriod } from '../../lib/data/model'
import { useDayNote, usePeriodCard } from '../../lib/data/queries'
import { hasNarrowingFilters, type Filters } from '../../lib/filters'
import { CARD_STYLES, notesText } from './cardModel'
import { AMOUNTS, rememberedStyle, rememberStyle, SaveActions, Segment } from './dialogParts'
import { cardToBlob, periodFileName } from './exportImage'
import { periodCardModel, type PeriodOptions } from './periodModel'
import { CardPreview, ShareCard } from './ShareCard'

/**
 * "Share day" and "Share week": a certificate-style picture of one trading day or one week (Sunday to Saturday), in the
 * trade card's styles, with its result, trades, contracts, net points and its best and worst trades. The numbers follow
 * the calendar's filters. The options live only while the pop-up is open; the image is made on the device.
 */
export default function PeriodShareDialog({
  period,
  filters,
  timeZone,
  username,
  onClose,
}: {
  period: SharePeriod
  filters: Filters
  timeZone: string
  username: string | null
  onClose: () => void
}) {
  const week = period.kind === 'week'
  const card = usePeriodCard(period, filters)
  const note = useDayNote(period.start, { enabled: !week })
  const noteHtml = week ? '' : (note.data ?? '')
  const noteText = useMemo(() => notesText(noteHtml), [noteHtml])
  const [options, setOptions] = useState<PeriodOptions>(() => ({
    style: rememberedStyle(),
    notes: true, // shown once the note has loaded, if there is one
    account: true,
    signature: username !== null,
    amounts: 'dollars',
  }))
  const still = useRef<HTMLDivElement>(null)
  const set = (change: Partial<PeriodOptions>) => setOptions((old) => ({ ...old, ...change }))

  const title = week ? 'Share week' : 'Share day'
  const when = week ? `the week of ${weekLabel(period.start)}` : dayLabel(period.start)
  const filtered = hasNarrowingFilters(filters)
  const data = card.data
  const model =
    data && data.summary.trade_count > 0
      ? periodCardModel(data, period, options, { username, timeZone }, noteHtml)
      : null

  const makeImage = () => {
    const el = still.current
    return el ? cardToBlob(el) : Promise.reject(new Error('The card is not ready.'))
  }

  return (
    <Modal label={title} onClose={onClose} className="sc-dialog">
      <div className="sc-dlg-head">
        <h2>{title}</h2>
        <Button icon aria-label="Close" onClick={onClose}>
          &times;
        </Button>
      </div>

      {card.isPending ? (
        <div className="sc-step">
          <Spinner label={`Loading ${when}`} />
        </div>
      ) : null}

      {card.isError ? (
        <div className="sc-step" role="alert">
          <p>The numbers for {when} could not be loaded.</p>
          <div className="sc-step-actions">
            <Button onClick={() => void card.refetch()}>Try again</Button>
          </div>
        </div>
      ) : null}

      {data && !model ? (
        <div className="sc-step" role="status">
          <h3>Nothing to share</h3>
          <p>
            There are no trades {week ? 'in' : 'on'} {when}
            {filtered ? ' with the filters you have on' : ''}.
          </p>
        </div>
      ) : null}

      {data && model ? (
        <div className="sc-dlg-body">
          <div className="sc-dlg-preview">
            <CardPreview model={model} picture={null} />
          </div>
          <div className="sc-dlg-opts">
            <fieldset>
              <legend>Style</legend>
              <Segment
                label="Style"
                options={CARD_STYLES}
                value={options.style}
                onChange={(style) => {
                  set({ style })
                  rememberStyle(style)
                }}
              />
            </fieldset>
            <fieldset>
              <legend>On the card</legend>
              <div className="sc-checks">
                {week ? null : (
                  <label>
                    <input
                      type="checkbox"
                      checked={options.notes && noteText !== ''}
                      disabled={noteText === ''}
                      onChange={(e) => set({ notes: e.target.checked })}
                    />{' '}
                    Day note{' '}
                    <span className="sc-hint">{noteText === '' ? 'This day has no note.' : 'first 3 lines'}</span>
                  </label>
                )}
                <label>
                  <input
                    type="checkbox"
                    checked={options.account}
                    onChange={(e) => set({ account: e.target.checked })}
                  />{' '}
                  {data.accounts.length > 1 ? 'Number of accounts' : 'Account name'}
                </label>
                {username ? (
                  <label>
                    <input
                      type="checkbox"
                      checked={options.signature}
                      onChange={(e) => set({ signature: e.target.checked })}
                    />{' '}
                    Sign as <b>{username}</b>
                  </label>
                ) : (
                  <label>
                    <input type="checkbox" checked={false} disabled onChange={() => undefined} /> Sign your card{' '}
                    <span className="sc-hint">Pick a username in Settings, Preferences to sign your card.</span>
                  </label>
                )}
              </div>
            </fieldset>
            <fieldset>
              <legend>Amounts</legend>
              <Segment
                label="Amounts"
                options={AMOUNTS}
                value={options.amounts}
                onChange={(amounts) => set({ amounts })}
              />
              <p className="sc-fine">
                Points only hides dollars and fees. Net points add up each trade's points per contract.
              </p>
            </fieldset>
            {filtered ? <p className="sc-fine">Only the trades your filters keep are on the card.</p> : null}
            <SaveActions makeImage={makeImage} fileName={periodFileName(period, data.number)} />
          </div>
        </div>
      ) : null}

      {model ? (
        <div className="sc-offstage" aria-hidden="true" inert>
          <ShareCard model={model} picture={null} still cardRef={still} />
        </div>
      ) : null}
    </Modal>
  )
}
