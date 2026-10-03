import { useState, type FormEvent } from 'react'
import { Popover } from '../../components/ui/Popover'
import { Button, Card } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { TagCatalog, TagRef, TradeDetail } from '../../lib/data/model'
import { useAddTag, useSetTradeTag } from '../../lib/data/queries'

function TagToggle({ tag, on, onToggle, busy }: { tag: TagRef; on: boolean; onToggle: () => void; busy: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={busy}
      onClick={onToggle}
      className={cx(
        'inline-flex h-[26px] cursor-pointer items-center rounded-full border px-2.5 font-[inherit] text-[13px] text-ink disabled:cursor-default disabled:opacity-60',
        on ? 'border-chip-selected-line bg-chip-selected-bg' : 'border-line bg-raised hover:border-line-strong',
      )}
    >
      {on ? (
        <span
          aria-hidden="true"
          className="mr-1.5 inline-block size-[7px] rounded-full"
          style={{ background: tag.color }}
        />
      ) : null}
      {tag.name}
    </button>
  )
}

/** A small form in a popover: pick the group, name the tag. A name that already exists in the group is reused. */
function AddTagForm({ catalog, tradeId, close }: { catalog: TagCatalog; tradeId: number; close: () => void }) {
  const add = useAddTag(tradeId)
  const [groupId, setGroupId] = useState(catalog.groups[0]?.id ?? 0)
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    add.mutate(
      { groupId, name },
      {
        onSuccess: close,
        onError: (error) => setProblem(error instanceof Error ? error.message : 'Could not add it.'),
      },
    )
  }

  return (
    <form onSubmit={submit} className="flex min-w-[240px] flex-col gap-2.5 p-1">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Group
        <select
          value={groupId}
          onChange={(e) => setGroupId(Number(e.target.value))}
          className="h-9 rounded-lg border border-line bg-raised px-2 font-[inherit] text-[14px] text-ink"
        >
          {catalog.groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Tag name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={40}
          required
          className="h-9 rounded-lg border border-line-strong bg-bg px-2.5 font-[inherit] text-[14px] text-ink"
        />
      </label>
      {problem ? (
        <p role="alert" className="m-0 text-xs text-neg">
          {problem}
        </p>
      ) : null}
      <Button type="submit" variant="primary" disabled={add.isPending}>
        Add tag
      </Button>
    </form>
  )
}

/** The trade's tags, by group, as toggles. Any number per group; a tag shows a dot in its own colour once chosen. */
export function TagsCard({ trade, catalog }: { trade: TradeDetail; catalog: TagCatalog | undefined }) {
  const toggle = useSetTradeTag(trade.id)
  const chosen = new Set(trade.tags.map((t) => t.id))
  return (
    <Card className="px-5 py-4" role="region" aria-label="Tags">
      <h2 className="m-0 mb-2.5 text-[13px] font-semibold tracking-wide text-muted uppercase">Tags</h2>
      {catalog ? (
        <>
          <div className="flex flex-col gap-3.5">
            {catalog.groups.map((group) => (
              <div key={group.id} role="group" aria-label={group.name}>
                <div className="mb-1.5 text-xs text-muted">{group.name}</div>
                <div className="flex flex-wrap gap-1.5">
                  {catalog.tags
                    .filter((tag) => tag.group_id === group.id)
                    .map((tag) => (
                      <TagToggle
                        key={tag.id}
                        tag={tag}
                        on={chosen.has(tag.id)}
                        busy={toggle.isPending && toggle.variables?.tagId === tag.id}
                        onToggle={() => toggle.mutate({ tagId: tag.id, on: !chosen.has(tag.id) })}
                      />
                    ))}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3.5">
            <Popover label="Add a tag" button="+ Add tag">
              {(close) => <AddTagForm catalog={catalog} tradeId={trade.id} close={close} />}
            </Popover>
          </div>
        </>
      ) : (
        <p role="status" className="m-0 text-muted">
          Loading tags…
        </p>
      )}
      {toggle.isError ? (
        <p role="alert" className="mt-2 mb-0 text-xs text-neg">
          That change was not saved. Try again.
        </p>
      ) : null}
    </Card>
  )
}
