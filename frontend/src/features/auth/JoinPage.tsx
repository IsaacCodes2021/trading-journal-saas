import { useState, type FormEvent } from 'react'
import { Link } from '@tanstack/react-router'
import { SITE } from '../../lib/auth'
import { joinProblems, joinTheBeta, type JoinForm, type JoinProblems } from '../../lib/beta'
import { AuthFrame } from './AuthFrame'

const INPUT = 'h-[46px] w-full rounded-[10px] border bg-bg px-3.5 font-[inherit] text-ink'

function TextField({
  id,
  label,
  value,
  onChange,
  problem,
  hint,
  ...input
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  problem?: string | undefined
  hint?: string
  type?: string
  autoComplete?: string
  placeholder?: string
  maxLength?: number
}) {
  const about = problem ? `${id}-problem` : hint ? `${id}-hint` : undefined
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={problem !== undefined}
        aria-describedby={about}
        className={`${INPUT} ${problem ? 'border-neg' : 'border-line-strong'}`}
        {...input}
      />
      {problem ? (
        <div id={`${id}-problem`} role="alert" className="text-xs text-neg">
          {problem}
        </div>
      ) : hint ? (
        <div id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </div>
      ) : null}
    </div>
  )
}

/**
 * The beta form: first name, address and favourite prop firm. The server emails a sign-up code, and says the same thing
 * whatever became of the address, so this page does too. It is the only public form, and it lives here, on the app's own
 * address, because the server accepts it only from its own pages.
 */
export function JoinPage() {
  const [form, setForm] = useState<JoinForm>({ firstName: '', email: '', propFirm: '', website: '' })
  const [problems, setProblems] = useState<JoinProblems>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState<{ name: string; email: string } | null>(null)

  const set = (key: keyof JoinForm) => (value: string) => {
    setForm((current) => ({ ...current, [key]: value }))
    setProblems((current) => (key === 'website' ? current : { ...current, [key]: undefined }))
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (sending) return
    const found = joinProblems(form)
    setProblems(found)
    if (Object.keys(found).length > 0) return
    setFailure(null)
    setSending(true)
    const result = await joinTheBeta(form)
    setSending(false)
    if (result.ok) setSent({ name: form.firstName.trim(), email: form.email.trim() })
    else setFailure(result.message)
  }

  if (sent) {
    return (
      <AuthFrame>
        <span
          aria-hidden="true"
          className="inline-flex size-11 items-center justify-center rounded-xl border border-line-strong bg-raised"
        >
          <svg
            viewBox="0 0 24 24"
            width="22"
            height="22"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-pos"
          >
            <path d="M4 12.5l5 5L20 6.5" />
          </svg>
        </span>
        <h1 className="mt-[18px] mb-0 text-[26px] leading-tight font-semibold tracking-[-0.02em]">Check your email</h1>
        <p role="status" className="mt-2.5 mb-0 text-[15px] text-muted">
          Thanks, {sent.name}. If <strong className="font-medium text-ink">{sent.email}</strong> can join, your sign-up
          code is on its way. It can take a few minutes. If you do not see it, check your spam folder.
        </p>
        <div className="mt-6 rounded-[10px] border border-line bg-bg px-[18px] py-4 text-sm text-muted">
          The code works for 24 hours. It gives you 30 days of full access, counted from the day you create your
          account.
        </div>
        <Link
          to="/signup"
          className="mt-6 flex h-[50px] items-center justify-center rounded-xl border border-line bg-raised text-base font-medium text-ink no-underline hover:border-line-strong hover:text-ink"
        >
          I have my code · Sign up
        </Link>
        <p className="mt-[18px] mb-0 text-[13px] text-muted">
          <a href={SITE} className="font-medium">
            Back to roundturn.app
          </a>
        </p>
      </AuthFrame>
    )
  }

  return (
    <AuthFrame>
      <h1 className="m-0 text-[26px] leading-tight font-semibold tracking-[-0.02em]">Join the beta</h1>
      <p className="mt-2 mb-0 text-[15px] text-muted">
        We will email you a sign-up code. It works once, for 24 hours, and is tied to your email address.
      </p>

      <form onSubmit={(event) => void submit(event)} noValidate className="mt-6 flex flex-col gap-[18px]">
        <TextField
          id="join-name"
          label="First name"
          value={form.firstName}
          onChange={set('firstName')}
          problem={problems.firstName}
          autoComplete="given-name"
          placeholder="Alex"
          maxLength={80}
        />
        <TextField
          id="join-email"
          label="Email"
          type="email"
          value={form.email}
          onChange={set('email')}
          problem={problems.email}
          autoComplete="email"
          placeholder="you@example.com"
          maxLength={254}
        />
        <TextField
          id="join-firm"
          label="Favourite prop firm"
          value={form.propFirm}
          onChange={set('propFirm')}
          problem={problems.propFirm}
          hint={'Type "none" if you do not use one yet.'}
          placeholder="The firm you trade with most"
          maxLength={80}
        />
        {/* The field only a script fills in. It is out of sight and out of the tab order, and nothing is made for it. */}
        <div aria-hidden="true" className="absolute -left-[10000px] size-px overflow-hidden">
          <label htmlFor="join-website">Leave this field empty</label>
          <input
            id="join-website"
            name="website"
            type="text"
            tabIndex={-1}
            autoComplete="off"
            value={form.website}
            onChange={(e) => set('website')(e.target.value)}
          />
        </div>
        {failure ? (
          <div role="alert" className="rounded-[10px] border border-line-strong bg-bg px-4 py-3 text-sm text-neg">
            {failure}
          </div>
        ) : null}
        <button
          type="submit"
          disabled={sending}
          className="h-[50px] cursor-pointer rounded-xl border border-accent bg-accent font-[inherit] text-base font-semibold text-accent-on hover:bg-accent-hover disabled:cursor-default disabled:opacity-60"
        >
          {sending ? 'Sending…' : 'Join the beta'}
        </button>
      </form>

      <p className="mt-[18px] mb-0 text-[13px] text-muted">
        Have a sign-up code?{' '}
        <Link to="/signup" className="font-medium">
          Sign up here
        </Link>
      </p>
      <p className="mt-3 mb-0 text-xs text-muted">
        We use your answers only to send you the code and to run the beta.{' '}
        <a href={`${SITE}/privacy/`}>How your details are handled</a>.
      </p>
    </AuthFrame>
  )
}
