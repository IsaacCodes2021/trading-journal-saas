import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { go } from '../lib/auth'
import { DataSourceProvider, useDataSource } from '../lib/data/source'
import type { DataSource } from '../lib/data/model'
import { DataSourceRoot } from './DataSourceRoot'

const source = (label: string | null) => ({ label }) as DataSource

/** Shows which source the page below would read from. */
function Who() {
  const label = useDataSource().label
  return <p>reading from {label ?? 'the server'}</p>
}

let leaving: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  sessionStorage.clear()
  leaving = vi.spyOn(go, 'to').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

function inside(ui: React.ReactElement) {
  // Whatever the page would read from without the demo: here, the server.
  return render(<DataSourceProvider source={source(null)}>{ui}</DataSourceProvider>)
}

describe('when nobody asked for the demo', () => {
  it('leaves the page reading from where it was going to, and never loads the sample journal', () => {
    const load = vi.fn()
    inside(
      <DataSourceRoot live demo={false} load={load}>
        <Who />
      </DataSourceRoot>,
    )
    expect(screen.getByText('reading from the server')).toBeInTheDocument()
    expect(load).not.toHaveBeenCalled()
  })

  it('does nothing in development and in tests, where the sample journal is already what is read', () => {
    const load = vi.fn()
    inside(
      <DataSourceRoot live={false} demo load={load}>
        <Who />
      </DataSourceRoot>,
    )
    expect(screen.getByText('reading from the server')).toBeInTheDocument() // untouched: the context it sat in
    expect(load).not.toHaveBeenCalled()
  })

  it('is not the demo by default: a tab that was never switched on reads from where it was going to', () => {
    const load = vi.fn()
    inside(
      <DataSourceRoot live load={load}>
        <Who />
      </DataSourceRoot>,
    )
    expect(screen.getByText('reading from the server')).toBeInTheDocument()
    expect(load).not.toHaveBeenCalled()
  })

  it('takes the switch from this tab by default', () => {
    sessionStorage.setItem('roundturn-demo', '1')
    const load = vi.fn(() => new Promise<DataSource>(() => undefined))
    inside(
      <DataSourceRoot live load={load}>
        <Who />
      </DataSourceRoot>,
    )
    expect(load).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('reading from the server')).not.toBeInTheDocument()
  })
})

describe('when the demo was asked for', () => {
  it('says it is loading, then has the page read from the sample journal and not from the server', async () => {
    let finish: (value: DataSource) => void = () => undefined
    const load = vi.fn(() => new Promise<DataSource>((resolve) => (finish = resolve)))
    inside(
      <DataSourceRoot live demo load={load}>
        <Who />
      </DataSourceRoot>,
    )
    expect(screen.getByRole('status')).toHaveTextContent('Loading the sample data')
    expect(screen.queryByText(/reading from/)).not.toBeInTheDocument() // nothing is read in the meantime
    finish(source('Sample data'))
    expect(await screen.findByText('reading from Sample data')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('loads the real sample journal by default: the same one the tests and the dev server use', async () => {
    inside(
      <DataSourceRoot live demo>
        <Who />
      </DataSourceRoot>,
    )
    expect(await screen.findByText('reading from Sample data')).toBeInTheDocument()
  })

  it('does not load it again when the page draws again', async () => {
    const load = vi.fn(() => Promise.resolve(source('Sample data')))
    const view = inside(
      <DataSourceRoot live demo load={load}>
        <Who />
      </DataSourceRoot>,
    )
    await screen.findByText('reading from Sample data')
    view.rerender(
      <DataSourceProvider source={source(null)}>
        <DataSourceRoot live demo load={load}>
          <Who />
        </DataSourceRoot>
      </DataSourceProvider>,
    )
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('says so when the sample journal cannot be loaded, and offers the way back to the person’s own data', async () => {
    const load = vi.fn(() => Promise.reject(new Error('chunk failed')))
    inside(
      <DataSourceRoot live demo load={load}>
        <Who />
      </DataSourceRoot>,
    )
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The sample data could not be loaded.')
    expect(screen.queryByText(/reading from/)).not.toBeInTheDocument() // never falls back to showing the server's data as sample
    await userEvent.setup().click(screen.getByRole('button', { name: 'Go back to my own data' }))
    expect(leaving).toHaveBeenCalledExactlyOnceWith('/')
    expect(sessionStorage.getItem('roundturn-demo')).toBeNull()
  })

  it('does not set anything when it is taken off the page before the sample journal arrives', async () => {
    let finish: (value: DataSource) => void = () => undefined
    const load = vi.fn(() => new Promise<DataSource>((resolve) => (finish = resolve)))
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const view = inside(
      <DataSourceRoot live demo load={load}>
        <Who />
      </DataSourceRoot>,
    )
    view.unmount()
    finish(source('Sample data'))
    await waitFor(() => expect(load).toHaveBeenCalled())
    expect(errors).not.toHaveBeenCalled()
  })
})
