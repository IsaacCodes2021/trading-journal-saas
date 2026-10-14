import { useState, type FormEvent } from 'react'
import { CONTROL } from '../../components/ui/Field'
import { cx } from '../../components/ui/cx'
import { Button, Spinner } from '../../components/ui/primitives'
import {
  PHASE_LABELS,
  type AccountDetail,
  type RuleField,
  type RuleFlag,
  type RuleSet,
  type RuleTemplate,
  type RuleTemplateSummary,
} from '../../lib/data/model'
import {
  useFetchRuleTemplate,
  useRuleFields,
  useRuleSet,
  useRuleTemplates,
  useSaveRuleSet,
} from '../../lib/data/queries'
import { formatDay, pluralize } from '../../lib/format'
import { readRuleForm, ruleText, warningOf } from '../../lib/rules'

/** The rules as the form holds them: the text of every field, and what came with the template it started from. */
interface Draft {
  entered: Record<string, string>
  flags: RuleFlag[]
  notes: Record<string, string>
  templateId: number | null
  origin: string
}

const PICKER_LIMIT = 30
const TEXTAREA = 'w-full rounded-lg border border-line-strong bg-bg px-3 py-2 font-[inherit] text-ink'

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

/** The server's (or the sample data's) field errors, by field name: `values.max_loss_usd` is `max_loss_usd`. */
function fieldErrorsOf(error: unknown): Record<string, string> {
  const fields = (error as { fields?: Record<string, string> } | null)?.fields ?? {}
  return Object.fromEntries(
    Object.entries(fields)
      .filter(([key]) => key.startsWith('values.'))
      .map(([key, message]) => [key.slice('values.'.length), message]),
  )
}

const entered = (values: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(values).map(([k, v]) => [k, ruleText(v as string | number | null)]))

function fromRuleSet(rules: RuleSet): Draft {
  return {
    entered: entered(rules.values),
    flags: rules.flags,
    notes: rules.source_notes,
    templateId: rules.source_template_id,
    origin: rules.source_template_id === null ? 'typed in by hand' : 'copied from a firm template',
  }
}

function fromTemplate(template: RuleTemplate, summary: RuleTemplateSummary): Draft {
  return {
    entered: entered(template.values),
    flags: template.flags,
    notes: template.source_notes,
    templateId: template.id,
    origin: `${summary.firm} · ${summary.program} · ${summary.account_size} · ${PHASE_LABELS[summary.phase]} (${summary.rule_version})`,
  }
}

/** "EOD Trailing (Apex 4.0)" as words: eod, trailing, apex, 4.0. */
const wordsOf = (text: string) =>
  text
    .toLowerCase()
    .split(/[^a-z0-9.%+/-]+/)
    .filter(Boolean)

function startLabel(t: RuleTemplateSummary): string {
  if (t.effective_from) return `from ${formatDay(t.effective_from)}`
  return t.effective_from_note ? `start: ${t.effective_from_note}` : 'start not known'
}

