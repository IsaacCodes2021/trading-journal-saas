import { useState } from 'react'
import { cx } from '../../components/ui/cx'
import { Button } from '../../components/ui/primitives'
import { CARD_STYLES, type Amounts, type CardStyle } from './cardModel'
import { canCopyImage, copyImage, saveBlob } from './exportImage'

/* The pieces the trade pop-up and the day and week pop-up share. */

export const AMOUNTS: readonly { value: Amounts; label: string }[] = [
  { value: 'dollars', label: 'Dollars' },
  { value: 'points', label: 'Points only' },
]

// The last style used, in this browser only, for every kind of card. The pop-ups work without it (a private window,
// blocked storage).
const STYLE_KEY = 'roundturn.share-style'

export function rememberedStyle(): CardStyle {
  try {
    const saved = window.localStorage.getItem(STYLE_KEY)
    if (CARD_STYLES.some((s) => s.value === saved)) return saved as CardStyle
  } catch {
    // no storage: the default
  }
  return 'gold'
}

export function rememberStyle(style: CardStyle) {
  try {
    window.localStorage.setItem(STYLE_KEY, style)
  } catch {
    // no storage: nothing is remembered
  }
}

export function Segment<T extends string>({
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

/** Download image and Copy image, the fine print, and what happened. `makeImage` draws the card when it is asked. */
export function SaveActions({ makeImage, fileName }: { makeImage: () => Promise<Blob>; fileName: string }) {
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  const download = async () => {
    setBusy(true)
    setStatus({ text: 'Making the image…', error: false })
    try {
      saveBlob(await makeImage(), fileName)
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

  return (
    <>
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
    </>
  )
}
