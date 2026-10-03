import { createContext, useContext, type ReactNode } from 'react'
import { demoSource } from '../demo/source'
import type { DataSource } from './model'

/*
 * Pages read data through this context, never by importing a source directly. Today the default is the sample-data
 * source; when the stats and trades endpoints exist (M3) the default becomes the API source and nothing else changes.
 * Tests wrap the tree in their own provider.
 */
const DataSourceContext = createContext<DataSource>(demoSource)

export function DataSourceProvider({ source, children }: { source: DataSource; children: ReactNode }) {
  return <DataSourceContext.Provider value={source}>{children}</DataSourceContext.Provider>
}

export function useDataSource(): DataSource {
  return useContext(DataSourceContext)
}
