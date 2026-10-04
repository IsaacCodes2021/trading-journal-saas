import { useState, type FormEvent } from 'react'
import { Button, Card, Chip, Spinner } from '../../components/ui/primitives'
import { CONTROL } from '../../components/ui/Field'
import type { CatalogTag, TagCatalog, TagGroupRef } from '../../lib/data/model'
import { useCreateGroup, useCreateTag, useTagCatalog } from '../../lib/data/queries'
import { SectionHeader } from '../settings/SettingsLayout'
import { DeleteGroupDialog, DeleteTagDialog, EditTagDialog, MergeTagDialog, RenameGroupDialog } from './TagDialogs'
import { messageOf } from './messages'

type Dialog =
  | { kind: 'rename'; group: TagGroupRef }
  | { kind: 'delete-group'; group: TagGroupRef }
  | { kind: 'edit'; tag: CatalogTag }
  | { kind: 'merge'; tag: CatalogTag }
  | { kind: 'delete-tag'; tag: CatalogTag }
  | null

const trades = (count: number) => `${count} ${count === 1 ? 'trade' : 'trades'}`

/** A one-line form: a text box and a button. It clears itself when the change worked. */
function AddForm({
  label,
  placeholder,
  button,
  pending,
  error,
  onSubmit,
}: {
  label: string
  placeholder: string
  button: string
  pending: boolean
  error: string | null
  onSubmit: (name: string, done: () => void) => void
}) {
  const [name, setName] = useState('')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSubmit(name, () => setName(''))
  }
  return (
    <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
      <input
        aria-label={label}
        className={`${CONTROL} max-w-[280px] flex-1`}
        placeholder={placeholder}
        value={name}
        onChange={(event) => setName(event.target.value)}
        autoComplete="off"
      />
      <Button type="submit" disabled={pending || name.trim() === ''}>
        {pending ? 'Adding…' : button}
      </Button>
      {error ? (
        <p role="alert" className="m-0 w-full text-xs text-neg">
          {error}
        </p>
      ) : null}
    </form>
  )
}

function TagRow({ tag, onDialog }: { tag: CatalogTag; onDialog: (dialog: Dialog) => void }) {
  return (
    <li className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-2">
      <Chip color={tag.color}>{tag.name}</Chip>
      <span className="text-xs text-muted">{trades(tag.trade_count)}</span>
      <span className="grow" />
      <Button variant="ghost" aria-label={`Edit tag ${tag.name}`} onClick={() => onDialog({ kind: 'edit', tag })}>
        Edit
      </Button>
      <Button variant="ghost" aria-label={`Merge tag ${tag.name}`} onClick={() => onDialog({ kind: 'merge', tag })}>
        Merge…
      </Button>
      <Button
        variant="ghost"
        aria-label={`Delete tag ${tag.name}`}
        onClick={() => onDialog({ kind: 'delete-tag', tag })}
      >
        Delete
      </Button>
    </li>
  )
}

function GroupCard({
  group,
  tags,
  onDialog,
}: {
  group: TagGroupRef
  tags: CatalogTag[]
  onDialog: (dialog: Dialog) => void
}) {
  const create = useCreateTag()
  return (
    <Card className="overflow-hidden" aria-label={`Category ${group.name}`} role="group">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <h2 className="m-0 text-base font-semibold">{group.name}</h2>
        <span className="text-xs text-muted">{tags.length === 1 ? '1 tag' : `${tags.length} tags`}</span>
        <span className="grow" />
        <Button
          variant="ghost"
          aria-label={`Rename category ${group.name}`}
          onClick={() => onDialog({ kind: 'rename', group })}
        >
          Rename
        </Button>
        <Button
          variant="ghost"
          aria-label={`Delete category ${group.name}`}
          onClick={() => onDialog({ kind: 'delete-group', group })}
        >
          Delete
        </Button>
      </div>
      {tags.length === 0 ? (
        <p className="m-0 border-t border-line px-4 py-3 text-muted">No tags in this category yet.</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {tags.map((tag) => (
            <TagRow key={tag.id} tag={tag} onDialog={onDialog} />
          ))}
        </ul>
      )}
      <div className="border-t border-line px-4 py-3">
        <AddForm
          label={`New tag in ${group.name}`}
          placeholder="New tag"
          button="Add tag"
          pending={create.isPending}
          error={create.isError ? messageOf(create.error) : null}
          onSubmit={(name, done) => create.mutate({ groupId: group.id, name }, { onSuccess: done })}
        />
      </div>
    </Card>
  )
}

function Catalog({ catalog, onDialog }: { catalog: TagCatalog; onDialog: (dialog: Dialog) => void }) {
  return (
    <div className="flex flex-col gap-4">
      {catalog.groups.map((group) => (
        <GroupCard
          key={group.id}
          group={group}
          tags={catalog.tags.filter((t) => t.group_id === group.id)}
          onDialog={onDialog}
        />
      ))}
    </div>
  )
}

/** Tags: the categories and tags a person uses on trades, with how many trades carry each, to rename, recolour, move, merge or delete. */
export function TagsPage() {
  const catalog = useTagCatalog()
  const createGroup = useCreateGroup()
  const [dialog, setDialog] = useState<Dialog>(null)
  const close = () => setDialog(null)
  const data = catalog.data

  return (
    <>
      <SectionHeader title="Tags">Group, rename, recolour and merge the tags you use on trades.</SectionHeader>
      <AddForm
        label="New category"
        placeholder="New category, like Setup"
        button="Add category"
        pending={createGroup.isPending}
        error={createGroup.isError ? messageOf(createGroup.error) : null}
        onSubmit={(name, done) => createGroup.mutate(name, { onSuccess: done })}
      />
      {catalog.isPending ? <Spinner label="Loading your tags" /> : null}
      {catalog.isError ? (
        <p role="alert" className="m-0">
          Your tags could not be loaded.
        </p>
      ) : null}
      {data ? <Catalog catalog={data} onDialog={setDialog} /> : null}
      {data && dialog?.kind === 'rename' ? <RenameGroupDialog group={dialog.group} onClose={close} /> : null}
      {data && dialog?.kind === 'delete-group' ? (
        <DeleteGroupDialog
          group={dialog.group}
          tags={data.tags.filter((t) => t.group_id === dialog.group.id)}
          onClose={close}
        />
      ) : null}
      {data && dialog?.kind === 'edit' ? <EditTagDialog tag={dialog.tag} catalog={data} onClose={close} /> : null}
      {data && dialog?.kind === 'merge' ? <MergeTagDialog tag={dialog.tag} catalog={data} onClose={close} /> : null}
      {data && dialog?.kind === 'delete-tag' ? <DeleteTagDialog tag={dialog.tag} onClose={close} /> : null}
    </>
  )
}
