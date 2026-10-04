import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { NotConnectedError, apiSource } from './apiSource'
import type { TradesQuery } from './model'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

/** Answer every call with `answer`, and remember the URLs asked for. */
function serve(answer: (url: URL) => Response) {
  const asked: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      asked.push(url)
      return answer(url)
    }),
  )
  return asked
}

afterEach(() => vi.unstubAllGlobals())

const query: TradesQuery = { preset: '30d', sort: '-net_pnl', page: 3, per_page: 25 }

describe('the dashboard', () => {
  it('sends the preset and only the filters that narrow, one key for each value of a list', async () => {
    const body = {
      range: null,
      summary: {},
      equity_by_trade: [],
      equity_by_day: [],
      daily: [],
      score: null,
      accounts: [],
    }
    const asked = serve(() => json(body))
    const result = await apiSource.dashboard({
      preset: '90d',
      account_type: 'funded',
      firms: ['Apex', 'Lucid'],
      direction: 'long',
    })
    expect(result).toEqual(body)
    const [url] = asked
    expect(url?.pathname).toBe('/api/v1/dashboard')
    expect(url?.searchParams.get('preset')).toBe('90d')
    expect(url?.searchParams.get('account_type')).toBe('funded')
    expect(url?.searchParams.getAll('firms')).toEqual(['Apex', 'Lucid'])
    expect(url?.searchParams.get('direction')).toBe('long')
    expect(url?.searchParams.has('symbol_roots')).toBe(false)
    expect(url?.searchParams.has('show_archived')).toBe(false) // off is the server's default: nothing is sent
  })

  it('asks for archived accounts only when the person turned them on', async () => {
    const asked = serve(() => json({}))
    await apiSource.dashboard({ preset: 'all', show_archived: true })
    expect(asked[0]?.searchParams.get('show_archived')).toBe('true')
  })

  it('throws what the server said when it refuses', async () => {
    serve(() =>
      json({ title: 'Unprocessable Entity', status: 422, detail: 'The request is not valid.', errors: null }, 422),
    )
    await expect(apiSource.dashboard({ preset: '30d' })).rejects.toMatchObject({ status: 422 })
  })
})

describe('the trade log', () => {
  it('turns a page number into an offset and sends the search and the order', async () => {
    const page = { items: [], total: 0, net_pnl: '0.00' }
    const asked = serve(() => json(page))
    expect(await apiSource.trades({ ...query, q: '  NQ  ' })).toEqual(page)
    const [url] = asked
    expect(url?.pathname).toBe('/api/v1/trades')
    expect(url?.searchParams.get('limit')).toBe('25')
    expect(url?.searchParams.get('offset')).toBe('50') // page 3 of 25 starts after 50 rows
    expect(url?.searchParams.get('q')).toBe('NQ')
    expect(url?.searchParams.get('sort')).toBe('-net_pnl')
    expect(url?.searchParams.get('preset')).toBe('30d')
  })

  it('leaves a blank search out', async () => {
    const asked = serve(() => json({ items: [], total: 0, net_pnl: '0.00' }))
    await apiSource.trades({ ...query, q: '   ', page: 1 })
    expect(asked[0]?.searchParams.has('q')).toBe(false)
    expect(asked[0]?.searchParams.get('offset')).toBe('0')
  })
})

describe('one trade', () => {
  it("is null when the server says there is no such trade (or it is not this person's)", async () => {
    serve(() => json({ title: 'Not Found', status: 404, detail: 'That trade does not exist.', errors: null }, 404))
    expect(await apiSource.trade(42)).toBeNull()
  })

  it('returns the trade, and other failures are errors, not "no such trade"', async () => {
    const asked = serve(() => json({ id: 42 }))
    expect(await apiSource.trade(42)).toEqual({ id: 42 })
    expect(asked[0]?.pathname).toBe('/api/v1/trades/42')
    serve(() => json({ title: 'Internal Server Error', status: 500, detail: null, errors: null }, 500))
    await expect(apiSource.trade(42)).rejects.toBeInstanceOf(ApiError)
  })

  it('asks for its neighbours in the list the person came from', async () => {
    const asked = serve(() => json({ previous: 7, next: null }))
    expect(await apiSource.neighbors(42, { ...query, q: 'es', direction: 'short' })).toEqual({
      previous: 7,
      next: null,
    })
    const [url] = asked
    expect(url?.pathname).toBe('/api/v1/trades/42/neighbors')
    expect(url?.searchParams.get('sort')).toBe('-net_pnl')
    expect(url?.searchParams.get('q')).toBe('es')
    expect(url?.searchParams.get('direction')).toBe('short')
    expect(url?.searchParams.has('limit')).toBe(false) // the arrows follow the whole list, not one page of it
  })
})

describe('the small reads', () => {
  it('reads the filter choices, the tag catalog and the accounts', async () => {
    const asked = serve((url) =>
      json(url.pathname.endsWith('accounts') ? [] : { firms: [], symbol_roots: [], groups: [], tags: [] }),
    )
    await apiSource.filterOptions()
    await apiSource.tagCatalog()
    await apiSource.accounts()
    expect(asked.map((url) => url.pathname)).toEqual(['/api/v1/filter-options', '/api/v1/tags', '/api/v1/accounts'])
  })
})

describe('what is not connected yet', () => {
  it('says so in words, for writes and for the screens still on sample data', async () => {
    const asked = serve(() => json({}))
    await expect(apiSource.saveNotes(1, '<p>x</p>')).rejects.toBeInstanceOf(NotConnectedError)
    await expect(apiSource.imports()).rejects.toThrow('The import history is not connected to the server yet.')
    await expect(apiSource.setFee(1, 'NQ', '4.00')).rejects.toThrow('Changing a fee')
    expect(asked).toHaveLength(0) // it did not even try the network
  })

  it('is not labelled as sample data', () => {
    expect(apiSource.label).toBeNull()
  })
})
