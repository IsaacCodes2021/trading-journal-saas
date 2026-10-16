import { useState, type FormEvent, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import {
  deletionDay,
  EXAMPLE_INVITE,
  EXAMPLE_PROMO,
  SIGN_IN_ERRORS,
  go,
  looksLikeInviteCode,
  looksLikePromoCode,
  normalizeInviteCode,
  signInErrorOf,
  signInUrl,
  signUpUrl,
} from '../../lib/auth'
import { AuthFrame } from './AuthFrame'

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
  promo: initialPromo = '',
  error: asked,
  deleted,
  next,
}: {
  mode: AuthMode
  /** A code from the link in the email, put in the box for the person. */
  code?: string
  /** A promo code the person typed, sent back with a refusal so they only have to fix it. */
  promo?: string
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
    <AuthFrame>
      <Switch mode={mode} next={next} />

      {deletionDate ? (
        <div role="status" className="mt-6 rounded-lg border border-line-strong bg-bg px-3.5 py-3 text-[13px]">
          <strong>Your account is scheduled for deletion on {deletionDate}.</strong> You have been signed out
          everywhere. If you change your mind, sign in before then and nothing is lost.
        </div>
      ) : null}

      {mode === 'signin' ? (
        <SignIn error={error} next={next} />
      ) : (
        <SignUp error={error} initial={initial} initialPromo={initialPromo} />
      )}
    </AuthFrame>
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
      <Link to="/join" className="font-medium">
        Join the beta
      </Link>
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

function SignUp({
  error,
  initial,
  initialPromo,
}: {
  error: keyof typeof SIGN_IN_ERRORS | null
  initial: string
  initialPromo: string
}) {
  const [code, setCode] = useState(initial)
  const [promo, setPromo] = useState(initialPromo)
  const [problem, setProblem] = useState<string | null>(null)
  const [promoProblem, setPromoProblem] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = normalizeInviteCode(code)
    const badCode = !looksLikeInviteCode(normalized)
    const badPromo = promo.trim() !== '' && !looksLikePromoCode(promo)
    setProblem(badCode ? `That does not look like a sign-up code. It looks like ${EXAMPLE_INVITE}.` : null)
    setPromoProblem(badPromo ? `That does not look like a promo code. It looks like ${EXAMPLE_PROMO}.` : null)
    if (badCode || badPromo) return
    go.to(signUpUrl(normalized, promo))
  }

  // The server refused a code: say so on that box as well, so it is clear which field to look at.
  const refused = error === 'invite_invalid'
  const promoRefused = error === 'promo_invalid'
  const flagged = problem !== null || refused
  const promoFlagged = promoProblem !== null || promoRefused

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
                  <Link to="/join" className="font-medium">
                    join the beta again
                  </Link>{' '}
                  for a new one.
                </>
              ) : (
                'It works once and is tied to the email address we sent it to.'
              )}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="signup-promo" className="text-[13px] font-medium">
            Promo code <span className="font-normal text-muted">(optional)</span>
          </label>
          <input
            id="signup-promo"
            value={promo}
            onChange={(e) => {
              setPromo(e.target.value)
              setPromoProblem(null)
            }}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={40}
            placeholder={EXAMPLE_PROMO}
            aria-describedby={promoProblem ? 'promo-problem' : 'promo-hint'}
            aria-invalid={promoFlagged}
            className={`num h-12 w-full rounded-[10px] border bg-bg px-3.5 font-mono text-base tracking-[0.04em] text-ink ${
              promoFlagged ? 'border-neg' : 'border-line-strong'
            }`}
          />
          {promoProblem ? (
            <div id="promo-problem" role="alert" className="text-xs text-neg">
              {promoProblem}
            </div>
          ) : (
            <div id="promo-hint" className="text-xs text-muted">
              {promoRefused
                ? 'Check the promo code, or empty this box to sign up without it.'
                : 'If you have one. You can also add it later, in Settings.'}
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
