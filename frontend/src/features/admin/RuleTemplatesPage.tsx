import { useEffect, useState, type FormEvent } from 'react'
import { RuleGroups } from '../../components/RuleFields'
import { CONTROL } from '../../components/ui/Field'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { utcDay } from '../../lib/admin'
import {
  changedBy,
  TEMPLATES_PAGE,
  useAdminTemplate,
  useAdminTemplates,
  useEditTemplate,
  useImportTemplates,
  useTemplateFields,
  type AdminTemplate,
  type TemplateEdit,
  type TemplateImport,
  type TemplateQuery,
} from '../../lib/adminRules'
import { PHASE_LABELS, type RuleField, type RuleFlag } from '../../lib/data/model'
import { pluralize } from '../../lib/format'
import { flagKind, flagReason, readRuleForm, ruleText } from '../../lib/rules'
import { useSession } from '../../lib/session'
import { messageOf } from '../tags/messages'
import { SearchBox } from './SearchBox'

const TH = 'border-b border-line px-3.5 py-2.5 font-medium'
const TD = 'border-b border-line-soft px-3.5 py-2.5'
const KINDS: RuleFlag['kind'][] = ['unknown', 'conflict', 'third-party only']

/** The server's field errors by field name (`values.max_loss_usd` is `max_loss_usd`), and a flag's by its field. */
function fieldErrorsOf(error: unknown, flags: readonly RuleFlag[]): Record<string, string> {
  const fields = (error as { fields?: Record<string, string> } | null)?.fields ?? {}
  const out: Record<string, string> = {}
  for (const [key, message] of Object.entries(fields)) {
    if (key.startsWith('values.')) out[key.slice('values.'.length)] = message
    const flag = /^flags\.(\d+)\./.exec(key)
    const name = flag ? flags[Number(flag[1])]?.field : undefined
    if (name) out[name] ??= message
  }
  return out
}

const enteredOf = (values: AdminTemplate['values']) =>
  Object.fromEntries(Object.entries(values).map(([k, v]) => [k, ruleText(v)]))

const nameOf = (values: AdminTemplate['values']) => {
  const phase = values['phase'] as keyof typeof PHASE_LABELS | null
  return [values['firm'], values['program'], values['account_size'], phase ? (PHASE_LABELS[phase] ?? phase) : null]
    .filter((part) => part !== null && part !== '')
    .join(' · ')
}

