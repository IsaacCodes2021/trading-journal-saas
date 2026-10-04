import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EXPORT_URL } from '../../lib/exportData'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => vi.unstubAllGlobals())

async function open() {
  stubApi()
  openApp('/settings/privacy')
  await screen.findByRole('heading', { name: 'Data & privacy' })
}

describe('the data and privacy section', () => {
  it('offers the download as a plain link to the server, so the file goes straight to disk', async () => {
    await open()
    const link = screen.getByRole('link', { name: 'Download my data' })
    expect(link).toHaveAttribute('href', '/api/v1/export')
    expect(link).toHaveAttribute('download')
    expect(EXPORT_URL).toBe('/api/v1/export')
  })

  it('says what is in the file and that it is exact', async () => {
    await open()
    const region = screen.getByRole('region', { name: 'Download your data' })
    for (const text of [
      /trades and the fills behind them/,
      /each as a web page and as plain text/,
      /size and checksum/,
      /exact/,
      /UTC/,
    ]) {
      expect(within(region).getByText(text)).toBeInTheDocument()
    }
    expect(within(region).getByText(/screenshots come with it as image files/)).toBeInTheDocument()
    expect(within(region).getByText(/the manifest says which one/)).toBeInTheDocument()
    expect(within(region).queryByText(/when screenshots are available/)).not.toBeInTheDocument() // no longer "later"
  })

  it('offers deleting the account as its own card, after the download, and says to download first', async () => {
    await open()
    const regions = screen.getAllByRole('region').map((region) => region.getAttribute('aria-label'))
    expect(regions).toEqual(['Download your data', 'Delete your account'])
    const region = screen.getByRole('region', { name: 'Delete your account' })
    expect(within(region).getByText(/Download your data first/)).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Delete my account…' })).toBeEnabled()
  })

  it('does not start a download or call the server just by being opened', async () => {
    await open()
    const calls = vi.mocked(globalThis.fetch).mock.calls.map(([request]) => new URL((request as Request).url).pathname)
    expect(calls).not.toContain('/api/v1/export')
  })

  it('is a real section: no sample-data banner, and it is marked in the list', async () => {
    await open()
    expect(screen.queryByText(/These numbers are made up/)).not.toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(within(nav).getByRole('link', { name: 'Data & privacy' })).toHaveAttribute('aria-current', 'page')
  })
})
