import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Filters } from '../lib/filters'
import { FilterBar } from './FilterBar'

const OPTIONS = { firms: ['Apex', 'Lucid'], symbol_roots: ['ES', 'NQ'] }

function setup(filters: Filters = { preset: '30d' }, options = OPTIONS) {
  const onChange = vi.fn()
  render(<FilterBar filters={filters} options={options} onChange={onChange} summary="132 trades" />)
  return { onChange, user: userEvent.setup() }
}

describe('FilterBar', () => {
  it('shows the range as pressed and the other filters as "all"', () => {
    setup()
    const range = screen.getByRole('group', { name: 'Date range' })
    expect(
      within(range)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['7D', '30D', '90D', 'YTD', 'All'])
    expect(within(range).getByRole('button', { name: '30D' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /All account types/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /All firms/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /All symbols/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Long \+ short/ })).toBeInTheDocument()
    expect(screen.getByText('132 trades')).toBeInTheDocument()
  })

  it('shows archived accounts only when asked, and leaves the key out when it is off again', async () => {
    const { onChange, user } = setup({ preset: '30d' })
    const box = screen.getByRole('checkbox', { name: 'Show archived' })
    expect(box).not.toBeChecked()
    await user.click(box)
    expect(onChange).toHaveBeenLastCalledWith({ preset: '30d', show_archived: true })
  })

  it('shows the toggle as on from the address and turning it off drops the key', async () => {
    const { onChange, user } = setup({ preset: '30d', show_archived: true })
    const box = screen.getByRole('checkbox', { name: 'Show archived' })
    expect(box).toBeChecked()
    await user.click(box)
    expect(onChange).toHaveBeenLastCalledWith({ preset: '30d' })
  })

  it('changes the range and keeps everything else', async () => {
    const { onChange, user } = setup({ preset: '30d', direction: 'long' })
    await user.click(screen.getByRole('button', { name: 'YTD' }))
    expect(onChange).toHaveBeenCalledWith({ preset: 'ytd', direction: 'long' })
  })

  it('chooses one account type, then clears it by leaving the key out', async () => {
    const { onChange, user } = setup()
    await user.click(screen.getByRole('button', { name: /All account types/ }))
    await user.click(screen.getByRole('radio', { name: 'Funded' }))
    expect(onChange).toHaveBeenLastCalledWith({ preset: '30d', account_type: 'funded' })
  })

  it('takes a choice clicked on its words, not only on its round button', async () => {
    const { onChange, user } = setup()
    await user.click(screen.getByRole('button', { name: /All account types/ }))
    await user.click(within(screen.getByRole('group', { name: 'Account type' })).getByText('Funded'))
    expect(onChange).toHaveBeenLastCalledWith({ preset: '30d', account_type: 'funded' })
  })

  it('clears a choice without leaving an undefined key behind', async () => {
    const { onChange, user } = setup({ preset: '30d', account_type: 'funded' })
    await user.click(screen.getByRole('button', { name: /Funded/ }))
    await user.click(screen.getByRole('radio', { name: 'All account types' }))
    const next = onChange.mock.calls.at(-1)?.[0] as Record<string, unknown>
    expect(next).toEqual({ preset: '30d' })
    expect('account_type' in next).toBe(false)
  })

  it('builds a multi-selection one box at a time and summarizes it', async () => {
    const { onChange, user } = setup({ preset: '30d', firms: ['Apex'] })
    expect(screen.getByRole('button', { name: /Firms: Apex/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Firms: Apex/ }))
    expect(screen.getByRole('checkbox', { name: 'Apex' })).toBeChecked()
    await user.click(screen.getByRole('checkbox', { name: 'Lucid' }))
    expect(onChange).toHaveBeenLastCalledWith({ preset: '30d', firms: ['Apex', 'Lucid'] })
  })

  it('ticks a box clicked on its words, and keeps the panel open for the next one', async () => {
    const { onChange, user } = setup()
    await user.click(screen.getByRole('button', { name: /All firms/ }))
    await user.click(within(screen.getByRole('group', { name: 'Firms' })).getByText('Lucid'))
    expect(onChange).toHaveBeenLastCalledWith({ preset: '30d', firms: ['Lucid'] })
    expect(screen.getByRole('group', { name: 'Firms' })).toBeInTheDocument()
  })

  it('drops the key when the last box is unticked', async () => {
    const { onChange, user } = setup({ preset: '30d', symbol_roots: ['NQ'] })
    await user.click(screen.getByRole('button', { name: /NQ/ }))
    await user.click(screen.getByRole('checkbox', { name: 'NQ' }))
    const next = onChange.mock.calls.at(-1)?.[0] as Record<string, unknown>
    expect(next).toEqual({ preset: '30d' })
    expect('symbol_roots' in next).toBe(false)
  })

  it('says so when there is nothing to choose from yet', async () => {
    const { user } = setup({ preset: '30d' }, { firms: [], symbol_roots: [] })
    await user.click(screen.getByRole('button', { name: /All firms/ }))
    expect(screen.getByText('Nothing to choose yet')).toBeInTheDocument()
  })

  it('opens one panel as a labelled group, and closes it on Escape', async () => {
    const { user } = setup()
    const button = screen.getByRole('button', { name: /All firms/ })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('group', { name: 'Firms' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: 'Firms' })).not.toBeInTheDocument()
  })

  it('closes a panel on a click elsewhere', async () => {
    const { user } = setup()
    await user.click(screen.getByRole('button', { name: /All symbols/ }))
    expect(screen.getByRole('group', { name: 'Symbols' })).toBeInTheDocument()
    await user.click(document.body)
    expect(screen.queryByRole('group', { name: 'Symbols' })).not.toBeInTheDocument()
  })
})
