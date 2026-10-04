import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AccountDetail, AccountRef } from '../../lib/data/model'
import { resetDemoAccounts } from '../../lib/demo/accounts'
import { resetDemoImports } from '../../lib/demo/imports'
import { demoSource } from '../../lib/demo/source'
import { REQUIRED_COLUMNS } from '../../lib/tradovate'
import { csv, csvFile, manyRows } from '../../test-csv'
import { openApp, stubApi, type ApiOptions } from '../../test-utils'
import { detectedZone, parseStep } from './steps'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoAccounts()
  resetDemoImports()
  document.documentElement.removeAttribute('data-theme')
})

async function open(step?: number, options: ApiOptions = {}) {
  const saved = stubApi(options)
  const app = openApp('/welcome', step ? { step } : {})
  await screen.findByRole('heading', { name: /^Welcome\./ })
  return { ...app, saved, user: userEvent.setup({ applyAccept: false }) }
}

const step = (title: string) => screen.findByRole('region', { name: title })
const stepOf = (router: { state: { location: { search: unknown } } }) =>
  (router.state.location.search as { step?: number }).step

/** A person with no accounts yet: the account list starts empty, and a created account joins it. */
function noAccountsYet() {
  let list: AccountRef[] = []
  vi.spyOn(demoSource, 'accounts').mockImplementation(() => Promise.resolve(list))
  vi.spyOn(demoSource, 'createAccount').mockImplementation((input) => {
    const account = { id: 99, name: input.name, firm: input.firm, account_type: input.account_type } as AccountRef
    list = [...list, account]
    return Promise.resolve(account as unknown as AccountDetail)
  })
}

/** What the browser reports as its own time zone. */
function browserIsIn(timeZone: string) {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({
    timeZone,
  } as Intl.ResolvedDateTimeFormatOptions)
}

describe('the step in the address', () => {
  it.each([
    [undefined, 1],
    ['1', 1],
    ['3', 3],
    [5, 5],
    ['0', 1],
    ['6', 1],
    ['abc', 1],
    ['2.5', 1],
    ['', 1],
    [null, 1],
    [['3'], 1],
  ])('reads %j as step %i', (value, expected) => {
    expect(parseStep(value)).toBe(expected)
  })

  it('is the browser zone only if it is one the app can use', () => {
    browserIsIn('Europe/London')
    expect(detectedZone()).toBe('Europe/London')
    browserIsIn('UTC')
    expect(detectedZone()).toBe('UTC')
    browserIsIn('Not/AZone')
    expect(detectedZone()).toBeNull()
    browserIsIn('')
    expect(detectedZone()).toBeNull()
  })
})