/** The fields the research could not confirm, each with why, and a way to clear one or add another. */
function FlagsEditor({
  fields,
  flags,
  onChange,
}: {
  fields: readonly RuleField[]
  flags: RuleFlag[]
  onChange: (next: RuleFlag[]) => void
}) {
  const labels = Object.fromEntries(fields.map((f) => [f.name, f.label]))
  const open = fields.filter((f) => !flags.some((flag) => flag.field === f.name))
  const [field, setField] = useState('')
  const [kind, setKind] = useState<RuleFlag['kind']>('unknown')
  const [detail, setDetail] = useState('')

  const add = () => {
    if (!field) return
    const reason = flagReason(kind, detail)
    onChange([...flags, { field, reason, kind: flagKind(reason) }])
    setField('')
    setDetail('')
  }

  return (
    <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
      <legend className="mb-2 p-0 text-sm font-semibold">Fields to check</legend>
      <p className="m-0 text-xs text-muted">
        A flagged field shows red, with its reason, to everyone who picks this template. Clear a flag once you have
        confirmed the value on the firm&rsquo;s own site.
      </p>
      {flags.length === 0 ? (
        <p className="m-0 text-[13px] text-muted">No field is flagged.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Flagged fields">
          {flags.map((flag) => (
            <li
              key={flag.field}
              className="flex flex-wrap items-start gap-x-3 gap-y-1 rounded-lg border border-neg bg-flag-bg px-3 py-2"
            >
              <span className="min-w-0 grow text-[13px]">
                <span className="font-medium">{labels[flag.field] ?? flag.field}</span>
                <span className="text-muted"> · {flag.reason}</span>
              </span>
              <Button
                variant="ghost"
                className="h-7! px-2! text-xs"
                aria-label={`Clear the flag on ${labels[flag.field] ?? flag.field}`}
                onClick={() => onChange(flags.filter((f) => f.field !== flag.field))}
              >
                Clear
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-end gap-2" role="group" aria-label="Add a flag">
        <select
          aria-label="Field to flag"
          value={field}
          onChange={(e) => setField(e.target.value)}
          className={`${CONTROL} h-9! w-auto! max-w-full`}
        >
          <option value="">Flag a field…</option>
          {open.map((f) => (
            <option key={f.name} value={f.name}>
              {f.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Why"
          value={kind}
          onChange={(e) => setKind(e.target.value as RuleFlag['kind'])}
          className={`${CONTROL} h-9! w-auto!`}
        >
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <input
          aria-label="What the sources say"
          placeholder="What the sources say"
          value={detail}
          maxLength={900}
          onChange={(e) => setDetail(e.target.value)}
          className={`${CONTROL} h-9! min-w-[200px] grow`}
        />
        <Button onClick={add} disabled={!field}>
          Add flag
        </Button>
      </div>
    </fieldset>
  )
}

/** One template's form: every value and flag, saved as a whole. Only the typing and the five names are checked. */
function TemplateForm({
  template,
  fields,
  groups,
  saved,
  saving,
  onSave,
  onEdited,
  onDirty,
}: {
  template: AdminTemplate
  fields: RuleField[]
  groups: string[]
  saved: boolean
  saving: boolean
  onSave: (edit: TemplateEdit, onError: (error: unknown) => void) => void
  onEdited: () => void
  onDirty: (dirty: boolean) => void
}) {
  const [entered, setEntered] = useState(() => enteredOf(template.values))
  const [flags, setFlags] = useState<RuleFlag[]>(template.flags)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<string | null>(null)

  const dirty =
    JSON.stringify(entered) !== JSON.stringify(enteredOf(template.values)) ||
    JSON.stringify(flags) !== JSON.stringify(template.flags)
  useEffect(() => onDirty(dirty), [dirty, onDirty])
  useEffect(() => () => onDirty(false), [onDirty])

  const set = (name: string, value: string) => {
    onEdited()
    setEntered((e) => ({ ...e, [name]: value }))
  }
  const setFlagList = (next: RuleFlag[]) => {
    onEdited()
    setFlags(next)
  }
  const discard = () => {
    setEntered(enteredOf(template.values))
    setFlags(template.flags)
    setErrors({})
    setProblem(null)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const read = readRuleForm(fields, entered, { whole: false })
    if (Object.keys(read.errors).length > 0) {
      setErrors(read.errors)
      setProblem('Some fields need fixing: they are marked in red.')
      return
    }
    setErrors({})
    setProblem(null)
    onSave({ values: read.values, flags: flags.map(({ field, reason }) => ({ field, reason })) }, (error) => {
      setErrors(fieldErrorsOf(error, flags))
      setProblem(messageOf(error))
    })
  }

  return (
    <form onSubmit={submit} aria-label="Template" className="flex flex-col gap-4">
      <RuleGroups
        fields={fields}
        groups={groups}
        entered={entered}
        flags={flags}
        notes={template.source_notes}
        errors={errors}
        onChange={set}
      />
      <FlagsEditor fields={fields} flags={flags} onChange={setFlagList} />
      {problem ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {problem}
        </p>
      ) : null}
      <div className="sticky bottom-0 -mx-5 flex flex-wrap items-center gap-3 border-t border-line bg-panel px-5 py-3">
        <Button type="submit" variant="primary" disabled={saving || !dirty}>
          {saving ? 'Saving…' : 'Save template'}
        </Button>
        {dirty ? (
          <Button variant="ghost" onClick={discard} disabled={saving}>
            Discard changes
          </Button>
        ) : null}
        <span role="status" className="text-[13px]">
          {dirty ? (
            <span className="text-warn">Unsaved changes</span>
          ) : saved ? (
            <span className="text-pos">Saved</span>
          ) : null}
        </span>
      </div>
    </form>
  )
}

/** The open template: what names it, when it last changed, and its form. */
function TemplateEditor({ id, onDirty }: { id: number; onDirty: (dirty: boolean) => void }) {
  const template = useAdminTemplate(id)
  const fields = useTemplateFields()
  const session = useSession()
  // The save lives here, not in the form: a save remounts the form, and a call's own callbacks die with its component.
  const save = useEditTemplate(id)
  const [saved, setSaved] = useState(false)

  if (template.isPending || fields.isPending) return <Spinner label="Loading the template" />
  if (template.isError || fields.isError) {
    return (
      <p role="alert" className="m-0 text-neg">
        {messageOf(template.error ?? fields.error)}
      </p>
    )
  }
  const t = template.data
  const self = session.data?.user.id ?? ''
  const flagged = new Set([...t.flags.map((f) => f.field), ...Object.keys(t.source_notes)]).size
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="m-0 text-lg font-semibold">{nameOf(t.values)}</h2>
        <p className="m-0 text-[13px] text-muted">
          Version {ruleText(t.values['rule_version'])} · last changed {utcDay(t.updated_at)} by{' '}
          {changedBy(t.updated_by, self)}
        </p>
      </div>
      {flagged > 0 ? (
        <p className="m-0 rounded-lg border border-neg bg-flag-bg px-3 py-2 text-[13px]" role="note">
          {pluralize(flagged, 'field')} in red could not be confirmed by the research. People see the same red when they
          pick this template.
        </p>
      ) : null}
      {/* A save gives the template a new time: the form starts again from what was stored. */}
      <TemplateForm
        key={t.updated_at}
        template={t}
        fields={fields.data.fields}
        groups={fields.data.groups}
        saved={saved}
        saving={save.isPending}
        onSave={(edit, onError) => save.mutate(edit, { onSuccess: () => setSaved(true), onError })}
        onEdited={() => setSaved(false)}
        onDirty={onDirty}
      />
    </div>
  )
}

function ImportSummary({ result }: { result: TemplateImport }) {
  const { inserted, updated, unchanged } = result
  const counts = `${inserted} new, ${updated} changed, ${unchanged} unchanged`
  return (
    <div className="flex flex-col gap-2 text-[13px]" role="status">
      <p className="m-0">
        {result.applied
          ? `Imported: ${counts}.`
          : inserted + updated === 0
            ? `Nothing would change (${unchanged} unchanged).`
            : `The file would add or change rule sets: ${counts}. Nothing is kept until you import it.`}
      </p>
      {result.changed.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="m-0 font-medium">
            {result.applied ? 'Replaced by the file’s values:' : 'These would be replaced by the file’s values:'}
          </p>
          <ul className="m-0 max-h-[160px] overflow-auto pl-5" aria-label="Changed templates">
            {result.changed.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {result.problems.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="m-0 font-medium text-neg">
            {pluralize(result.problems.length, 'row')} could not be read and {result.applied ? 'were' : 'will be'}{' '}
            skipped:
          </p>
          <ul className="m-0 max-h-[160px] overflow-auto pl-5 text-neg" aria-label="Rows that cannot be read">
            {result.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/** Load the research CSV again: checked first (nothing kept), then imported. */
function ImportCard() {
  const [file, setFile] = useState<File | null>(null)
  const [checked, setChecked] = useState<TemplateImport | null>(null)
  const [done, setDone] = useState<TemplateImport | null>(null)
  const run = useImportTemplates()

  const go = (dryRun: boolean) => {
    if (!file) return
    run.mutate(
      { file, dryRun },
      {
        onSuccess: (result) => {
          if (result.applied) {
            setDone(result)
            setChecked(null)
          } else {
            setChecked(result)
            setDone(null)
          }
        },
      },
    )
  }
  const worth = checked !== null && checked.inserted + checked.updated > 0

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Import the research CSV">
      <h2 className="m-0 text-base font-semibold">Import the research CSV</h2>
      <p className="m-0 text-[13px] text-muted">
        A row the file has replaces that template&rsquo;s values and flags, an edit made here included; a template the
        file does not have is left alone. Check the file first to see what would change.
      </p>
      <input
        type="file"
        accept=".csv,text/csv"
        aria-label="Research CSV file"
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null)
          setChecked(null)
          setDone(null)
          run.reset()
        }}
        className="text-[13px]"
      />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => go(true)} disabled={!file || run.isPending}>
          {run.isPending && run.variables?.dryRun ? 'Checking…' : 'Check the file'}
        </Button>
        {worth ? (
          <Button variant="primary" onClick={() => go(false)} disabled={run.isPending}>
            {run.isPending && !run.variables?.dryRun ? 'Importing…' : 'Import'}
          </Button>
        ) : null}
      </div>
      {run.isError ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {messageOf(run.error)}
        </p>
      ) : null}
      {checked ? <ImportSummary result={checked} /> : null}
      {done ? <ImportSummary result={done} /> : null}
    </Card>
  )
}

/** The firms' rule templates, with one open beside the list to check or edit, and the CSV import. State is in the
 * address, so a view can be shared. */
export function RuleTemplatesPage({
  query,
  selected,
  onQuery,
  onSelect,
}: {
  query: TemplateQuery
  selected: number | null
  onQuery: (next: TemplateQuery) => void
  onSelect: (id: number | null) => void
}) {
  const templates = useAdminTemplates(query)
  const [dirty, setDirty] = useState(false)
  const [held, setHeld] = useState(false)
  const total = templates.data?.total ?? 0
  const first = total === 0 ? 0 : (query.page - 1) * TEMPLATES_PAGE + 1
  const last = Math.min(query.page * TEMPLATES_PAGE, total)

  const choose = (id: number | null) => {
    if (dirty && id !== selected) {
      setHeld(true) // an open form with changes is not thrown away by a click on another row
      return
    }
    setHeld(false)
    onSelect(id)
  }

  return (
    <>
      <div role="note" className="rounded-lg border border-line-strong bg-chip-selected-bg px-3.5 py-2.5 text-[13px]">
        The firms&rsquo; published rules. People pick one for an account and check every value before it applies. An
        edit here changes what is offered from now on; the rule sets people already verified are their own copies and do
        not change. Every edit and import is recorded in the audit log.
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <Card className="min-w-0 flex-[2_1_440px] p-0" role="region" aria-label="Rule templates">
          <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-4 py-3.5">
            <SearchBox
              value={query.q}
              onSearch={(q) => onQuery({ q, page: 1 })}
              label="Search templates by firm, program, size, phase or version"
              placeholder="Apex 50K funded"
            />
            <span className="grow" />
            <span className="text-[13px] text-muted" aria-live="polite">
              {templates.data ? pluralize(total, 'template') : ''}
            </span>
          </div>
          {held && dirty ? (
            <p role="alert" className="m-0 border-b border-line px-4 py-2.5 text-[13px] text-warn">
              The open template has unsaved changes. Save them or discard them first.
            </p>
          ) : null}
          {templates.isPending ? <Spinner label="Loading the templates" /> : null}
          {templates.isError ? (
            <p role="alert" className="m-0 px-4 py-4 text-neg">
              {messageOf(templates.error)}
            </p>
          ) : null}
          {templates.data ? (
            templates.data.templates.length === 0 ? (
              <p className="m-0 px-4 py-4 text-muted">
                {query.q ? 'No template matches. Try fewer words.' : 'No templates yet. Import the research CSV.'}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-[13px]">
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th className={TH}>Firm and program</th>
                      <th className={TH}>Size</th>
                      <th className={TH}>Phase</th>
                      <th className={TH}>Version</th>
                      <th className={`${TH} text-right`}>To check</th>
                    </tr>
                  </thead>
                  <tbody>
                    {templates.data.templates.map((t) => (
                      <tr
                        key={t.id}
                        aria-selected={t.id === selected}
                        className={t.id === selected ? 'bg-chip-selected-bg' : undefined}
                      >
                        <td className={TD}>
                          <button
                            type="button"
                            onClick={() => choose(t.id === selected ? null : t.id)}
                            className="cursor-pointer border-0 bg-transparent p-0 text-left font-[inherit] text-ink underline-offset-2 hover:underline"
                          >
                            {t.firm} · {t.program}
                          </button>
                        </td>
                        <td className={`${TD} whitespace-nowrap`}>{t.account_size}</td>
                        <td className={TD}>{PHASE_LABELS[t.phase]}</td>
                        <td className={TD}>
                          {t.rule_version}
                          <span className="block text-xs text-muted">
                            {t.effective_from
                              ? `from ${utcDay(`${t.effective_from}T00:00:00Z`)}`
                              : (t.effective_from_note ?? 'start not known')}
                          </span>
                        </td>
                        <td className={`num ${TD} text-right ${t.flagged ? 'text-neg' : 'text-muted'}`}>{t.flagged}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : null}
          {templates.data && total > TEMPLATES_PAGE ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-[13px]">
              <span className="text-muted">
                {first}–{last} of {total.toLocaleString('en-US')}
              </span>
              <span className="flex gap-2">
                <Button onClick={() => onQuery({ ...query, page: query.page - 1 })} disabled={query.page <= 1}>
                  Previous
                </Button>
                <Button onClick={() => onQuery({ ...query, page: query.page + 1 })} disabled={last >= total}>
                  Next
                </Button>
              </span>
            </div>
          ) : null}
        </Card>
        <aside className="flex min-w-0 flex-[3_1_520px] flex-col gap-4" aria-label="Selected template">
          {selected ? (
            <Card className="px-5 pt-4" role="region" aria-label="Template editor">
              <TemplateEditor key={selected} id={selected} onDirty={setDirty} />
            </Card>
          ) : (
            <Card className="px-5 py-4 text-muted">Choose a template to check or edit its values and flags.</Card>
          )}
          <ImportCard />
        </aside>
      </div>
    </>
  )
}
