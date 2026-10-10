import type { ReactNode } from 'react'
import { SITE } from '../../lib/auth'

/** The page around the way-in cards: the name, one card, and what continuing means. Needs no session. */
export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center bg-bg px-6 pt-14 pb-8 text-ink">
      <div className="flex items-center gap-2.5 text-[19px] font-semibold">
        <span aria-hidden="true" className="inline-block size-[26px] rounded-lg bg-accent" />
        Roundturn
      </div>

      <main className="mt-9 flex w-full max-w-[440px] flex-col rounded-2xl border border-line bg-panel p-8">
        {children}
      </main>

      <p className="mt-6 mb-0 max-w-[440px] text-center text-xs text-muted">
        By continuing you agree to the <a href={`${SITE}/terms/`}>Terms</a> and{' '}
        <a href={`${SITE}/privacy/`}>Privacy Policy</a>. Roundturn analyzes data you upload and is not investment
        advice.
      </p>
    </div>
  )
}
