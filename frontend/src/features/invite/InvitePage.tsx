import { useState, type FormEvent } from 'react'
import { Button } from '../../components/ui/primitives'
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

/**
 * The page before sign-in, while the beta is by invitation: enter the code from the invite email to create an account,
 * or sign in. It needs no session. The code is only checked for its shape here; the server decides whether it is real
 * and unused, and says so, as a short code in `error`, when the person comes back from the identity provider.
 */
export function InvitePage({
  code: initial = '',
  error: asked,
  deleted,
}: {
  code?: string
  error?: string
  /** When the account just asked to be deleted will be deleted (an ISO time); the page says so. */
  deleted?: string
}) {
  // Checked here and not only in the route: the router keeps address parameters it did not validate, so a value the
  // route dropped can still arrive. Only the server's own codes are ever put on the page.
  const error = signInErrorOf(asked)
  const deletionDate = deletionDay(deleted)
  const [code, setCode] = useState(initial)
  const [problem, setProblem] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = normalizeInviteCode(code)
    if (!looksLikeInviteCode(normalized)) {
      setProblem(`That does not look like an invite code. It looks like ${EXAMPLE_INVITE}.`)
      return
    }
    setProblem(null)
    go.to(signUpUrl(normalized))
  }

  return (
    <div className="flex min-h-screen flex-col items-center bg-bg px-6 pt-14 pb-8 text-ink">
      <div className="mb-7 flex items-center gap-2.5 text-lg font-semibold">
        <span aria-hidden="true" className="inline-block size-[26px] rounded-[7px] bg-accent" />
        Roundturn
      </div>

      <main className="flex w-full max-w-[440px] flex-col gap-[18px] rounded-xl border border-line bg-panel p-8">
        <div>
          <span className="inline-flex h-[22px] items-center rounded-full bg-chip-selected-bg px-2.5 text-xs font-medium text-accent">
            Private beta
          </span>
          <h1 className="mt-3 mb-1.5 text-2xl font-semibold">Enter your invite code</h1>
          <p className="m-0 text-muted">
            We&rsquo;re letting in a small group of prop-firm futures traders first. Paste the code from your invite
            email to create your account.
          </p>
        </div>

        {deletionDate ? (
          <div role="status" className="rounded-lg border border-line-strong bg-bg px-3.5 py-3 text-[13px]">
            <strong>Your account is scheduled for deletion on {deletionDate}.</strong> You have been signed out
            everywhere. If you change your mind, sign in before then and nothing is lost.
          </div>
        ) : null}

        {error ? (
          <div
            role="alert"
            data-error={error}
            className="rounded-lg border border-line-strong bg-bg px-3.5 py-3 text-[13px] text-neg"
          >
            {SIGN_IN_ERRORS[error]}
          </div>
        ) : null}

        <form onSubmit={submit} className="flex flex-col gap-[18px]">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="invite-code" className="text-[13px] font-medium">
              Invite code
            </label>
            <input
              id="invite-code"
              value={code}
              onChange={(e) => {
                setCode(e.target.value)
                setProblem(null)
              }}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              placeholder={EXAMPLE_INVITE}
              aria-describedby={problem ? 'invite-problem' : 'invite-hint'}
              aria-invalid={problem !== null}
              className="num h-11 w-full rounded-lg border border-line-strong bg-bg px-3.5 font-[inherit] text-ink"
            />
            {problem ? (
              <div id="invite-problem" role="alert" className="text-xs text-neg">
                {problem}
              </div>
            ) : (
              <div id="invite-hint" className="text-xs text-muted">
                Codes are single-use and tied to one signup.
              </div>
            )}
          </div>
          <Button type="submit" variant="primary" className="h-11 w-full">
            Continue to sign up
          </Button>
        </form>

        <div className="flex items-center gap-3 text-muted" aria-hidden="true">
          <span className="h-px grow bg-line" />
          <span className="text-xs">or</span>
          <span className="h-px grow bg-line" />
        </div>

        <a
          href={signInUrl()}
          className="flex h-11 w-full items-center justify-center rounded-lg border border-line bg-raised text-ink no-underline hover:border-line-strong hover:text-ink"
        >
          I already have an account &middot; Sign in
        </a>

        <p className="m-0 text-center text-[13px] text-muted">
          No code? <a href={`${SITE}/#join`}>Join the waitlist</a>
        </p>
      </main>

      <p className="mt-6 mb-0 max-w-[440px] text-center text-xs text-muted">
        By continuing you agree to the <a href={`${SITE}/terms/`}>Terms</a> and{' '}
        <a href={`${SITE}/privacy/`}>Privacy Policy</a>. Roundturn analyzes data you upload and is not investment
        advice.
      </p>
    </div>
  )
}
