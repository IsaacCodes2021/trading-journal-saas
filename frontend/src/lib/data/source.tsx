import { createContext, useContext, type ReactNode } from 'react'
import { demoSource } from '../demo/source'
import { apiSource } from './apiSource'
import type { DataSource } from './model'

/*
 * Pages read data through this context, never by importing a source directly. The shipped default is the sample-data
 * source, until every screen's calls are connected (M3). `pnpm dev` with `TJ_DEV_API=real` (vite.config.ts defines
 * `__TJ_LIVE_DATA__`) reads the real API instead, so a screen can be tried against a real database as its calls
 * arrive. Tests wrap the tree in their own provider.
 */
const DataSourceContext = createContext<DataSource>(__TJ_LIVE_DATA__ ? apiSource : demoSource)

export function DataSourceProvider({ source, children }: { source: DataSource; children: ReactNode }) {
  return <DataSourceContext.Provider value={source}>{children}</DataSourceContext.Provider>
}

export function useDataSource(): DataSource {
  return useContext(DataSourceContext)
}
