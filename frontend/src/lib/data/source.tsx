import { createContext, useContext, type ReactNode } from 'react'
import { defaultSource } from './defaultSource'
import type { DataSource } from './model'

/*
 * Pages read data through this context, never by importing a source directly. A build reads the real API (every call
 * is connected except screenshots, M6); `pnpm dev` keeps the sample journal unless started with `TJ_DEV_API=real`, so
 * screens can be tried with no backend. `dev/live-data.ts` has the rule and vite.config.ts applies it twice: it bakes it in
 * as `__TJ_LIVE_DATA__`, and in a live build it swaps `defaultSource` for the real API's, so the sample journal is not
 * imported by the build at all except for the demo mode, which loads it on demand (components/DataSourceRoot.tsx).
 */
const DataSourceContext = createContext<DataSource>(defaultSource)

export function DataSourceProvider({ source, children }: { source: DataSource; children: ReactNode }) {
  return <DataSourceContext.Provider value={source}>{children}</DataSourceContext.Provider>
}

export function useDataSource(): DataSource {
  return useContext(DataSourceContext)
}
