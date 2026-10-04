import { ApiError, api, unwrap, type ProblemBody } from '../api/client'
import type { components } from '../api/schema'
import type { Filters } from '../filters'
import type { DataSource, TradesQuery } from './model'

/*
 * The data source that talks to the real API. Everything is connected except screenshots, which fail with a plain
 * message and never touch the network: image files wait for the object store (M6). So it is wired in only for
 * development against a real server (`TJ_DEV_API=real`, see `source.tsx`); the shipped app still reads the sample data
 * until screenshots arrive.
 *
 * Responses are the API's generated types passed straight through: the shapes in `model.ts` were written first and
 * the API returns them as they are (money as decimal strings, times as ISO 8601 UTC). A refusal reaches the page as an
 * `ApiError` whose message is the server's own sentence, which every form already shows.
 */

type FirmName = components['schemas']['AccountDetailsIn']['firm']

/** A file upload is multipart, not JSON: the browser writes the boundary itself, so no content type is set here. */
function uploadForm(accountId: number, file: File): FormData {
  const form = new FormData()
  form.append('account_id', String(accountId))
  form.append('file', file)
  return form
}

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

  async saveNotes(id, html) {
    await unwrap(api.PUT('/api/v1/trades/{trade_id}/notes', { params: { path: { trade_id: id } }, body: { html } }))
  },

  async setTradeTag(id, tagId, on) {
    const params = { params: { path: { trade_id: id, tag_id: tagId } } }
    await unwrap(
      on
        ? api.PUT('/api/v1/trades/{trade_id}/tags/{tag_id}', params)
        : api.DELETE('/api/v1/trades/{trade_id}/tags/{tag_id}', params),
    )
  },

  createTag: (groupId, name) => unwrap(api.POST('/api/v1/tags', { body: { group_id: groupId, name } })),

  addScreenshots: notConnected('Adding a screenshot'),
  deleteScreenshot: notConnected('Deleting a screenshot'),
  previewImport: (accountId, file) =>
    unwrap(
      api.POST('/api/v1/imports/preview', {
        body: { account_id: accountId, file: file as unknown as string },
        bodySerializer: () => uploadForm(accountId, file),
      }),
    ),
  commitImport: (accountId, file) =>
    unwrap(
      api.POST('/api/v1/imports', {
        body: { account_id: accountId, file: file as unknown as string },
        bodySerializer: () => uploadForm(accountId, file),
      }),
    ),
  imports: (signal) => unwrap(api.GET('/api/v1/imports', { signal })),
  undoPreview: (importId) =>
    unwrap(api.POST('/api/v1/imports/{import_id}/undo-preview', { params: { path: { import_id: importId } } })),
  async undoImport(importId) {
    await unwrap(api.POST('/api/v1/imports/{import_id}/undo', { params: { path: { import_id: importId } } }))
  },
  accountOverviews: (signal) => unwrap(api.GET('/api/v1/account-overviews', { signal })),
  businessTotals: (signal) => unwrap(api.GET('/api/v1/business-totals', { signal })),

  // The firm is a closed list on the server; the form offers the same list, and anything else comes back as a refusal.
  createAccount: (input) => unwrap(api.POST('/api/v1/accounts', { body: { ...input, firm: input.firm as FirmName } })),
  updateAccount: (id, input) =>
    unwrap(
      api.PUT('/api/v1/accounts/{account_id}', {
        params: { path: { account_id: id } },
        body: { ...input, firm: input.firm as FirmName },
      }),
    ),
  updateRules: (id, input) =>
    unwrap(api.PUT('/api/v1/accounts/{account_id}/rules', { params: { path: { account_id: id } }, body: input })),
  archiveAccount: (id, input) =>
    unwrap(api.POST('/api/v1/accounts/{account_id}/archive', { params: { path: { account_id: id } }, body: input })),
  restoreAccount: (id) =>
    unwrap(api.POST('/api/v1/accounts/{account_id}/restore', { params: { path: { account_id: id } } })),

  fees: (id, signal) =>
    unwrap(api.GET('/api/v1/accounts/{account_id}/fees', { params: { path: { account_id: id } }, signal })),
  async setFee(id, symbolRoot, rate) {
    await unwrap(
      api.PUT('/api/v1/accounts/{account_id}/fees/{symbol_root}', {
        params: { path: { account_id: id, symbol_root: symbolRoot } },
        body: { rate },
      }),
    )
  },
  async removeFee(id, symbolRoot) {
    await unwrap(
      api.DELETE('/api/v1/accounts/{account_id}/fees/{symbol_root}', {
        params: { path: { account_id: id, symbol_root: symbolRoot } },
      }),
    )
  },

  ledger: (id, signal) =>
    unwrap(api.GET('/api/v1/accounts/{account_id}/ledger', { params: { path: { account_id: id } }, signal })),
  addLedgerEntry: (id, entry) =>
    unwrap(api.POST('/api/v1/accounts/{account_id}/ledger', { params: { path: { account_id: id } }, body: entry })),
  async deleteLedgerEntry(id, entryId) {
    await unwrap(
      api.DELETE('/api/v1/accounts/{account_id}/ledger/{entry_id}', {
        params: { path: { account_id: id, entry_id: entryId } },
      }),
    )
  },
}
