import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Filters } from '../filters'
import type { TradeDetail, TradesQuery } from './model'
import { useDataSource } from './source'

/** One query hook per read, keyed by everything that changes the answer, so a filter change refetches and a repeat is cached. */

export function useDashboard(filters: Filters) {
  const source = useDataSource()
  return useQuery({
    queryKey: ['dashboard', source.label, filters],
    queryFn: ({ signal }) => source.dashboard(filters, signal),
    placeholderData: keepPreviousData, // keep the old numbers on screen while a new range loads, instead of blanking
  })
}

export function useTrades(query: TradesQuery) {
  const source = useDataSource()
  return useQuery({
    queryKey: ['trades', source.label, query],
    queryFn: ({ signal }) => source.trades(query, signal),
    placeholderData: keepPreviousData,
  })
}

export function useFilterOptions() {
  const source = useDataSource()
  return useQuery({
    queryKey: ['filter-options', source.label],
    queryFn: ({ signal }) => source.filterOptions(signal),
    staleTime: 5 * 60_000,
  })
}

/** One trade. `null` data means there is no such trade for this user. */
export function useTrade(id: number) {
  const source = useDataSource()
  return useQuery({
    queryKey: ['trade', source.label, id],
    queryFn: ({ signal }) => source.trade(id, signal),
  })
}

export function useNeighbors(id: number, query: TradesQuery) {
  const source = useDataSource()
  return useQuery({
    queryKey: ['neighbors', source.label, id, query],
    queryFn: ({ signal }) => source.neighbors(id, query, signal),
    placeholderData: keepPreviousData, // the arrows keep their place while the next trade's neighbours load
  })
}

export function useTagCatalog() {
  const source = useDataSource()
  return useQuery({
    queryKey: ['tag-catalog', source.label],
    queryFn: ({ signal }) => source.tagCatalog(signal),
    staleTime: 5 * 60_000,
  })
}

/*
 * The journal writes. Each one refreshes what it changed: this trade's page, and the trade log (its tag, note and
 * image columns). Notes are the exception: the editor already shows what was typed, so the saved text goes straight
 * into the cache and nothing refetches under the person's cursor.
 */

export function useSaveNotes(id: number) {
  const source = useDataSource()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (html: string) => source.saveNotes(id, html),
    onSuccess: (_result, html) => {
      queryClient.setQueryData<TradeDetail | null>(['trade', source.label, id], (old) =>
        old ? { ...old, notes_html: html } : old,
      )
      return queryClient.invalidateQueries({ queryKey: ['trades'] })
    },
  })
}

function useJournalWrite<Variables, Result>(write: (variables: Variables) => Promise<Result>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: write,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['trade'] }),
        queryClient.invalidateQueries({ queryKey: ['trades'] }),
        queryClient.invalidateQueries({ queryKey: ['tag-catalog'] }),
      ]),
  })
}

export function useSetTradeTag(id: number) {
  const source = useDataSource()
  return useJournalWrite(({ tagId, on }: { tagId: number; on: boolean }) => source.setTradeTag(id, tagId, on))
}

/** Makes the tag if the group has none of that name, then puts it on the trade. */
export function useAddTag(id: number) {
  const source = useDataSource()
  return useJournalWrite(async ({ groupId, name }: { groupId: number; name: string }) => {
    const tag = await source.createTag(groupId, name)
    await source.setTradeTag(id, tag.id, true)
    return tag
  })
}

export function useAddScreenshots(id: number) {
  const source = useDataSource()
  return useJournalWrite((files: readonly File[]) => source.addScreenshots(id, files))
}

export function useDeleteScreenshot(id: number) {
  const source = useDataSource()
  return useJournalWrite((screenshotId: number) => source.deleteScreenshot(id, screenshotId))
}

/** The accounts a person can import into. */
export function useAccounts() {
  const source = useDataSource()
  return useQuery({
    queryKey: ['accounts', source.label],
    queryFn: ({ signal }) => source.accounts(signal),
    staleTime: 5 * 60_000,
  })
}

export function useImports() {
  const source = useDataSource()
  return useQuery({
    queryKey: ['imports', source.label],
    queryFn: ({ signal }) => source.imports(signal),
  })
}

/** Works out what a file would do, without saving anything. Each call replaces the last one's result. */
export function usePreviewImport() {
  const source = useDataSource()
  return useMutation({
    mutationFn: ({ accountId, file }: { accountId: number; file: File }) => source.previewImport(accountId, file),
  })
}

/** Saves the file's new pairs. Refreshes the history, and everything that reads trades. */
export function useCommitImport() {
  const source = useDataSource()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ accountId, file }: { accountId: number; file: File }) => source.commitImport(accountId, file),
    onSuccess: () =>
      Promise.all(
        ['imports', 'trades', 'trade', 'dashboard', 'filter-options'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      ),
  })
}

export function useUndoPreview(importId: number | null) {
  const source = useDataSource()
  return useQuery({
    queryKey: ['undo-preview', source.label, importId],
    queryFn: () => source.undoPreview(importId as number),
    enabled: importId !== null,
    gcTime: 0, // always ask again: what an undo would remove depends on what has happened since
  })
}

export function useUndoImport() {
  const source = useDataSource()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (importId: number) => source.undoImport(importId),
    onSuccess: () =>
      Promise.all(
        ['imports', 'trades', 'trade', 'dashboard', 'filter-options'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      ),
  })
}
