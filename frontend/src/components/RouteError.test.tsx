import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { go } from '../lib/auth'
import { stubApi, openApp } from '../test-utils'
import {
  AppErrorBoundary,
  NotFound,
  RouteError,
  SUPPORT_ADDRESS,
  isStaleBuildError,
  reloadForNewVersion,
} from './RouteError'

beforeEach(() => {
  sessionStorage.clear()
  vi.spyOn(go, 'reload').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const STALE_MESSAGES = [
  'Failed to fetch dynamically imported module: https://app.example.com/assets/ImportPage-abc123.js', // Chrome
  'error loading dynamically imported module: https://app.example.com/assets/ImportPage-abc123.js', // Firefox
  'Importing a module script failed.', // Safari
  'Loading chunk 42 failed.',
  'Unable to preload CSS for /assets/index-abc.css',
]

describe('telling a page opened before an update from a real failure', () => {
  it.each(STALE_MESSAGES)('knows %s', (message) => {
    expect(isStaleBuildError(new Error(message))).toBe(true)
    expect(isStaleBuildError(message)).toBe(true)
  })

  it.each([
    new Error('Cannot read properties of undefined'),
    new TypeError('x is not a function'),
    null,
    undefined,
    42,
    {},
  ])('does not mistake %s for one', (error) => {
    expect(isStaleBuildError(error)).toBe(false)
  })

  it('ignores case, as browsers differ', () => {
    expect(isStaleBuildError(new Error('FAILED TO FETCH DYNAMICALLY IMPORTED MODULE'))).toBe(true)
  })
})

describe('reloading for a new version', () => {
  it('reloads, and remembers that it did', () => {
    expect(reloadForNewVersion(1_000_000)).toBe(true)
    expect(go.reload).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('roundturn-reloaded-at')).toBe('1000000')
  })

  it('reloads the first time whatever the clock says, even in the first minute of the day', () => {
    expect(reloadForNewVersion(30_000)).toBe(true)
  })

  it('does not reload again within a minute, so a screen that is really broken cannot loop', () => {
    reloadForNewVersion(1_000_000)
    expect(reloadForNewVersion(1_059_999)).toBe(false)
    expect(go.reload).toHaveBeenCalledTimes(1)
  })

  it('reloads again after a minute', () => {
    reloadForNewVersion(1_000_000)
    expect(reloadForNewVersion(1_060_000)).toBe(true)
    expect(go.reload).toHaveBeenCalledTimes(2)
  })

  it('does nothing when the browser has no session storage, rather than risk a loop', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    })
    expect(reloadForNewVersion(1_000_000)).toBe(false)
    expect(go.reload).not.toHaveBeenCalled()
  })
})

describe('the answer to a screen that failed', () => {
  const fail = (message: string, reset = vi.fn()) => {
    render(<RouteError error={new Error(message)} reset={reset} info={undefined} />)
    return reset
  }

  it('says something went wrong in plain words, offers to try again, and never shows the failure itself', async () => {
    const reset = fail('Cannot read properties of undefined (reading secretInternalDetail)')
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('alert')).not.toHaveTextContent('secretInternalDetail')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(reset).toHaveBeenCalledTimes(1)
    expect(go.reload).not.toHaveBeenCalled()
  })

  it('gives somewhere to go and someone to write to', () => {
    fail('boom')
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: SUPPORT_ADDRESS })).toHaveAttribute('href', `mailto:${SUPPORT_ADDRESS}`)
  })

  it('does not reload for an ordinary failure', () => {
    fail('boom')
    expect(go.reload).not.toHaveBeenCalled()
  })

  it('says the app was updated when its code could not be fetched, and reloads at once, once', () => {
    fail(STALE_MESSAGES[0] as string)
    expect(screen.getByRole('alert')).toHaveTextContent('Roundturn was updated')
    expect(screen.queryByText(SUPPORT_ADDRESS)).not.toBeInTheDocument() // nothing is broken: no one to write to
    expect(go.reload).toHaveBeenCalledTimes(1)
  })

  it('offers a Reload button for a new version, which reloads and does not try the failed screen again', async () => {
    sessionStorage.setItem('roundturn-reloaded-at', String(Date.now())) // it has just reloaded: the button is the way on
    const reset = fail(STALE_MESSAGES[2] as string)
    expect(go.reload).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }))
    expect(go.reload).toHaveBeenCalledTimes(1)
    expect(reset).not.toHaveBeenCalled()
  })
})

describe('an address that matches no screen', () => {
  it('says so and leads home', () => {
    render(<NotFound />)
    expect(screen.getByRole('heading', { name: 'That page does not exist' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/')
  })

  it('is what the app shows for an unknown address', async () => {
    stubApi()
    openApp('/no-such-page' as never)
    expect(await screen.findByRole('heading', { name: 'That page does not exist' })).toBeInTheDocument()
  })
})

describe('the last net', () => {
  const Broken = ({ message }: { message: string }): never => {
    throw new Error(message)
  }

  it('shows its children while nothing fails', () => {
    render(
      <AppErrorBoundary>
        <p>all well</p>
      </AppErrorBoundary>,
    )
    expect(screen.getByText('all well')).toBeInTheDocument()
  })

  it('replaces a failing page with the notice instead of a blank screen', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined) // React logs what it catches
    render(
      <AppErrorBoundary>
        <Broken message="secretInternalDetail" />
      </AppErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('alert')).not.toHaveTextContent('secretInternalDetail')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(go.reload).toHaveBeenCalledTimes(1) // out here there is no router to reset, so it reloads
  })

  it('reloads for a new version when it was the code that could not be fetched', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(
      <AppErrorBoundary>
        <Broken message={STALE_MESSAGES[1] as string} />
      </AppErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Roundturn was updated')
    expect(go.reload).toHaveBeenCalledTimes(1)
  })
})
