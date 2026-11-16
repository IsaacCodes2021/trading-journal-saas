import '@fontsource/ibm-plex-serif/latin-500.css'
import '@fontsource/ibm-plex-serif/latin-400-italic.css'
import './share.css'

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { cx } from '../../components/ui/cx'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/primitives'
import type { ScreenshotRef, TradeDetail } from '../../lib/data/model'
import { useAddScreenshots } from '../../lib/data/queries'
import { MAX_IMAGE_MB } from '../../lib/limits'
import { CARD_STYLES, cardModel, notesText, type Amounts, type CardOptions, type CardStyle } from './cardModel'
import { CENTRED, type Crop, type Focus } from './crop'
import { Cropper } from './Cropper'
import { canCopyImage, cardToBlob, copyImage, imageFileName, saveBlob } from './exportImage'
import { CardPreview, ShareCard, type CardPicture } from './ShareCard'

type Step = 'shot' | 'crop' | 'card'

const STEPS: readonly { step: Step; name: string }[] = [
  { step: 'shot', name: 'Screenshot' },
  { step: 'crop', name: 'Crop' },
  { step: 'card', name: 'Card' },
]

const AMOUNTS: readonly { value: Amounts; label: string }[] = [
  { value: 'dollars', label: 'Dollars' },
  { value: 'points', label: 'Points only' },
]

const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp'
const isImage = (file: File) => file.type.startsWith('image/')
/** "entry.png" to "entry", as under the trade page's thumbnails. */
const labelOf = (shot: ScreenshotRef) => shot.original_name.replace(/\.[^.]+$/, '')

/** The card's picture space with everything switched on, until the card has been measured (and in tests, where
 * nothing has a size). */
const SLOT_BEFORE_MEASURING: Record<CardStyle, readonly [number, number]> = {
  gold: [446, 134],
  holo: [456, 154],
  paper: [438, 102],
}

// The last style used, in this browser only. The pop-up works without it (a private window, blocked storage).
const STYLE_KEY = 'roundturn.share-style'

function rememberedStyle(): CardStyle {
  try {
    const saved = window.localStorage.getItem(STYLE_KEY)
    if (CARD_STYLES.some((s) => s.value === saved)) return saved as CardStyle
  } catch {
    // no storage: the default
  }
  return 'gold'
}

function rememberStyle(style: CardStyle) {
  try {
    window.localStorage.setItem(STYLE_KEY, style)
  } catch {
    // no storage: nothing is remembered
  }
}

