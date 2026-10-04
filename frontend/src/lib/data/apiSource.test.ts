import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, setCsrfToken } from '../api/client'
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
  it('says so in words for screenshots, without trying the network', async () => {
    const asked = serve(() => json({}))
    await expect(apiSource.addScreenshots(1, [])).rejects.toBeInstanceOf(NotConnectedError)
    await expect(apiSource.addScreenshots(1, [])).rejects.toThrow('Adding a screenshot is not connected')
    await expect(apiSource.deleteScreenshot(1, 2)).rejects.toThrow('Deleting a screenshot')
    expect(asked).toHaveLength(0)
  })

  it('is not labelled as sample data', () => {
    expect(apiSource.label).toBeNull()
  })
})

interface Seen {
  method: string
  path: string
  body: unknown
  csrf: string | null
}

/** Answer every call with `answer`, remembering what was sent: the method, the path, the JSON body and the CSRF header. */
function record(answer: (seen: Seen) => Response) {
  const seen: Seen[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const text = await request.clone().text()
      const call: Seen = {
        method: request.method,
        path: new URL(request.url).pathname,
        body: text ? JSON.parse(text) : undefined,
        csrf: request.headers.get('x-csrf-token'),
      }
      seen.push(call)
      return answer(call)
    }),
  )
  return seen
}

const problem = (status: number, detail: string) =>
  json({ title: 'Unprocessable Entity', status, detail, errors: null }, status)

describe('the journal writes', () => {
  beforeEach(() => setCsrfToken('tok'))

  it("aves a note, with the session's CSRF token", async () => {
    const seen = record(() => json({ notes_html: '<p>x</p>' }))
    await apiSource.saveNotes(7, '<p>x</p>')
    expect(seen).toEqual([{ method: 'PUT', path: '/api/v1/trades/7/notes', body: { html: '<p>x</p>' }, csrf: 'tok' }])
  })

  it('puts a tag on with PUT and takes it off with DELETE', async () => {
    const seen = record(() => json([]))
    await apiSource.setTradeTag(7, 3, true)
    await apiSource.setTradeTag(7, 3, false)
    expect(seen.map((s) => [s.method, s.path])).toEqual([
      ['PUT', '/api/v1/trades/7/tags/3'],
      ['DELETE', '/api/v1/trades/7/tags/3'],
    ])
  })

  it("akes a tag and returns it, and a refused name reaches the page as the server's sentence", async () => {
    const tag = { id: 9, group_id: 2, name: 'FOMO', color: '#ff0000' }
    const seen = record(() => json(tag, 201))
    expect(await apiSource.createTag(2, 'FOMO')).toEqual(tag)
    expect(seen[0]).toMatchObject({ method: 'POST', path: '/api/v1/tags', body: { group_id: 2, name: 'FOMO' } })
    record(() => problem(422, 'Name is empty.'))
    await expect(apiSource.createTag(2, ' ')).rejects.toMatchObject({ message: 'Name is empty.', status: 422 })
  })
})

