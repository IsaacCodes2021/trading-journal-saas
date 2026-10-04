import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { go } from './lib/auth'
import { openApp, stubApi } from './test-utils'

// The dashboard stands in for any screen that fails while it is drawn.
const failure = vi.hoisted(() => ({ message: 'boom' }))
vi.mock('./features/dashboard/DashboardPage', () => ({
  DashboardPage: () => {
    throw new Error(failure.message)
  },
}))

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('a screen that fails inside the app', () => {
  it('is replaced by the notice, inside the app, and not by a blank page', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined) // React and the router log what they catch
    stubApi()
    failure.message = 'secretInternalDetail'
    openApp('/')
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent('Something went wrong')
    expect(notice).not.toHaveTextContent('secretInternalDetail')
  })

  it('says the app was updated, and reloads, when the failure is a screen that could not be fetched', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const reload = vi.spyOn(go, 'reload').mockImplementation(() => undefined)
    stubApi()
    failure.message = 'Failed to fetch dynamically imported module: https://app.example.com/assets/ImportPage-abc.js'
    openApp('/')
    expect(await screen.findByRole('heading', { name: 'Roundturn was updated' })).toBeInTheDocument()
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
