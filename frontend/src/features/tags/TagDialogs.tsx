import { useState, type FormEvent, type ReactNode } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/primitives'
import type { CatalogTag, TagCatalog, TagGroupRef } from '../../lib/data/model'
import { useDeleteGroup, useDeleteTag, useEditTag, useMergeTag, useRenameGroup } from '../../lib/data/queries'
import { ColorPicker } from './ColorPicker'
import { messageOf } from './messages'

const trades = (count: number) => `${count} ${count === 1 ? 'trade' : 'trades'}`

/** The frame every dialog here shares: a title, what it is about, an error if the change was refused, and its buttons. */
function Dialog({
  label,
  onClose,
  pending,
  error,
  children,
  actions,
}: {
  label: string
  onClose: () => void
  pending: boolean
  error: string | null
  children: ReactNode
  actions: ReactNode
}) {
  return (
    <Modal label={label} onClose={pending ? () => undefined : onClose} className="max-w-[460px]">
      <h2 className="m-0 text-base font-semibold">{label}</h2>
      {children}
      {error ? (
        <p role="alert" className="m-0 text-xs text-neg">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">{actions}</div>
    </Modal>
  )
}

export function RenameGroupDialog({ group, onClose }: { group: TagGroupRef; onClose: () => void }) {
  const rename = useRenameGroup()
  const [name, setName] = useState(group.name)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    rename.mutate({ id: group.id, name }, { onSuccess: onClose })
  }
  return (
    <Modal label="Rename category" onClose={rename.isPending ? () => undefined : onClose} className="max-w-[460px]">
      <h2 className="m-0 text-base font-semibold">Rename category</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field id="group-name" label="Category name" error={rename.isError ? messageOf(rename.error) : undefined}>
          <input
            id="group-name"
            data-autofocus
            className={CONTROL}
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoComplete="off"
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={rename.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={rename.isPending || name.trim() === ''}>
            {rename.isPending ? 'Saving…' : 'Rename'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

export function DeleteGroupDialog({
  group,
  tags,
  onClose,
}: {
  group: TagGroupRef
  tags: CatalogTag[]
  onClose: () => void
}) {
  const remove = useDeleteGroup()
  const links = tags.reduce((total, tag) => total + tag.trade_count, 0)
  const what =
    tags.length === 0
      ? `${group.name} has no tags.`
      : `${group.name} and its ${tags.length === 1 ? '1 tag' : `${tags.length} tags`} will be deleted, and taken off ${links === 1 ? '1 trade' : `${links} trades`}.`
  return (
    <Dialog
      label="Delete this category?"
      onClose={onClose}
      pending={remove.isPending}
      error={remove.isError ? messageOf(remove.error) : null}
      actions={
        <>
          <Button onClick={onClose} disabled={remove.isPending}>
            Keep it
          </Button>
          <Button
            variant="primary"
            disabled={remove.isPending}
            onClick={() => remove.mutate(group.id, { onSuccess: onClose })}
          >
            {remove.isPending ? 'Deleting…' : 'Delete category'}
          </Button>
        </>
      }
    >
      <p className="m-0">{what} The trades themselves stay.</p>
    </Dialog>
  )
}

export function EditTagDialog({
  tag,
  catalog,
  onClose,
}: {
  tag: CatalogTag
  catalog: TagCatalog
  onClose: () => void
}) {
  const edit = useEditTag()
  const [name, setName] = useState(tag.name)
  const [groupId, setGroupId] = useState(tag.group_id)
  const [color, setColor] = useState(tag.color)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    edit.mutate({ id: tag.id, edit: { name, group_id: groupId, color } }, { onSuccess: onClose })
  }
  return (
    <Modal label="Edit tag" onClose={edit.isPending ? () => undefined : onClose} className="max-w-[520px]">
      <h2 className="m-0 text-base font-semibold">Edit tag</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field id="tag-name" label="Name">
          <input
            id="tag-name"
            data-autofocus
            className={CONTROL}
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field id="tag-group" label="Category" hint="Moving a tag keeps it on its trades.">
          <select
            id="tag-group"
            className={CONTROL}
            value={groupId}
            onChange={(event) => setGroupId(Number(event.target.value))}
          >
            {catalog.groups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </Field>
        <ColorPicker value={color} onChange={setColor} presets={catalog.presets} custom={catalog.custom_colors} />
        {edit.isError ? (
          <p role="alert" className="m-0 text-xs text-neg">
            {messageOf(edit.error)}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={edit.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={edit.isPending || name.trim() === ''}>
            {edit.isPending ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

export function MergeTagDialog({
  tag,
  catalog,
  onClose,
}: {
  tag: CatalogTag
  catalog: TagCatalog
  onClose: () => void
}) {
  const merge = useMergeTag()
  const others = catalog.tags.filter((t) => t.id !== tag.id)
  const [intoId, setIntoId] = useState<number | null>(others[0]?.id ?? null)
  const groupName = (id: number) => catalog.groups.find((g) => g.id === id)?.name ?? ''
  const target = others.find((t) => t.id === intoId)
  return (
    <Dialog
      label="Merge this tag?"
      onClose={onClose}
      pending={merge.isPending}
      error={merge.isError ? messageOf(merge.error) : null}
      actions={
        <>
          <Button onClick={onClose} disabled={merge.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={merge.isPending || intoId === null}
            onClick={() => intoId !== null && merge.mutate({ id: tag.id, intoId }, { onSuccess: onClose })}
          >
            {merge.isPending ? 'Merging…' : 'Merge'}
          </Button>
        </>
      }
    >
      {others.length === 0 ? (
        <p className="m-0">There is no other tag to merge {tag.name} into.</p>
      ) : (
        <>
          <p className="m-0">
            Every trade with {tag.name} ({trades(tag.trade_count)}) gets the tag you pick instead, then {tag.name} is
            deleted. A trade that has both keeps the one.
          </p>
          <Field id="merge-into" label="Merge into">
            <select
              id="merge-into"
              data-autofocus
              className={CONTROL}
              value={intoId ?? ''}
              onChange={(event) => setIntoId(Number(event.target.value))}
            >
              {others.map((t) => (
                <option key={t.id} value={t.id}>
                  {groupName(t.group_id)} · {t.name}
                </option>
              ))}
            </select>
          </Field>
          {target ? (
            <p className="m-0 text-xs text-muted">
              {target.name} is on {trades(target.trade_count)} today.
            </p>
          ) : null}
        </>
      )}
    </Dialog>
  )
}

export function DeleteTagDialog({ tag, onClose }: { tag: CatalogTag; onClose: () => void }) {
  const remove = useDeleteTag()
  return (
    <Dialog
      label="Delete this tag?"
      onClose={onClose}
      pending={remove.isPending}
      error={remove.isError ? messageOf(remove.error) : null}
      actions={
        <>
          <Button onClick={onClose} disabled={remove.isPending}>
            Keep it
          </Button>
          <Button
            variant="primary"
            disabled={remove.isPending}
            onClick={() => remove.mutate(tag.id, { onSuccess: onClose })}
          >
            {remove.isPending ? 'Deleting…' : 'Delete tag'}
          </Button>
        </>
      }
    >
      <p className="m-0">
        {tag.name} will be taken off {tag.trade_count === 1 ? '1 trade' : `${tag.trade_count} trades`}. The trades stay.
      </p>
    </Dialog>
  )
}