describe('the account writes', () => {
  beforeEach(() => setCsrfToken('tok'))
  const account = { id: 4, name: 'Apex 11' }
  const details = {
    name: 'Apex 11',
    firm: 'Apex',
    account_type: 'evaluation' as const,
    account_size: '50000.00',
    starting_balance: '50000.00',
  }

  it('adds, changes, rules, archives and restores an account', async () => {
    const seen = record(() => json(account))
    await apiSource.createAccount(details)
    await apiSource.updateAccount(4, details)
    const rules = {
      profit_target: '3000.00',
      max_drawdown: null,
      trailing_type: null,
      trail_lock_enabled: false,
      trail_lock_level: null,
      daily_loss_limit: null,
    }
    await apiSource.updateRules(4, rules)
    const archive = { outcome: 'passed' as const, ended_on: '2024-03-01', note: 'done' }
    await apiSource.archiveAccount(4, archive)
    await apiSource.restoreAccount(4)
    expect(seen.map((s) => [s.method, s.path, s.csrf])).toEqual([
      ['POST', '/api/v1/accounts', 'tok'],
      ['PUT', '/api/v1/accounts/4', 'tok'],
      ['PUT', '/api/v1/accounts/4/rules', 'tok'],
      ['POST', '/api/v1/accounts/4/archive', 'tok'],
      ['POST', '/api/v1/accounts/4/restore', 'tok'],
    ])
    expect(seen[0]?.body).toEqual(details)
    expect(seen[2]?.body).toEqual(rules)
    expect(seen[3]?.body).toEqual(archive)
  })

  it("hows a refused change as the server's own sentence", async () => {
    record(() => problem(409, 'You already have an account with that name.'))
    await expect(apiSource.createAccount(details)).rejects.toMatchObject({
      message: 'You already have an account with that name.',
      status: 409,
    })
  })

  it('reads and changes fees, keeping "*" (any other contract) as it is in the address', async () => {
    const seen = record(() => json({ '*': '4.00' }))
    expect(await apiSource.fees(4)).toEqual({ '*': '4.00' })
    await apiSource.setFee(4, '*', '1.00')
    await apiSource.removeFee(4, 'NQ')
    expect(seen.map((s) => [s.method, s.path])).toEqual([
      ['GET', '/api/v1/accounts/4/fees'],
      ['PUT', '/api/v1/accounts/4/fees/*'],
      ['DELETE', '/api/v1/accounts/4/fees/NQ'],
    ])
    expect(seen[1]?.body).toEqual({ rate: '1.00' })
  })

  it('reads, adds to and deletes from the ledger', async () => {
    const entry = { kind: 'payout' as const, amount: '1500.00', entry_date: '2024-04-01', note: '' }
    const seen = record((call) => json(call.method === 'POST' ? { id: 1, account_id: 4, ...entry } : []))
    expect(await apiSource.ledger(4)).toEqual([])
    expect(await apiSource.addLedgerEntry(4, entry)).toMatchObject({ id: 1, amount: '1500.00' })
    await apiSource.deleteLedgerEntry(4, 1)
    expect(seen.map((s) => [s.method, s.path])).toEqual([
      ['GET', '/api/v1/accounts/4/ledger'],
      ['POST', '/api/v1/accounts/4/ledger'],
      ['DELETE', '/api/v1/accounts/4/ledger/1'],
    ])
    expect(seen[1]?.body).toEqual(entry)
  })

  it('reads the progress overviews and the business totals', async () => {
    const seen = record(() => json([]))
    await apiSource.accountOverviews()
    await apiSource.businessTotals()
    expect(seen.map((s) => s.path)).toEqual(['/api/v1/account-overviews', '/api/v1/business-totals'])
  })
})

describe('importing', () => {
  beforeEach(() => setCsrfToken('tok'))
  const csv = new File([['symbol,qty', 'NQH4,1'].join(String.fromCharCode(10))], 'export.csv', { type: 'text/csv' })

  /** Answer every call, remembering how the upload was sent: its path, whether it was multipart, its text, the CSRF header. */
  function recordUploads(answer: () => Response) {
    const seen: { path: string; method: string; multipart: boolean; text: string; csrf: string | null }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: Request) => {
        const multipart = (request.headers.get('content-type') ?? '').startsWith('multipart/form-data')
        seen.push({
          path: new URL(request.url).pathname,
          method: request.method,
          multipart,
          text: await request.clone().text(),
          csrf: request.headers.get('x-csrf-token'),
        })
        return answer()
      }),
    )
    return seen
  }

  it('sends a preview and an import as multipart with the account and the file, never as JSON', async () => {
    const seen = recordUploads(() => json({ id: 1 }))
    await apiSource.previewImport(4, csv)
    await apiSource.commitImport(4, csv)
    expect(seen.map((s) => [s.method, s.path, s.multipart, s.csrf])).toEqual([
      ['POST', '/api/v1/imports/preview', true, 'tok'],
      ['POST', '/api/v1/imports', true, 'tok'],
    ])
    for (const { text } of seen) {
      expect(text).toContain('name="account_id"')
      // The file is a part of its own. (The test environment's File is not a browser's, so its bytes and file name are
      // not serialized here; the browser check and the server's tests cover the real upload.)
      expect(text).toContain('name="file"')
    }
  })

  it("shows a refused file as the server's sentence", async () => {
    recordUploads(() => problem(422, 'That is not a Tradovate Performance export.'))
    await expect(apiSource.commitImport(4, csv)).rejects.toMatchObject({
      message: 'That is not a Tradovate Performance export.',
      status: 422,
    })
  })

  it('reads the history and undoes an import after showing what it would remove', async () => {
    const seen = record(() => json([]))
    await apiSource.imports()
    await apiSource.undoPreview(7)
    await apiSource.undoImport(7)
    expect(seen.map((s) => [s.method, s.path, s.csrf])).toEqual([
      ['GET', '/api/v1/imports', null],
      ['POST', '/api/v1/imports/7/undo-preview', 'tok'],
      ['POST', '/api/v1/imports/7/undo', 'tok'],
    ])
  })
})
