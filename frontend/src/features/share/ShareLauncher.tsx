import { useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, type ReactNode } from 'react'
import { usernamePromptPutAway } from '../../components/UsernamePrompt'
import type { SharePeriod } from '../../lib/data/model'
import type { Filters } from '../../lib/filters'
import { useSession } from '../../lib/session'

/*
 * What a page needs to offer sharing: the Share button, and the day or week pop-up. The pop-up, its card, the serif
 * font and the image maker are a chunk of their own, loaded only when Share is first clicked.
 */

const PeriodShareDialog = lazy(() => import('./PeriodShareDialog'))

/** Opens a share pop-up: a certificate-style picture to download or copy. */
export function ShareButton({ onClick, children = 'Share' }: { onClick: () => void; children?: ReactNode }) {
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      onClick={onClick}
      className="inline-flex h-[30px] cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-line bg-raised px-2.5 font-[inherit] text-[13px] text-ink hover:border-line-strong"
    >
      <ShareIcon />
      {children}
    </button>
  )
}

export function ShareIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3v12" />
      <path d="m7 8 5-5 5 5" />
      <path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6" />
    </svg>
  )
}

/** Whether a Share click may open its pop-up now: never over the "Pick a username" box, which shows while the session
 * asks for a username, until it is answered or put away. */
export function useMayShare(): () => boolean {
  const session = useSession()
  const queryClient = useQueryClient()
  return () => !session.data?.flags.ask_username || usernamePromptPutAway(queryClient)
}

/** The "Share day" or "Share week" pop-up, with the person's username and time zone. */
export function PeriodShare({
  period,
  filters,
  onClose,
}: {
  period: SharePeriod
  filters: Filters
  onClose: () => void
}) {
  const session = useSession()
  return (
    <Suspense fallback={null}>
      <PeriodShareDialog
        key={`${period.kind}-${period.start}`}
        period={period}
        filters={filters}
        timeZone={session.data?.settings.timezone ?? 'UTC'}
        username={session.data?.user.username ?? null}
        onClose={onClose}
      />
    </Suspense>
  )
}
