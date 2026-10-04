import { createContext, useContext, type ReactNode } from 'react'
import { demoSource } from '../demo/source'
import { apiSource } from './apiSource'
import type { DataSource } from './model'

/*
 * Pages read data through this context, never by importing a source directly. A build reads the real API (every call
 * is connected except screenshots, M6); `pnpm dev` keeps the sample journal unless started with `TJ_DEV_API=real`, so
 * screens can be tried with no backend. `dev/live-data.ts` has the rule and vite.config.ts bakes it in as
 * `__TJ_LIVE_DATA__`, which also drops the sample journal from a build. Tests wrap the tree in their own provider.
 */
const DataSourceContext = createContext<DataSource>(__TJ_LIVE_DATA__ ? apiSource : demoSource)

export function DataSourceProvider({ source, children }: { source: DataSource; children: ReactNode }) {
  return <DataSourceContext.Provider value={source}>{children}</DataSourceContext.Provider>
}

export function useDataSource(): DataSource {
  return useContext(DataSourceContext)
}