/** Search the firms' rule sets: type the firm, then the program, the size and the phase. */
function RulePicker({
  purchasedOn,
  suggest,
  disabled,
  onPick,
}: {
  purchasedOn: string
  suggest: string
  disabled: boolean
  onPick: (template: RuleTemplateSummary) => void
}) {
  const templates = useRuleTemplates(purchasedOn)
  const [q, setQ] = useState(suggest)
  const words = wordsOf(q)
  // Each word typed must start a word of the name: "50K" finds 50K accounts, not 150K ones.
  const matches = (templates.data ?? []).filter((t) => {
    const name = wordsOf(`${t.firm} ${t.program} ${t.account_size} ${t.phase} ${t.rule_version}`)
    return words.every((w) => name.some((n) => n.startsWith(w)))
  })
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="rule-search" className="text-[13px] font-medium">
        Find your account&rsquo;s rules
      </label>
      <input
        id="rule-search"
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Firm, program, size, phase: Apex 50K funded"
        className={CONTROL}
      />
      <p className="m-0 text-xs text-muted">
        Only the versions in force on your purchase date ({formatDay(purchasedOn)}) are offered, and the latest is
        marked Suggested. You check every value before it applies.
      </p>
      {templates.isPending ? <Spinner label="Loading the firms' rules" /> : null}
      {templates.isError ? (
        <p role="alert" className="m-0 text-neg">
          The firms&rsquo; rules could not be loaded.
        </p>
      ) : null}
      {templates.data ? (
        <ul className="m-0 flex max-h-[320px] list-none flex-col gap-1 overflow-auto p-0" aria-label="Rule sets">
          {matches.slice(0, PICKER_LIMIT).map((t) => (
            <li key={t.id}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onPick(t)}
                className="flex w-full cursor-pointer flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg border border-line bg-raised px-3 py-2 text-left font-[inherit] text-ink hover:border-line-strong disabled:cursor-default disabled:opacity-50"
              >
                <span className="font-medium">
                  {t.firm} · {t.program} · {t.account_size} · {PHASE_LABELS[t.phase]}
                </span>
                {t.preselect ? (
                  <span className="rounded-full border border-accent px-2 text-xs text-accent">Suggested</span>
                ) : null}
                <span className="w-full text-xs text-muted">
                  {t.rule_version} · {startLabel(t)}
                  {t.flagged ? ` · ${pluralize(t.flagged, 'field')} to check` : ''}
                </span>
              </button>
            </li>
          ))}
          {matches.length === 0 ? <li className="text-muted">No rule set matches. Try fewer words.</li> : null}
          {matches.length > PICKER_LIMIT ? (
            <li className="text-xs text-muted">
              {matches.length - PICKER_LIMIT} more: type more of the name to narrow it down.
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  )
}

/** One rule field. Red when the research could not confirm it (with why, as a tooltip and in words) or when it is wrong. */
function RuleInput({
  field,
  value,
  warning,
  error,
  onChange,
}: {
  field: RuleField
  value: string
  warning: string | null
  error: string | undefined
  onChange: (next: string) => void
}) {
  const id = `rule-${field.name}`
  const red = warning !== null || error !== undefined
  const described = [warning ? `${id}-warning` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined
  const common = {
    id,
    value,
    title: warning ?? undefined,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': described,
  }
  const redClass = red ? 'border-neg bg-flag-bg' : ''
  let control
  if (field.kind === 'choice') {
    control = (
      <select {...common} onChange={(e) => onChange(e.target.value)} className={cx(CONTROL, redClass)}>
        <option value="">—</option>
        {field.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    )
  } else if (field.kind === 'text' && field.group !== 'Rule set') {
    control = (
      <textarea {...common} rows={2} onChange={(e) => onChange(e.target.value)} className={cx(TEXTAREA, redClass)} />
    )
  } else {
    control = (
      <input
        {...common}
        type={field.kind === 'date' ? 'date' : 'text'}
        inputMode={
          field.kind === 'money' || field.kind === 'pct' ? 'decimal' : field.kind === 'int' ? 'numeric' : undefined
        }
        onChange={(e) => onChange(e.target.value)}
        className={cx(CONTROL, redClass, field.kind !== 'text' && field.kind !== 'date' && 'num')}
      />
    )
  }
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">
        {field.label}
        {field.kind === 'money' ? ' ($)' : field.kind === 'pct' ? ' (%)' : ''}
      </label>
      {control}
      {field.hint ? <p className="m-0 text-xs text-muted">{field.hint}</p> : null}
      {warning ? (
        <p id={`${id}-warning`} className="m-0 text-xs text-neg">
          {warning}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} role="alert" className="m-0 text-xs text-neg">
          {error}
        </p>
      ) : null}
    </div>
  )
}

function RulesEditor({
  account,
  fields,
  groups,
  current,
  suggest,
}: {
  account: AccountDetail
  fields: RuleField[]
  groups: string[]
  current: RuleSet | null
  suggest: string
}) {
  const [draft, setDraft] = useState<Draft | null>(current ? fromRuleSet(current) : null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [picking, setPicking] = useState(current === null)
  const [loading, setLoading] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const save = useSaveRuleSet(account.id)
  const fetchTemplate = useFetchRuleTemplate()

  const pick = async (summary: RuleTemplateSummary) => {
    setLoading(true)
    setProblem(null)
    try {
      setDraft(fromTemplate(await fetchTemplate(summary.id), summary))
      setErrors({})
      setSaved(false)
      setPicking(false)
    } catch (error) {
      setProblem(messageOf(error))
    } finally {
      setLoading(false)
    }
  }

  const startBlank = () => {
    setDraft({ entered: { phase: account.phase }, flags: [], notes: {}, templateId: null, origin: 'typed in by hand' })
    setErrors({})
    setSaved(false)
    setPicking(false)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!draft) return
    const read = readRuleForm(fields, draft.entered)
    if (Object.keys(read.errors).length > 0) {
      setErrors(read.errors)
      setProblem('Some rules need fixing: they are marked in red below.')
      return
    }
    setErrors({})
    setProblem(null)
    save.mutate(
      {
        values: read.values,
        flags: draft.flags.map(({ field, reason }) => ({ field, reason })),
        source_notes: draft.notes,
        source_template_id: draft.templateId,
      },
      {
        onSuccess: () => setSaved(true),
        onError: (error) => {
          setErrors(fieldErrorsOf(error))
          setProblem(messageOf(error))
        },
      },
    )
  }

  const set = (name: string, value: string) => {
    setSaved(false)
    setDraft((d) => (d ? { ...d, entered: { ...d.entered, [name]: value } } : d))
  }

  const flagged = draft ? new Set([...draft.flags.map((f) => f.field), ...Object.keys(draft.notes)]).size : 0

  return (
    <div className="flex flex-col gap-4">
      {current ? (
        <p className="m-0 text-[13px]" role="note">
          Verified {formatDay(current.verified_at.slice(0, 10))},{' '}
          {current.source_template_id === null ? 'typed in by hand' : 'copied from a firm template'}. The Accounts tab
          checks your trades against these rules.
        </p>
      ) : (
        <p className="m-0 text-[13px] text-muted">
          No rules yet. Pick your firm&rsquo;s rule set (or start blank), check every value, then verify.
        </p>
      )}

      {picking ? (
        account.purchase_date ? (
          <RulePicker
            purchasedOn={account.purchase_date}
            suggest={suggest}
            disabled={loading}
            onPick={(t) => void pick(t)}
          />
        ) : (
          <p className="m-0 rounded-lg border border-warn-line px-3 py-2.5 text-[13px]" role="note">
            Add the purchase date on the Details tab first: it decides which version of the firm&rsquo;s rules applies.
            You can still type the rules in by hand.
          </p>
        )
      ) : null}
      <div className="flex flex-wrap gap-2">
        {!picking ? <Button onClick={() => setPicking(true)}>Pick another rule set</Button> : null}
        {picking && draft ? <Button onClick={() => setPicking(false)}>Back to the form</Button> : null}
        <Button variant="ghost" onClick={startBlank}>
          Start blank
        </Button>
      </div>

      {draft && !picking ? (
        <form onSubmit={submit} aria-label="Firm rules" className="flex flex-col gap-4 border-t border-line pt-4">
          <p className="m-0 text-[13px]">
            <span className="text-muted">Starting from:</span> {draft.origin}
          </p>
          {flagged > 0 ? (
            <p className="m-0 rounded-lg border border-neg bg-flag-bg px-3 py-2 text-[13px]" role="note">
              {pluralize(flagged, 'field')} in red could not be confirmed by the research: an empty one was left empty
              on purpose, a filled one came from a third party. Check them on the firm&rsquo;s site. Red is a warning;
              only a real error stops you saving.
            </p>
          ) : null}
          {groups.map((group) => {
            const inGroup = fields.filter((f) => f.group === group)
            if (inGroup.length === 0) return null
            return (
              <fieldset key={group} className="m-0 flex flex-col gap-3 border-0 p-0">
                <legend className="mb-2 p-0 text-sm font-semibold">{group}</legend>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {inGroup.map((field) => (
                    <RuleInput
                      key={field.name}
                      field={field}
                      value={draft.entered[field.name] ?? ''}
                      warning={warningOf(field.name, draft.flags, draft.notes)}
                      error={errors[field.name]}
                      onChange={(next) => set(field.name, next)}
                    />
                  ))}
                </div>
              </fieldset>
            )
          })}
          {problem ? (
            <p role="alert" className="m-0 text-[13px] text-neg">
              {problem}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Verify and save rules'}
            </Button>
            {saved && !save.isPending ? (
              <span role="status" className="text-[13px] text-pos">
                Verified and saved
              </span>
            ) : null}
          </div>
        </form>
      ) : null}
      {problem && (!draft || picking) ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {problem}
        </p>
      ) : null}
    </div>
  )
}

/** The firm's rules for this account: picked from the research's templates (or typed in), checked, then verified. */
export function FirmRulesTab({ account, suggest = '' }: { account: AccountDetail; suggest?: string }) {
  const fields = useRuleFields()
  const ruleSet = useRuleSet(account.id)
  if (fields.isPending || ruleSet.isPending) return <Spinner label="Loading the rules" />
  if (fields.isError || ruleSet.isError) {
    return (
      <p role="alert" className="m-0">
        The rules could not be loaded.
      </p>
    )
  }
  return (
    <RulesEditor
      account={account}
      fields={fields.data.fields}
      groups={fields.data.groups}
      current={ruleSet.data}
      suggest={suggest}
    />
  )
}
