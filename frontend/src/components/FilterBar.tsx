import type { ReactNode } from 'react'
import {
  ACCOUNT_TYPES,
  ACCOUNT_TYPE_LABELS,
  DIRECTIONS,
  DIRECTION_LABELS,
  PRESETS,
  PRESET_LABELS,
  summarizeSelection,
  type AccountType,
  type Direction,
  type Filters,
} from '../lib/filters'
import type { FilterOptions } from '../lib/data/model'
import { Chevron, Popover } from './ui/Popover'
import { Card, Segmented } from './ui/primitives'

const PRESET_OPTIONS = PRESETS.map((value) => ({ value, label: PRESET_LABELS[value] }))

function ChoiceFilter<T extends string>({
  label,
  allLabel,
  options,
  value,
  onChange,
}: {
  label: string
  allLabel: string
  options: readonly { value: T; label: string }[]
  value: T | undefined
  onChange: (next: T | undefined) => void
}) {
  const current = options.find((o) => o.value === value)?.label ?? allLabel
  return (
    <Popover
      label={label}
      button={
        <>
          {current} <Chevron />
        </>
      }
    >
      {(close) => (
        <div className="flex flex-col gap-1">
          {[{ value: undefined, label: allLabel }, ...options].map((option) => (
            <label
              key={option.value ?? 'all'}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-raised"
            >
              <input
                type="radio"
                name={label}
                checked={option.value === value}
                onChange={() => {
                  onChange(option.value)
                  close()
                }}
              />
              {option.label}
            </label>
          ))}
        </div>
      )}
    </Popover>
  )
}

function MultiFilter({
  label,
  prefix,
  allLabel,
  noun,
  options,
  selected,
  onChange,
}: {
  label: string
  prefix?: string
  allLabel: string
  noun: string
  options: readonly string[]
  selected: string[] | undefined
  onChange: (next: string[] | undefined) => void
}) {
  const summary = summarizeSelection(selected, allLabel, noun)
  const toggle = (option: string, on: boolean) => {
    const next = on ? [...(selected ?? []), option] : (selected ?? []).filter((s) => s !== option)
    onChange(next.length > 0 ? next : undefined)
  }
  return (
    <Popover
      label={label}
      button={
        <>
          {selected?.length && prefix ? `${prefix}: ` : ''}
          {summary} <Chevron />
        </>
      }
    >
      {() => (
        <div className="flex flex-col gap-1">
          {options.length === 0 ? <p className="px-2 py-1.5 text-muted">Nothing to choose yet</p> : null}
          {options.map((option) => (
            <label key={option} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-raised">
              <input
                type="checkbox"
                checked={selected?.includes(option) ?? false}
                onChange={(e) => toggle(option, e.target.checked)}
              />
              {option}
            </label>
          ))}
        </div>
      )}
    </Popover>
  )
}

/** The bar every data page shares. Each change goes to `onChange`, which writes it to the URL. */
export function FilterBar({
  filters,
  options,
  onChange,
  summary,
}: {
  filters: Filters
  options: FilterOptions | undefined
  onChange: (next: Filters) => void
  summary?: ReactNode
}) {
  const set = (patch: Partial<Filters>) => {
    // A cleared filter arrives as undefined; leave the key out so it disappears from the URL.
    const next = Object.fromEntries(Object.entries({ ...filters, ...patch }).filter(([, value]) => value !== undefined))
    onChange(next as unknown as Filters)
  }
  return (
    <Card className="flex flex-wrap items-center gap-2.5 px-4 py-3" role="search" aria-label="Filters">
      <Segmented
        label="Date range"
        options={PRESET_OPTIONS}
        value={filters.preset}
        onChange={(preset) => set({ preset })}
      />
      <ChoiceFilter<AccountType>
        label="Account type"
        allLabel="All account types"
        options={ACCOUNT_TYPES.map((value) => ({ value, label: ACCOUNT_TYPE_LABELS[value] }))}
        value={filters.account_type}
        onChange={(account_type) => set({ account_type })}
      />
      <MultiFilter
        label="Firms"
        prefix="Firms"
        allLabel="All firms"
        noun="firms"
        options={options?.firms ?? []}
        selected={filters.firms}
        onChange={(firms) => set({ firms })}
      />
      <MultiFilter
        label="Symbols"
        allLabel="All symbols"
        noun="symbols"
        options={options?.symbol_roots ?? []}
        selected={filters.symbol_roots}
        onChange={(symbol_roots) => set({ symbol_roots })}
      />
      <ChoiceFilter<Direction>
        label="Direction"
        allLabel={DIRECTION_LABELS.both}
        options={DIRECTIONS.map((value) => ({ value, label: DIRECTION_LABELS[value] }))}
        value={filters.direction}
        onChange={(direction) => set({ direction })}
      />
      <span className="grow" />
      {summary ? <span className="text-[13px] text-muted">{summary}</span> : null}
    </Card>
  )
}
