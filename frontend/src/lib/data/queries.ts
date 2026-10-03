import { keepPreviousData, useQuery } from '@tanstack/react-query'
import type { Filters } from '../filters'
import type { TradesQuery } from './model'
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
