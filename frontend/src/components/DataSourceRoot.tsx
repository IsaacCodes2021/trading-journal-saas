import { useEffect, useState, type ReactNode } from 'react'
import { DataSourceProvider } from '../lib/data/source'
import type { DataSource } from '../lib/data/model'
import { exitDemo, isDemo } from '../lib/demoMode'
import { Button, Card, Spinner } from './ui/primitives'

/** The sample journal, fetched only when demo mode asks for it (its own chunk: see `scripts/check-bundle.mjs`). */
const loadSampleJournal = () => import('../lib/demo/source').then((m) => m.demoSource)

/**
 * Chooses where every page reads from. A build reads the real API; if the person asked for the demo before this page was
 * loaded (a switch kept in this tab only), the sample journal is loaded and used instead until they leave it. In `pnpm dev`
 * and in tests the sample journal is already the default (`__TJ_LIVE_DATA__` is false), so there is nothing to choose and
 * this passes through.
 */
export function DataSourceRoot({
  children,
  live = __TJ_LIVE_DATA__,
  demo = isDemo(),
  load = loadSampleJournal,
}: {
  children: ReactNode
  live?: boolean
  demo?: boolean
  load?: () => Promise<DataSource>
}) {
  const wanted = live && demo
  const [sample, setSample] = useState<DataSource | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!wanted) return
    let current = true
    load().then(
      (source) => current && setSample(source),
      () => current && setFailed(true),
    )
    return () => {
      current = false
    }
  }, [wanted, load])

  if (!wanted) return <>{children}</>
  if (failed)
    return (
      <Card className="m-8 flex flex-col items-start gap-3 p-6" role="alert">
        <p className="m-0">The sample data could not be loaded.</p>
        <Button onClick={exitDemo}>Go back to my own data</Button>
      </Card>
    )
  if (!sample) return <Spinner label="Loading the sample data" />
  return <DataSourceProvider source={sample}>{children}</DataSourceProvider>
}
