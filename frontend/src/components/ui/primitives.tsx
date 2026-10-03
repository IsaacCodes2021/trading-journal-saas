import type { ComponentProps, HTMLAttributes, ReactNode } from 'react'
import { formatPnl, pnlTone } from '../../lib/format'
import type { Direction } from '../../lib/filters'
import { cx } from './cx'

/** A bordered panel. Pass `as` semantics through a wrapping section element where the content is a landmark. */
export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('rounded-[10px] border border-line bg-panel', className)} {...rest} />
}

type ButtonProps = ComponentProps<'button'> & {
  variant?: 'default' | 'primary' | 'ghost'
  /** A square icon button: it must have an aria-label. */
  icon?: boolean
}

export function Button({ variant = 'default', icon = false, className, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        'inline-flex h-9 cursor-pointer items-center justify-center gap-1.5 rounded-lg border font-[inherit] text-ink',
        icon ? 'w-9 px-0' : 'px-3',
        variant === 'default' && 'border-line bg-raised hover:border-line-strong',
        variant === 'primary' && 'border-accent bg-accent font-semibold text-accent-on hover:bg-accent-hover',
        variant === 'ghost' && 'border-transparent bg-transparent hover:bg-raised',
        'disabled:cursor-default disabled:opacity-50',
        className,
      )}
      {...rest}
    />
  )
}

/** A row of mutually exclusive choices, as buttons that say which one is pressed. */
export function Segmented<T extends string>({
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
    <div role="group" aria-label={label} className="flex overflow-hidden rounded-lg border border-line">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            'h-[34px] cursor-pointer border-0 px-3 font-[inherit]',
            option.value === value ? 'bg-selected text-ink' : 'bg-transparent text-muted hover:text-ink',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** A tag chip, with the tag's own colour as a dot. */
export function Chip({ color, children, className }: { color?: string; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        'inline-flex h-[22px] items-center rounded-full border border-line bg-raised px-2 text-xs',
        className,
      )}
    >
      {color ? (
        <span
          aria-hidden="true"
          className="mr-1.5 inline-block size-[7px] rounded-full"
          style={{ background: color }}
        />
      ) : null}
      {children}
    </span>
  )
}

export function DirectionBadge({ direction }: { direction: Direction }) {
  return (
    <span
      className={cx(
        'inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium',
        direction === 'long' ? 'bg-long-bg text-pos' : 'bg-short-bg text-neg',
      )}
    >
      {direction === 'long' ? 'Long' : 'Short'}
    </span>
  )
}

/** A signed P&L in green or red. The sign is in the text, so colour is never the only signal. */
export function Pnl({ value, className }: { value: string; className?: string }) {
  const tone = pnlTone(value)
  return (
    <span className={cx('num', tone === 'pos' && 'text-pos', tone === 'neg' && 'text-neg', className)}>
      {formatPnl(value)}
    </span>
  )
}

export function StatTile({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    // Two to a row on a phone (smaller figures), up to six across on a desktop.
    <Card className="min-w-[140px] flex-[1_1_150px] p-3 sm:min-w-[170px] sm:flex-[1_1_190px] sm:p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="num mt-1 text-xl font-semibold [overflow-wrap:anywhere] sm:text-[26px]">{children}</div>
      {note ? <div className="mt-0.5 text-xs text-muted">{note}</div> : null}
    </Card>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <p role="status" className="p-6 text-muted">
      {label}…
    </p>
  )
}