function Segment<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (next: T) => void
}) {
  return (
    <div className="sc-seg" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/**
 * "Share trade": a certificate-style picture of one trade, to download or copy. Three steps: choose (or add) a
 * screenshot, crop it to the card's picture space, then the card with its style and options. The crop and the options
 * live only while the pop-up is open; an added screenshot is saved to the trade. The image is made on the device.
 */
export default function ShareDialog({
  trade,
  timeZone,
  username,
  onClose,
}: {
  trade: TradeDetail
  timeZone: string
  username: string | null
  onClose: () => void
}) {
  const add = useAddScreenshots(trade.id)
  const notes = useMemo(() => notesText(trade.notes_html), [trade.notes_html])
  const [added, setAdded] = useState<ScreenshotRef | null>(null) // uploaded here, perhaps before the trade is read again
  // Only a screenshot with a picture can go on a card: the sample data's have none.
  const shots = useMemo(() => {
    const usable = trade.screenshots.filter((shot) => shot.src)
    return added && !usable.some((shot) => shot.id === added.id) ? [...usable, added] : usable
  }, [trade.screenshots, added])
  const sampleOnly = trade.screenshots.length > 0 && shots.length === 0

  const [step, setStep] = useState<Step>(sampleOnly ? 'card' : 'shot') // with nothing to show, steps 1 and 2 are skipped
  const [crop, setCrop] = useState<Crop | null>(() => (shots[0] ? { screenshotId: shots[0].id, ...CENTRED } : null))
  const [options, setOptions] = useState<CardOptions>(() => ({
    style: rememberedStyle(),
    shot: shots.length > 0,
    notes: notes !== '',
    tags: trade.tags.length > 0,
    account: true,
    signature: username !== null,
    amounts: 'dollars',
  }))
  const [slot, setSlot] = useState<readonly [number, number] | null>(null)
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const still = useRef<HTMLDivElement>(null)
  const stepBox = useRef<HTMLDivElement>(null)

  const picked = shots.find((shot) => shot.id === crop?.screenshotId) ?? null
  const shotOnCard = options.shot && picked !== null
  const set = (change: Partial<CardOptions>) => setOptions((old) => ({ ...old, ...change }))

  // The card the pop-up shows, and the still copy out of sight that the image is made from. While cropping, the copy
  // has the picture switched on, so it measures the shape the crop frame takes.
  const context = { username, timeZone }
  const model = cardModel(trade, { ...options, shot: shotOnCard }, context)
  const stillModel = step === 'crop' ? cardModel(trade, { ...options, shot: true }, context) : model
  const picture: CardPicture | null = picked?.src && crop ? { src: picked.src, focus: crop } : null
  const measured = useCallback((width: number, height: number) => setSlot([width, height]), [])

  // Moving between steps moves focus into the new step, so it never falls behind the pop-up.
  const firstStep = useRef(true)
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false
      return
    }
    // Without scrolling to it: on a phone the new step is read from its top (the card preview, on step 3).
    stepBox.current?.querySelector<HTMLElement>('[data-step-focus]')?.focus({ preventScroll: true })
    const panel = stepBox.current?.closest('[role="dialog"]')
    if (panel) panel.scrollTop = 0
  }, [step])

  const pick = (shot: ScreenshotRef) => {
    if (shot.id !== crop?.screenshotId) setCrop({ screenshotId: shot.id, ...CENTRED }) // a new picture, a new crop
  }

  const take = (files: readonly File[]) => {
    const image = files.find(isImage)
    if (!image) {
      if (files.length > 0) setRefusal('Only images can be added here.')
      return
    }
    setRefusal(null)
    add.mutate([image], {
      onSuccess: ([shot]) => {
        if (!shot) return
        setAdded(shot)
        if (shot.src) {
          setCrop({ screenshotId: shot.id, ...CENTRED })
          set({ shot: true })
          setStep('crop')
        } else {
          set({ shot: false }) // kept on the trade, but there is no picture to show here
          setStep('card')
        }
      },
      onError: (error) => setRefusal(error instanceof Error ? error.message : 'Could not add the image.'),
    })
  }

  // A pasted picture is the pop-up's, never the trade page's behind it; it is taken while the upload box is showing.
  const takePaste = useRef<((files: File[]) => void) | null>(null)
  useEffect(() => {
    takePaste.current = step === 'shot' && shots.length === 0 && !add.isPending ? take : null
  })
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const files = [...(event.clipboardData?.files ?? [])].filter(isImage)
      if (files.length === 0) return
      event.preventDefault()
      event.stopPropagation()
      takePaste.current?.(files)
    }
    window.addEventListener('paste', onPaste, true)
    return () => window.removeEventListener('paste', onPaste, true)
  }, [])

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    take([...event.dataTransfer.files])
  }

  const withoutShot = () => {
    set({ shot: false })
    setStep('card')
  }

  const makeImage = () => {
    const card = still.current
    return card ? cardToBlob(card) : Promise.reject(new Error('The card is not ready.'))
  }

  const download = async () => {
    setBusy(true)
    setStatus({ text: 'Making the image…', error: false })
    try {
      saveBlob(await makeImage(), imageFileName(trade))
      setStatus({ text: 'Image saved.', error: false })
    } catch {
      setStatus({ text: 'The image could not be made. Try again, or try another browser.', error: true })
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    setBusy(true)
    setStatus({ text: 'Making the image…', error: false })
    try {
      await copyImage(makeImage())
      setStatus({ text: 'Image copied.', error: false })
    } catch {
      setStatus({ text: 'The image could not be copied. Try Download image instead.', error: true })
    } finally {
      setBusy(false)
    }
  }

  const at = STEPS.findIndex((s) => s.step === step)
  const frame = slot ?? SLOT_BEFORE_MEASURING[options.style]

  return (
    <Modal label="Share trade" onClose={onClose} className="sc-dialog">
      <div className="sc-dlg-head">
        <h2>Share trade</h2>
        <Button icon aria-label="Close" onClick={onClose}>
          &times;
        </Button>
      </div>
      <ol className="sc-steps" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s.step} aria-current={i === at ? 'step' : undefined} className={i < at ? 'sc-done' : undefined}>
            <b>{i + 1}</b>
            {s.name}
          </li>
        ))}
      </ol>

      <div ref={stepBox}>
        {step === 'shot' && shots.length > 0 ? (
          <div className="sc-step">
            <h3 tabIndex={-1} data-step-focus>
              Include a screenshot?
            </h3>
            <p>Pick the one to show on your card. You crop it to fit in the next step.</p>
            <ul className="sc-thumbs">
              {shots.map((shot) => (
                <li key={shot.id}>
                  <button
                    type="button"
                    className="sc-thumb"
                    aria-pressed={shot.id === crop?.screenshotId}
                    aria-label={`Use ${shot.original_name}`}
                    onClick={() => pick(shot)}
                  >
                    <img src={shot.src ?? undefined} alt="" />
                    <span>{labelOf(shot)}</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="sc-step-actions">
              <Button onClick={withoutShot}>Share without a screenshot</Button>
              <Button
                variant="primary"
                onClick={() => {
                  set({ shot: true })
                  setStep('crop')
                }}
              >
                Crop screenshot
              </Button>
            </div>
          </div>
        ) : null}

        {step === 'shot' && shots.length === 0 ? (
          <div className="sc-step">
            <h3 tabIndex={-1} data-step-focus>
              Add a screenshot?
            </h3>
            <p>
              This trade has no screenshot yet. Add one to show your chart on the card. It is saved to the trade's
              screenshots.
            </p>
            <div
              onDragOver={(e) => e.preventDefault()}
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              className={cx('sc-drop', dragging && 'sc-dragging')}
            >
              Drop an image here, paste from the clipboard, or{' '}
              <button type="button" className="sc-linkbtn" onClick={() => fileInput.current?.click()}>
                browse
              </button>
              <input
                ref={fileInput}
                type="file"
                accept={ACCEPT}
                tabIndex={-1}
                aria-label="Choose an image to add"
                className="sr-only"
                onChange={(event) => {
                  take([...(event.target.files ?? [])])
                  event.target.value = '' // so choosing the same file again still counts
                }}
              />
              <div className="sc-fine">PNG, JPEG, GIF or WebP, up to {MAX_IMAGE_MB} MB</div>
            </div>
            {add.isPending ? <p role="status">Adding…</p> : null}
            {refusal ? (
              <p role="alert" className="sc-error">
                {refusal}
              </p>
            ) : null}
            <div className="sc-step-actions">
              <Button onClick={withoutShot}>Share without a screenshot</Button>
            </div>
          </div>
        ) : null}

        {step === 'crop' && picked?.src && crop ? (
          <div className="sc-step">
            <h3>Crop your screenshot</h3>
            <p>
              Drag the picture to move it and use the slider to zoom. The frame is the exact shape of the picture on
              your card.
            </p>
            <Cropper
              key={picked.id}
              src={picked.src}
              aspect={frame}
              focus={crop}
              onChange={(focus: Focus) => setCrop({ ...focus, screenshotId: picked.id })}
            />
            <div className="sc-step-actions">
              <Button onClick={() => setStep('shot')}>&larr; Back</Button>
              <Button variant="primary" onClick={() => setStep('card')}>
                Use this crop
              </Button>
            </div>
          </div>
        ) : null}

        {step === 'card' ? (
          <div className="sc-dlg-body">
            <div className="sc-dlg-preview">
              <CardPreview model={model} picture={shotOnCard ? picture : null} />
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
                  {shots.length > 0 ? (
                    <div className="sc-opt-row">
                      <label>
                        <input
                          type="checkbox"
                          checked={options.shot}
                          onChange={(e) => set({ shot: e.target.checked })}
                        />{' '}
                        Screenshot
                      </label>
                      {options.shot ? (
                        <button type="button" className="sc-linkbtn" onClick={() => setStep('crop')}>
                          Crop
                        </button>
                      ) : null}
                    </div>
                  ) : sampleOnly ? (
                    <label>
                      <input type="checkbox" checked={false} disabled onChange={() => undefined} /> Screenshot{' '}
                      <span className="sc-hint">No preview in the sample data</span>
                    </label>
                  ) : (
                    <div className="sc-opt-row">
                      <span className="text-muted">No screenshot</span>
                      <button type="button" className="sc-linkbtn" onClick={() => setStep('shot')}>
                        Add a screenshot
                      </button>
                    </div>
                  )}
                  <label>
                    <input
                      type="checkbox"
                      checked={options.notes && notes !== ''}
                      disabled={notes === ''}
                      onChange={(e) => set({ notes: e.target.checked })}
                    />{' '}
                    Notes{' '}
                    <span className="sc-hint">
                      {notes === '' ? 'This trade has no notes.' : shotOnCard ? 'first 3 lines' : 'first 9 lines'}
                    </span>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={options.tags && trade.tags.length > 0}
                      disabled={trade.tags.length === 0}
                      onChange={(e) => set({ tags: e.target.checked })}
                    />{' '}
                    Tags
                    {trade.tags.length === 0 ? <span className="sc-hint">This trade has no tags.</span> : null}
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={options.account}
                      onChange={(e) => set({ account: e.target.checked })}
                    />{' '}
                    Account name
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
                <p className="sc-fine">Points only hides dollars, quantity and fees.</p>
              </fieldset>
              <div className="sc-dlg-actions">
                <Button variant="primary" onClick={() => void download()} disabled={busy} data-step-focus>
                  Download image
                </Button>
                {canCopyImage() ? (
                  <Button onClick={() => void copy()} disabled={busy}>
                    Copy image
                  </Button>
                ) : null}
              </div>
              <p className="sc-fine">
                A 1080 × 1350 image, made on your device. Nothing is posted until you share it yourself.
              </p>
              <p className={cx('sc-fine sc-status', status?.error && 'sc-is-error')} aria-live="polite">
                {status?.text}
              </p>
            </div>
          </div>
        ) : null}
      </div>

      {step !== 'shot' ? (
        <div className="sc-offstage" aria-hidden="true" inert>
          <ShareCard
            model={stillModel}
            picture={stillModel.shot ? picture : null}
            still
            cardRef={still}
            onShotSize={measured}
          />
        </div>
      ) : null}
    </Modal>
  )
}
