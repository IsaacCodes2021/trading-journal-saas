import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('data-theme')
})

async function open(
  path: '/settings' | `/settings/${string}` = '/settings/preferences',
  opts: Parameters<typeof stubApi>[0] = {},
) {
  const saved = stubApi(opts)
  const app = openApp(path)
  return { ...app, saved, user: userEvent.setup() }
}

describe('the settings frame', () => {
  it('lists the sections, marks the current one, and opens preferences first', async () => {
    const { router } = await open('/settings')
    await screen.findByRole('heading', { name: 'Preferences' })
    expect(router.state.location.pathname).toBe('/settings/preferences')
    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(
      within(nav)
        .getAllByRole('link')
        .map((a) => a.textContent),
    ).toEqual(['Preferences', 'Prop accounts', 'Tags', 'Plan & billing', 'Data & privacy'])
    expect(within(nav).getByRole('link', { name: 'Preferences' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Tags' })).not.toHaveAttribute('aria-current')
  })

  it('keeps Settings marked in the main navigation inside a section', async () => {
    await open('/settings/plan')
    await screen.findByRole('heading', { name: 'Plan & billing' })
    expect(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Settings' }),
    ).toHaveAttribute('aria-current', 'page')
  })

  it('says what is coming in the sections that are not built yet', async () => {
    const { user } = await open()
    const nav = await screen.findByRole('navigation', { name: 'Settings sections' })
    await user.click(within(nav).getByRole('link', { name: 'Tags' }))
    expect(await screen.findByText(/next screen to be built/)).toBeInTheDocument()
    await user.click(within(nav).getByRole('link', { name: 'Data & privacy' }))
    expect(await screen.findByText(/Exporting your data/)).toBeInTheDocument()
  })
})

describe('the sample-data banner', () => {
  it('is shown on screens that use sample data, and not on settings that are real', async () => {
    const { user } = await open('/settings/preferences')
    await screen.findByRole('heading', { name: 'Preferences' })
    expect(screen.queryByText(/These numbers are made up/)).not.toBeInTheDocument()

    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    await user.click(within(nav).getByRole('link', { name: 'Plan & billing' }))
    await screen.findByRole('heading', { name: 'Plan & billing' })
    expect(screen.queryByText(/These numbers are made up/)).not.toBeInTheDocument()

    await user.click(within(nav).getByRole('link', { name: 'Tags' })) // still sample, so the warning is back
    expect(await screen.findByText(/These numbers are made up/)).toBeInTheDocument()
  })
})

describe('preferences', () => {
  it('shows what is saved', async () => {
    await open()
    expect(await screen.findByLabelText('Your time zone')).toHaveValue('America/Los_Angeles')
    expect(screen.getByLabelText('Trading day time zone')).toHaveValue('America/New_York')
    expect(screen.getByLabelText('A trading day starts at')).toHaveValue('18:00')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled() // nothing changed yet
  })

  it('offers real zones, by name', async () => {
    await open()
    const select = await screen.findByLabelText('Your time zone')
    const names = within(select)
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(names).toContain('America / New York')
    expect(names).toContain('Europe / London')
    expect(names).toContain('UTC')
  })

  it('saves only what changed, and says so', async () => {
    const { user, saved } = await open()
    await user.selectOptions(await screen.findByLabelText('Your time zone'), 'Europe/London')
    const button = screen.getByRole('button', { name: 'Save changes' })
    expect(button).toBeEnabled()
    await user.click(button)
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(saved).toEqual([{ timezone: 'Europe/London' }])
    expect(button).toBeDisabled() // nothing left to save
    expect(screen.getByLabelText('Your time zone')).toHaveValue('Europe/London')
  })

  it('warns, before saving, that moving the trading day recalculates trades', async () => {
    const { user, saved } = await open()
    expect(screen.queryByText(/moves trades to the day they belong to/)).not.toBeInTheDocument()
    const start = await screen.findByLabelText('A trading day starts at')
    await user.clear(start)
    await user.type(start, '17:00')
    expect(screen.getByText(/moves trades to the day they belong to/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText('Saved')
    expect(saved).toEqual([{ trading_day_start: '17:00' }])
    expect(screen.queryByText(/moves trades to the day they belong to/)).not.toBeInTheDocument()
  })

  it('does not warn for a change that leaves the trading day alone', async () => {
    const { user } = await open()
    await user.selectOptions(await screen.findByLabelText('Your time zone'), 'UTC')
    expect(screen.queryByText(/moves trades to the day they belong to/)).not.toBeInTheDocument()
  })

  it('shows the server’s reason next to the field it is about', async () => {
    const { user } = await open('/settings/preferences', {
      saveProblem: {
        status: 422,
        body: {
          title: 'Unprocessable Content',
          status: 422,
          detail: 'The request is not valid.',
          errors: [{ field: 'trading_day_timezone', message: 'Unknown time zone' }],
        },
      },
    })
    await user.selectOptions(await screen.findByLabelText('Trading day time zone'), 'UTC')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown time zone')
    expect(screen.getByLabelText('Trading day time zone')).toHaveValue('UTC') // what was chosen is kept
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled()
  })

  it('says so, in words, when saving fails for another reason', async () => {
    const { user } = await open('/settings/preferences', { saveFails: true })
    await user.selectOptions(await screen.findByLabelText('Your time zone'), 'UTC')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The server had a problem. Try again in a moment.')
  })

  it('holds back a start time that is not a time', async () => {
    const { user } = await open()
    const start = await screen.findByLabelText('A trading day starts at')
    await user.clear(start)
    expect(await screen.findByRole('alert')).toHaveTextContent('Use a time like 18:00.')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('switches the theme at once from here too, and saves it', async () => {
    const { user, saved } = await open()
    const group = await screen.findByRole('group', { name: 'Theme' })
    expect(within(group).getByRole('button', { name: 'Dark' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(group).getByRole('button', { name: 'Light' }))
    expect(document.documentElement.dataset['theme']).toBe('light')
    await waitFor(() => expect(saved).toEqual([{ theme: 'light' }]))
    await waitFor(() =>
      expect(within(group).getByRole('button', { name: 'Light' })).toHaveAttribute('aria-pressed', 'true'),
    )
  })

  it('puts the theme back when saving it fails', async () => {
    const { user } = await open('/settings/preferences', { saveFails: true })
    const group = await screen.findByRole('group', { name: 'Theme' })
    await user.click(within(group).getByRole('button', { name: 'Light' }))
    await waitFor(() => expect(document.documentElement.dataset['theme']).toBe('dark'))
  })
})

describe('the timezone of Tradovate files', () => {
  it('starts as "the same as my timezone", naming it', async () => {
    await open()
    const group = await screen.findByRole('radiogroup', { name: 'Timezone of Tradovate CSV exports' })
    expect(within(group).getByRole('radio', { name: /Same as my timezone/ })).toBeChecked()
    expect(within(group).getByText('America / Los Angeles')).toBeInTheDocument()
    expect(within(group).getByText('America / Chicago (CME)')).toBeInTheDocument()
    expect(screen.queryByLabelText('Timezone of your CSV exports')).not.toBeInTheDocument()
  })

  it('saves exchange time, and says only new imports are affected', async () => {
    const { user, saved } = await open()
    const group = await screen.findByRole('radiogroup', { name: 'Timezone of Tradovate CSV exports' })
    await user.click(within(group).getByRole('radio', { name: /Exchange time/ }))
    expect(screen.getByText(/Files you import from now on are read in this zone/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText('Saved')
    expect(saved).toEqual([{ csv_timezone: 'America/Chicago' }])
    expect(within(group).getByRole('radio', { name: /Exchange time/ })).toBeChecked()
    expect(screen.queryByText(/Files you import from now on/)).not.toBeInTheDocument()
  })

  it('offers the full list for "something else"', async () => {
    const { user, saved } = await open()
    await user.click(await screen.findByRole('radio', { name: /Something else/ }))
    await user.selectOptions(screen.getByLabelText('Timezone of your CSV exports'), 'Europe/London')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText('Saved')
    expect(saved).toEqual([{ csv_timezone: 'Europe/London' }])
  })

  it('shows a saved choice, and goes back to following the own timezone with an empty value', async () => {
    const { user, saved } = await open('/settings/preferences', { settings: { csv_timezone: 'America/Chicago' } })
    const group = await screen.findByRole('radiogroup', { name: 'Timezone of Tradovate CSV exports' })
    expect(within(group).getByRole('radio', { name: /Exchange time/ })).toBeChecked()
    await user.click(within(group).getByRole('radio', { name: /Same as my timezone/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText('Saved')
    expect(saved).toEqual([{ csv_timezone: '' }])
    expect(within(group).getByRole('radio', { name: /Same as my timezone/ })).toBeChecked()
  })

  it('shows another saved zone as "something else", with that zone chosen', async () => {
    await open('/settings/preferences', { settings: { csv_timezone: 'Europe/London' } })
    expect(await screen.findByRole('radio', { name: /Something else/ })).toBeChecked()
    expect(screen.getByLabelText('Timezone of your CSV exports')).toHaveValue('Europe/London')
  })

  it('does not ask to save when nothing changed', async () => {
    await open()
    await screen.findByRole('radiogroup', { name: 'Timezone of Tradovate CSV exports' })
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('shows the server’s reason next to the choice', async () => {
    const { user } = await open('/settings/preferences', {
      saveProblem: {
        status: 422,
        body: {
          title: 'Unprocessable Content',
          status: 422,
          detail: 'The request is not valid.',
          errors: [{ field: 'csv_timezone', message: 'not a timezone name such as America/New_York' }],
        },
      },
    })
    await user.click(await screen.findByRole('radio', { name: /Exchange time/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('not a timezone name such as America/New_York')).toBeInTheDocument()
  })
})

describe('plan & billing', () => {
  it('shows the free plan', async () => {
    await open('/settings/plan')
    const card = await screen.findByRole('region', { name: 'Your plan' })
    expect(card).toHaveTextContent('free')
    expect(card).toHaveTextContent('The free plan.')
    expect(card).not.toHaveTextContent('left')
    expect(screen.getByRole('region', { name: 'What your plan includes' })).toHaveTextContent(
      'Nothing is limited on this plan.',
    )
  })

  it('shows a plan that is running out, with the date and the days left', async () => {
    const ends = new Date(Date.now() + 9 * 86_400_000).toISOString()
    await open('/settings/plan', {
      plan: { effective: 'pro', source: 'grant', expires_at: ends },
      entitlements: { max_accounts: 10, screenshots_enabled: true, csv_export: false },
    })
    const card = await screen.findByRole('region', { name: 'Your plan' })
    expect(card).toHaveTextContent('pro')
    expect(card).toHaveTextContent('9 days left')
    expect(card).toHaveTextContent('Given to you by an invitation or a code.')
    expect(card).toHaveTextContent(/Ends [A-Z][a-z]{2} \d{1,2}, \d{4}\./)

    const included = screen.getByRole('region', { name: 'What your plan includes' })
    expect(within(included).getByText('Max accounts').nextSibling).toHaveTextContent('10')
    expect(within(included).getByText('Screenshots enabled').nextSibling).toHaveTextContent('Included')
    expect(within(included).getByText('Csv export').nextSibling).toHaveTextContent('Not included')
  })

  it('says a plan has ended once it has', async () => {
    await open('/settings/plan', {
      plan: { effective: 'free', source: 'grant', expires_at: '2020-01-01T00:00:00Z' },
    })
    expect(await screen.findByText(/^Ended Jan 1, 2020\.$/)).toBeInTheDocument()
  })
})
