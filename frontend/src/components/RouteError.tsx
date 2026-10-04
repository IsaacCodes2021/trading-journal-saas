import { Component, useEffect, type ReactNode } from 'react'
import { type ErrorComponentProps } from '@tanstack/react-router'
import { go } from '../lib/auth'
import { Button, Card } from './ui/primitives'

/** Where a person can write when something keeps going wrong. */
export const SUPPORT_ADDRESS = 'hello@roundturn.app'

/**
 * What a browser says when it cannot load a screen's code: the page was opened before the app was updated, and the file it
 * asks for no longer exists (the build names each file by its content). Chrome, Firefox and Safari word it differently.
 */
const STALE_BUILD =
  /dynamically imported module|Importing a module script failed|Loading chunk [\w-]+ failed|Unable to preload CSS/i

export function isStaleBuildError(error: unknown): boolean {
  const text = error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  return STALE_BUILD.test(text)
}

const RELOADED_AT = 'roundturn-reloaded-at'
const RELOAD_ONCE_FOR_MS = 60_000

/**
 * Reload the page for a new version, at most once a minute so a screen that is really broken cannot reload for ever.
 * Returns whether it reloaded. Without session storage it does not (it could not tell whether it already had); the
 * button on the page is still there.
 */
export function reloadForNewVersion(now: number = Date.now()): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOADED_AT))
    if (last > 0 && now - last < RELOAD_ONCE_FOR_MS) return false
    sessionStorage.setItem(RELOADED_AT, String(now))
  } catch {
    return false
  }
  go.reload()
  return true
}

/** Nothing about the failure is shown: its text is for developers, and the person can do nothing with it. */
function Notice({ stale, onRetry }: { stale: boolean; onRetry: () => void }) {
  return (
    <Card className="mx-auto my-16 flex max-w-[520px] flex-col gap-3 px-6 py-6" role="alert">
      <h1 className="m-0 text-lg font-semibold">{stale ? 'Roundturn was updated' : 'Something went wrong'}</h1>
      <p className="m-0 text-muted">
        {stale
          ? 'A newer version is ready. Reload this page to carry on; nothing you saved is lost.'
          : 'This page ran into a problem. Nothing you saved is lost. Try again, or go back to the dashboard.'}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={onRetry}>
          {stale ? 'Reload' : 'Try again'}
        </Button>
        <a href="/" className="text-accent">
          Back to the dashboard
        </a>
      </div>
      {stale ? null : (
        <p className="m-0 text-xs text-muted">
          If it keeps happening, write to{' '}
          <a href={`mailto:${SUPPORT_ADDRESS}`} className="text-accent">
            {SUPPORT_ADDRESS}
          </a>{' '}
          and say what you were doing.
        </p>
      )}
    </Card>
  )
}

/** The router's answer to a screen that threw while it was drawn or loaded. */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const stale = isStaleBuildError(error)
  useEffect(() => {
    if (stale) reloadForNewVersion()
  }, [stale])
  return <Notice stale={stale} onRetry={stale ? () => go.reload() : reset} />
}

/** The router's answer to an address that matches no screen. */
export function NotFound() {
  return (
    <Card className="mx-auto my-16 flex max-w-[520px] flex-col gap-3 px-6 py-6">
      <h1 className="m-0 text-lg font-semibold">That page does not exist</h1>
      <p className="m-0 text-muted">The address may be old or mistyped.</p>
      <a href="/" className="text-accent">
        Back to the dashboard
      </a>
    </Card>
  )
}

/** The last net, outside the router: a failure there would otherwise leave a blank page. */
export class AppErrorBoundary extends Component<{ children: ReactNode }, { error: unknown; failed: boolean }> {
  override state = { error: null as unknown, failed: false }

  static getDerivedStateFromError(error: unknown) {
    return { error, failed: true }
  }

  override componentDidCatch(error: unknown) {
    if (isStaleBuildError(error)) reloadForNewVersion()
  }

  override render() {
    if (!this.state.failed) return this.props.children
    return <Notice stale={isStaleBuildError(this.state.error)} onRetry={() => go.reload()} />
  }
}