describe('the first-run setup', () => {
  it('opens on the first step, lists all five, offers a way out, and shows no main navigation', async () => {
    await open()
    const nav = screen.getByRole('navigation', { name: 'Setup steps' })
    expect(
      within(nav)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual([
      '1Confirm timezone',
      '2Add your first account',
      '3Export from Tradovate',
      '4Upload your CSV',
      '5See your dashboard',
    ])
    expect(within(nav).getByRole('button', { name: /Confirm timezone/ })).toHaveAttribute('aria-current', 'step')
    expect(within(nav).getByRole('button', { name: /Add your first account/ })).not.toHaveAttribute('aria-current')
    expect(screen.getByText('Step 1 of 5')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Skip setup for now' })).toHaveAttribute('href', '/')
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Account menu' })).toBeInTheDocument()
    expect(screen.queryByText(/These numbers are made up/)).not.toBeInTheDocument()
  })

  it('opens on the step the address names, and on the first for anything else', async () => {
    const { unmount } = await open(3)
    expect(await step('Export from Tradovate')).toBeInTheDocument()
    unmount()
    await open(9)
    expect(await step('Confirm your timezone')).toBeInTheDocument()
  })

  it('moves with Back and Continue and the list, marks finished steps, and offers no Back on the first', async () => {
    noAccountsYet()
    const { user, router } = await open()
    expect(within(await step('Confirm your timezone')).getByRole('button', { name: 'Back' })).toBeDisabled()

    await user.click(within(await step('Confirm your timezone')).getByRole('button', { name: 'Continue' }))
    await step('Add your first account')
    await waitFor(() => expect(stepOf(router)).toBe(2))

    await user.click(
      within(screen.getByRole('region', { name: 'Add your first account' })).getByRole('button', { name: 'Back' }),
    )
    await step('Confirm your timezone')
    await waitFor(() => expect(stepOf(router)).toBeUndefined()) // the first step is the bare address

    await user.click(screen.getByRole('button', { name: /Upload your CSV/ })) // any step is one click away
    await step('Upload your CSV')
    const nav = screen.getByRole('navigation', { name: 'Setup steps' })
    expect(within(nav).getByRole('button', { name: /Confirm timezone \(done\)/ })).toBeInTheDocument()
    expect(within(nav).getByRole('button', { name: /Upload your CSV/ })).toHaveAttribute('aria-current', 'step')
  })
})

describe('step 1: timezone', () => {
  it('starts at the zone the browser reports, and says so', async () => {
    browserIsIn('Europe/London')
    await open()
    const region = await step('Confirm your timezone')
    expect(within(region).getByLabelText('Your timezone')).toHaveValue('Europe/London')
    expect(within(region).getByText('Detected from your browser.')).toBeInTheDocument()
    expect(within(region).getByRole('note')).toHaveTextContent('trades can land hours off and on the wrong trading day')
  })

  it('goes on without saving anything when nothing changed', async () => {
    browserIsIn('America/Los_Angeles') // the account already has it
    const { user, saved, router } = await open()
    await user.click(within(await step('Confirm your timezone')).getByRole('button', { name: 'Continue' }))
    await step('Add your first account')
    expect(saved).toEqual([])
    expect(stepOf(router)).toBe(2)
  })

  it('saves the timezone and the clock of the files together, then goes on', async () => {
    browserIsIn('Europe/London')
    const { user, saved } = await open()
    const region = await step('Confirm your timezone')
    await user.click(within(region).getByRole('radio', { name: /Exchange time/ }))
    await user.click(within(region).getByRole('button', { name: 'Continue' }))
    await step('Add your first account')
    expect(saved).toEqual([{ timezone: 'Europe/London', csv_timezone: 'America/Chicago' }])
  })

  it('clears a separate CSV zone with an empty value when "Same as my timezone" is chosen again', async () => {
    browserIsIn('America/Los_Angeles')
    const { user, saved } = await open(undefined, { settings: { csv_timezone: 'America/Chicago' } })
    const region = await step('Confirm your timezone')
    expect(within(region).getByRole('radio', { name: /Exchange time/ })).toBeChecked() // what is stored
    await user.click(within(region).getByRole('radio', { name: /Same as my timezone/ }))
    await user.click(within(region).getByRole('button', { name: 'Continue' }))
    await step('Add your first account')
    expect(saved).toEqual([{ csv_timezone: '' }]) // the server reads "" as "follow my timezone"; null would be refused
  })

  it('saves only what is different from what is stored', async () => {
    browserIsIn('America/Los_Angeles')
    const { user, saved } = await open()
    const region = await step('Confirm your timezone')
    await user.selectOptions(within(region).getByLabelText('Your timezone'), 'America/Denver')
    await user.click(within(region).getByRole('button', { name: 'Continue' }))
    await step('Add your first account')
    expect(saved).toEqual([{ timezone: 'America/Denver' }])
  })

  it('stays on the step and says so when the save fails', async () => {
    browserIsIn('Europe/London')
    const { user, router } = await open(undefined, { saveFails: true })
    await user.click(within(await step('Confirm your timezone')).getByRole('button', { name: 'Continue' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Confirm your timezone' })).toBeInTheDocument()
    expect(stepOf(router)).toBeUndefined()
    expect(within(screen.getByRole('region')).getByRole('button', { name: 'Continue' })).toBeEnabled() // and can try again
  })

  it("shows the server's sentence when it refuses a zone", async () => {
    browserIsIn('Europe/London')
    const { user } = await open(undefined, {
      saveProblem: {
        status: 422,
        body: {
          title: 'Unprocessable',
          status: 422,
          detail: null,
          errors: [{ field: 'timezone', message: 'Unknown time zone.' }],
        },
      },
    })
    await user.click(within(await step('Confirm your timezone')).getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Unknown time zone.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Confirm your timezone' })).toBeInTheDocument()
  })
})

describe('step 2: the first account', () => {
  it('asks for it, and Continue waits until there is one', async () => {
    noAccountsYet()
    const { user, router } = await open(2)
    const region = await step('Add your first account')
    expect(within(region).getByRole('button', { name: 'Continue' })).toBeDisabled()
    await user.type(within(region).getByLabelText('Account name'), 'Apex 50K #1')
    await user.click(within(region).getByRole('button', { name: 'Add account' }))

    expect(await within(region).findByRole('status')).toHaveTextContent('You have one account:')
    expect(within(region).getByText('Apex 50K #1')).toBeInTheDocument()
    expect(within(region).queryByLabelText('Account name')).not.toBeInTheDocument() // the form has done its job
    await user.click(within(region).getByRole('button', { name: 'Continue' }))
    await step('Export from Tradovate')
    expect(stepOf(router)).toBe(3)
  })

  it('lists the accounts already there and offers to add another, without making anyone', async () => {
    const { user } = await open(2)
    const region = await step('Add your first account')
    await waitFor(() => expect(within(region).getByRole('status')).toHaveTextContent('You have 4 accounts:'))
    expect(within(region).getByText('Lucid 100K')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Continue' })).toBeEnabled()
    expect(within(region).queryByLabelText('Account name')).not.toBeInTheDocument()
    await user.click(within(region).getByRole('button', { name: 'Add another account' }))
    expect(within(region).getByLabelText('Account name')).toBeInTheDocument()
    await user.click(within(region).getByRole('button', { name: 'Cancel' }))
    expect(within(region).queryByLabelText('Account name')).not.toBeInTheDocument()
  })

  it("shows the server's sentence when the account is refused, and keeps what was typed", async () => {
    noAccountsYet()
    vi.spyOn(demoSource, 'createAccount').mockRejectedValueOnce(
      new Error('There is already an account with that name.'),
    )
    const { user } = await open(2)
    const region = await step('Add your first account')
    await user.type(within(region).getByLabelText('Account name'), 'Apex 50K #1')
    await user.click(within(region).getByRole('button', { name: 'Add account' }))
    expect(await within(region).findByText('There is already an account with that name.')).toBeInTheDocument()
    expect(within(region).getByLabelText('Account name')).toHaveValue('Apex 50K #1')
    expect(within(region).getByRole('button', { name: 'Continue' })).toBeDisabled()
  })
})

describe('step 3: the file', () => {
  it('says what to look for and lists exactly the columns the file must have', async () => {
    await open(3)
    const region = await step('Export from Tradovate')
    expect(within(region).getAllByRole('listitem').length).toBeGreaterThan(REQUIRED_COLUMNS.length)
    const columns = within(within(region).getByRole('list', { name: 'Required columns' })).getAllByRole('listitem')
    expect(columns.map((c) => c.textContent)).toEqual([...REQUIRED_COLUMNS])
    expect(within(region).getByText(/Performance/, { selector: 'strong' })).toBeInTheDocument()
    expect(within(region).getByText(/Nothing is saved until you confirm a preview/)).toBeInTheDocument()
  })
})

describe('step 4: upload', () => {
  it('imports a file inside the setup, says what happened, and then lets Continue say Continue', async () => {
    const { user, router } = await open(4)
    const region = await step('Upload your CSV')
    const next = within(region).getByRole('button', { name: 'Skip for now' }) // nothing imported yet
    expect(next).toBeEnabled()

    await user.upload(
      within(region).getByLabelText('Choose a Performance CSV'),
      csvFile(csv(manyRows(3)), 'Performance_2026-10-02.csv'),
    )
    await user.click(await within(region).findByRole('button', { name: 'Import 3 fill pairs' }))
    expect(
      await within(region).findByText('Imported 3 fill pairs into Apex 50K #1, making 3 trades.'),
    ).toBeInTheDocument()
    expect(within(region).queryByRole('region', { name: 'Import history' })).not.toBeInTheDocument() // the history is on the Import screen

    await user.click(within(region).getByRole('button', { name: 'Continue' }))
    const done = await step('You are all set')
    expect(done).toHaveTextContent(
      'Your first file is in: 3 fill pairs from Performance_2026-10-02.csv became 3 trades in Apex 50K #1.',
    )
    expect(stepOf(router)).toBe(5)
    expect(within(done).getByRole('link', { name: 'Import another file' })).toHaveAttribute('href', '/import')
  })

  it('names the zone the files are read in: the CSV zone, not the display zone', async () => {
    await open(4, { settings: { csv_timezone: 'America/Chicago' } })
    const region = await step('Upload your CSV')
    expect(await within(region).findByText('America/Chicago')).toBeInTheDocument()
    expect(within(region).queryByText('America/Los_Angeles')).not.toBeInTheDocument()
  })

  it('lets a person without their file skip it', async () => {
    const { user } = await open(4)
    await user.click(within(await step('Upload your CSV')).getByRole('button', { name: 'Skip for now' }))
    const done = await step('You are all set')
    expect(done).toHaveTextContent('You have not imported a file yet')
    expect(within(done).getByRole('link', { name: 'Import a file' })).toHaveAttribute('href', '/import')
  })

  it('tells what is wrong with a file that is not a Performance export', async () => {
    const { user } = await open(4)
    const region = await step('Upload your CSV')
    await user.upload(within(region).getByLabelText('Choose a Performance CSV'), csvFile('a,b\n1,2', 'orders.csv'))
    expect(await within(region).findByRole('alert')).toHaveTextContent(/missing/i)
    expect(within(region).getByRole('button', { name: 'Skip for now' })).toBeEnabled() // and nobody is stuck
  })
})

describe('step 5: done', () => {
  it('ends at the dashboard, and points to the places to go next', async () => {
    const { user, router } = await open(5)
    const region = await step('You are all set')
    expect(within(region).getByRole('link', { name: 'Set up account rules' })).toHaveAttribute(
      'href',
      '/settings/accounts',
    )
    expect(within(region).getByRole('link', { name: 'Preferences' })).toHaveAttribute('href', '/settings/preferences')
    await user.click(within(region).getByRole('button', { name: 'Open my dashboard' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeInTheDocument() // the full app is back
  })

  it('can go Back to the upload', async () => {
    const { user } = await open(5)
    await user.click(within(await step('You are all set')).getByRole('button', { name: 'Back' }))
    await step('Upload your CSV')
  })
})

describe('where a new person starts', () => {
  it('sees where to begin instead of an empty chart, and the link opens the setup', async () => {
    noAccountsYet()
    stubApi()
    const { router } = openApp('/')
    const card = await screen.findByRole('region', { name: 'Get started' })
    expect(card).toHaveTextContent('Your journal is empty')
    const link = within(card).getByRole('link', { name: 'Start setup' })
    expect(link).toHaveAttribute('href', '/welcome')
    expect(screen.queryByRole('group', { name: 'Date range' })).not.toBeInTheDocument() // no filters over nothing
    expect(screen.queryByRole('region', { name: 'Key statistics' })).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument() // the app is still the app

    await userEvent.setup().click(link)
    expect(await screen.findByRole('heading', { name: /^Welcome\./ })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/welcome')
  })

  it('is not shown to anyone who has an account', async () => {
    stubApi()
    openApp('/')
    expect(await screen.findByRole('group', { name: 'Date range' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Get started' })).not.toBeInTheDocument()
  })
})
