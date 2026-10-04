import { ApiError, api, unwrap, type ProblemBody } from '../api/client'
import type { Filters } from '../filters'
import type { DataSource, TradesQuery } from './model'

/*
 * The data source that reads the real API. The routes it needs exist for the reads (dashboard, trade log and detail,
 * tags, accounts, filters); everything that writes, and the import, account-overview and ledger calls, are not
 * connected yet and fail with a plain message. So it is wired in only for development against a real server
 * (`TJ_DEV_API=real`, see `source.tsx`); the shipped app still reads the sample data until every screen is connected.
 *
 * Responses are the API's generated types passed straight through: the shapes in `model.ts` were written first and
 * the API returns them as they are (money as decimal strings, times as ISO 8601 UTC).
 */

/** An action the server cannot do yet, said in words a person can read. */
export class NotConnectedError extends Error {
  constructor(what: string) {
    super(`${what} is not connected to the server yet.`)
    this.name = 'NotConnectedError'
  }
}

const notConnected = (what: string) => (): Promise<never> => Promise.reject(new NotConnectedError(what))

function filterQuery(filters: Filters) {
  return {
    preset: filters.preset,
    account_type: filters.account_type,
    firms: filters.firms,
    symbol_roots: filters.symbol_roots,
    direction: filters.direction,
    show_archived: filters.show_archived,
  }
}

/** The log's filters, search and order, in the API's words (the same for the list and for a trade's neighbors). */
function listQuery(query: TradesQuery) {
  return { ...filterQuery(query), q: query.q?.trim() || undefined, sort: query.sort }
}

export const apiSource: DataSource = {
  label: null,

  dashboard: (filters, signal) =>
    unwrap(api.GET('/api/v1/dashboard', { params: { query: filterQuery(filters) }, signal })),

  trades: (query, signal) =>
    unwrap(
      api.GET('/api/v1/trades', {
        params: { query: { ...listQuery(query), limit: query.per_page, offset: (query.page - 1) * query.per_page } },
        signal,
      }),
    ),

  filterOptions: (signal) => unwrap(api.GET('/api/v1/filter-options', { signal })),

  async trade(id, signal) {
    const { data, error, response } = await api.GET('/api/v1/trades/{trade_id}', {
      params: { path: { trade_id: id } },
      signal,
    })
    if (response.status === 404) return null // nobody else's trade and a missing one look the same, as on the server
    if (data === undefined) throw new ApiError(response.status, error as ProblemBody | undefined)
    return data
  },

  neighbors: (id, query, signal) =>
    unwrap(
      api.GET('/api/v1/trades/{trade_id}/neighbors', {
        params: { path: { trade_id: id }, query: listQuery(query) },
        signal,
      }),
    ),

  tagCatalog: (signal) => unwrap(api.GET('/api/v1/tags', { signal })),

  accounts: (signal) => unwrap(api.GET('/api/v1/accounts', { signal })),

  saveNotes: notConnected('Saving notes'),
  setTradeTag: notConnected('Tagging a trade'),
  createTag: notConnected('Creating a tag'),
  addScreenshots: notConnected('Adding a screenshot'),
  deleteScreenshot: notConnected('Deleting a screenshot'),
  previewImport: notConnected('Importing'),
  commitImport: notConnected('Importing'),
  imports: notConnected('The import history'),
  undoPreview: notConnected('Undoing an import'),
  undoImport: notConnected('Undoing an import'),
  accountOverviews: notConnected('Account progress'),
  businessTotals: notConnected('The prop business totals'),
  createAccount: notConnected('Adding an account'),
  updateAccount: notConnected('Changing an account'),
  updateRules: notConnected('Changing account rules'),
  archiveAccount: notConnected('Archiving an account'),
  restoreAccount: notConnected('Restoring an account'),
  fees: notConnected('Fees'),
  setFee: notConnected('Changing a fee'),
  removeFee: notConnected('Removing a fee'),
  ledger: notConnected('The ledger'),
  addLedgerEntry: notConnected('Adding a ledger entry'),
  deleteLedgerEntry: notConnected('Deleting a ledger entry'),
}
