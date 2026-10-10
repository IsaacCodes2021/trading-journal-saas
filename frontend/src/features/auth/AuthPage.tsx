import { useState, type FormEvent, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import {
  deletionDay,
  EXAMPLE_INVITE,
  SIGN_IN_ERRORS,
  SITE,
  go,
  looksLikeInviteCode,
  normalizeInviteCode,
  signInErrorOf,
  signInUrl,
  signUpUrl,
} from '../../lib/auth'

export type AuthMode = 'signin' | 'signup'

/**
 * The front door: sign in, or sign up with a code. It needs no session. The password and the one-time codes are asked
 * for on the identity provider's page (it is restyled to match), so this page only decides where to send the person.
 * The sign-up code is checked here for its shape only; the server decides whether it is real and unused, and says so,
 * as a short code in `error`, when the person comes back.
 */
export function AuthPage({
  mode,
  code: initial = '',
  error: asked,
  deleted,
  next,
}: {
  mode: AuthMode
  /** A code from the link in the email, put in the box for the person. */
  code?: string
  /** What the server said when a sign-in did not complete (one of its own short codes). */
  error?: string
  /** When the account just asked to be deleted will be deleted (an ISO time); the page says so. */
  deleted?: string
  /** A path on this site to come back to after signing in. */
  next?: string
}) {
  // Checked here and not only in the route: the router keeps address parameters it did not validate, so a value the
  // route dropped can still arrive. Only the server's own codes are ever put on the page.
  const error = signInErrorOf(asked)
  const deletionDate = deletionDay(deleted)

  return (
    <div className="flex min-h-screen flex-col items-center bg-bg px-6 pt-14 pb-8 text-ink">
      <div className="flex items-center gap-2.5 text-[19px] font-semibold">
        <span aria-hidden="true" className="inline-block size-[26px] rounded-lg bg-accent" />
        Roundturn
      </div>

      <main className="mt-9 flex w-full max-w-[440px] flex-col rounded-2xl border border-line bg-panel p-8">
        <Switch mode={mode} next={next} />

        {deletionDate ? (
          <div role="status" className="mt-6 rounded-lg border border-line-strong bg-bg px-3.5 py-3 text-[13px]">
            <strong>Your account is scheduled for deletion on {deletionDate}.</strong> You have been signed out
            everywhere. If you change your mind, sign in before then and nothing is lost.
          </div>
        ) : null}

        {mode === 'signin' ? <SignIn error={error} next={next} /> : <SignUp error={error} initial={initial} />}
      </main>

      <p className="mt-6 mb-0 max-w-[440px] text-center text-xs text-muted">
        By continuing you agree to the <a href={`${SITE}/terms/`}>Terms</a> and{' '}
        <a href={`${SITE}/privacy/`}>Privacy Policy</a>. Roundturn analyzes data you upload and is not investment
        advice.
      </p>
    </div>
  )
}

/** Sign in | Sign up. Links, not tabs: each is its own address, so Back works and either can be bookmarked. */
function Switch({ mode, next }: { mode: AuthMode; next: string | undefined }) {
  const search = next ? { next } : {}
  const tab = (active: boolean) =>
    `flex h-[42px] items-center justify-center rounded-[9px] border text-[15px] no-underline ${
      active
        ? 'border-line-strong bg-raised font-semibold text-ink hover:text-ink'
        : 'border-transparent font-medium text-muted hover:text-ink'
    }`
  return (
    <nav aria-label="Sign in or sign up" className="grid grid-cols-2 gap-1 rounded-xl border border-line bg-bg p-1">
      <Link
        to="/signin"
        search={search}
        aria-current={mode === 'signin' ? 'page' : undefined}
        className={tab(mode === 'signin')}
      >
        Sign in
      </Link>
      <Link
        to="/signup"
        search={search}
        aria-current={mode === 'signup' ? 'page' : undefined}
        className={tab(mode === 'signup')}
      >
        Sign up
      </Link>
    </nav>
  )
}

function Problem({ error }: { error: keyof typeof SIGN_IN_ERRORS | null }) {
  if (!error) return null
  return (
    <div
      role="alert"
      data-error={error}
      className="mt-4 rounded-[10px] border border-line-strong bg-bg px-4 py-3.5 text-sm text-neg"
    >
      {SIGN_IN_ERRORS[error]}
    </div>
  )
}

function Heading({ children, chip }: { children: ReactNode; chip?: string }) {
  return (
    <>
      {chip ? (
        <span className="mt-7 inline-flex h-6 w-fit items-center rounded-full border border-line-strong bg-raised px-2.5 font-mono text-[11px] tracking-[0.06em] text-accent">
          {chip}
        </span>
      ) : null}
      <h1 className={`${chip ? 'mt-3' : 'mt-7'} mb-0 text-[28px] leading-tight font-semibold tracking-[-0.02em]`}>
        {children}
      </h1>
    </>
  )
}

function JoinTheBeta({ children = 'No code yet?' }: { children?: string }) {
  return (
    <p className="m-0 text-center text-sm text-muted">
      {children}{' '}
      <a href={`${SITE}/#join`} className="font-medium">
        Join the beta
      </a>
    </p>
  )
}

function SignIn({ error, next }: { error: keyof typeof SIGN_IN_ERRORS | null; next: string | undefined }) {
  return (
    <>
      <Heading>Welcome back</Heading>
      <Problem error={error} />
      <p className="mt-2 mb-0 text-[15px] text-muted">Sign in with the email you used to create your account.</p>

      <a
        href={signInUrl(next ?? '/')}
        className="mt-6 flex h-[50px] items-center justify-center rounded-xl border border-accent bg-accent text-base font-semibold text-accent-on no-underline hover:bg-accent-hover hover:text-accent-on"
      >
        Continue to sign in
      </a>
      <p className="mt-3 mb-0 text-[13px] text-muted">
        You will confirm your email and password on our sign-in service, then come straight back here.
      </p>

      <div className="my-7 flex items-center gap-3 text-muted" aria-hidden="true">
        <span className="h-px grow bg-line" />
        <span className="text-xs">or</span>
        <span className="h-px grow bg-line" />
      </div>

      <Link
        to="/signup"
        search={next ? { next } : {}}
        className="flex h-[50px] items-center justify-center rounded-xl border border-line bg-raised text-[15px] font-medium text-ink no-underline hover:border-line-strong hover:text-ink"
      >
        Have a sign-up code? Sign up here
      </Link>
      <div className="mt-5">
        <JoinTheBeta />
      </div>
    </>
  )
}

function SignUp({ error, initial }: { error: keyof typeof SIGN_IN_ERRORS | null; initial: string }) {
  const [code, setCode] = useState(initial)
  const [problem, setProblem] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = normalizeInviteCode(code)
    if (!looksLikeInviteCode(normalized)) {
      setProblem(`That does not look like a sign-up code. It looks like ${EXAMPLE_INVITE}.`)
      return
    }
    setProblem(null)
    go.to(signUpUrl(normalized))
  }

  // The server refused the code: say so on the box itself as well, so it is clear which field to look at.
  const refused = error === 'invite_invalid'
  const flagged = problem !== null || refused

  return (
    <>
      <Heading chip={error ? undefined : 'INVITE-ONLY BETA'}>Create your account</Heading>
      <Problem error={error} />
      {error ? null : (
        <p className="mt-2 mb-0 text-[15px] text-muted">
          Enter the sign-up code from your email. You will then choose a password on our sign-in service.
        </p>
      )}

      <form onSubmit={submit} className="mt-6 flex flex-col gap-[18px]">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="signup-code" className="text-[13px] font-medium">
            Sign-up code
          </label>
          <input
            id="signup-code"
            value={code}
            onChange={(e) => {
              setCode(e.target.value)
              setProblem(null)
            }}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder={EXAMPLE_INVITE}
            aria-describedby={problem ? 'signup-problem' : 'signup-hint'}
            aria-invalid={flagged}
            className={`num h-12 w-full rounded-[10px] border bg-bg px-3.5 font-mono text-base tracking-[0.04em] text-ink ${
              flagged ? 'border-neg' : 'border-line-strong'
            }`}
          />
          {problem ? (
            <div id="signup-problem" role="alert" className="text-xs text-neg">
              {problem}
            </div>
          ) : (
            <div id="signup-hint" className="text-xs text-muted">
              {refused ? (
                <>
                  Check the code in your email, or{' '}
                  <a href={`${SITE}/#join`} className="font-medium">
                    join the beta again
                  </a>{' '}
                  for a new one.
                </>
              ) : (
                'It works once and is tied to the email address we sent it to.'
              )}
            </div>
          )}
        </div>
        <button
          type="submit"
          className="h-[50px] cursor-pointer rounded-xl border border-accent bg-accent font-[inherit] text-base font-semibold text-accent-on hover:bg-accent-hover"
        >
          Continue to create account
        </button>
      </form>

      {refused ? null : (
        <div className="mt-5 rounded-[10px] border border-line bg-bg px-4 py-3.5 text-[13px] text-muted">
          Beta codes give 30 days of full access, counted from the day you create your account.
        </div>
      )}

      <div className="mt-6">
        {refused ? (
          <p className="m-0 text-center text-sm text-muted">
            Still stuck? Write to{' '}
            <a href="mailto:hello@roundturn.app" className="font-medium">
              hello@roundturn.app
            </a>
          </p>
        ) : (
          <JoinTheBeta />
        )}
      </div>
    </>
  )
}
