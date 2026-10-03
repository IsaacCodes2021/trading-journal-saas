// Fails the build when the entry chunk outgrows its budget (docs/design/01-requirements.md: 150 KB gzipped).
// The chart and the editor must be lazily loaded so they stay out of it (spike S1: unsplit, 422 KB gzipped).
import { readdirSync, readFileSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

const ENTRY_BUDGET_KB = 150
const dist = new URL('../dist/', import.meta.url)

const html = readFileSync(new URL('index.html', dist), 'utf8')
const entry = /<script[^>]+src="\/assets\/([^"]+\.js)"/.exec(html)?.[1]
if (!entry) {
  console.error('check-bundle: no entry script found in dist/index.html (run the build first)')
  process.exit(1)
}

const chunks = readdirSync(new URL('assets/', dist))
  .filter((name) => name.endsWith('.js'))
  .map((name) => ({ name, gzipKb: gzipSync(readFileSync(new URL(`assets/${name}`, dist))).length / 1024 }))
  .sort((a, b) => b.gzipKb - a.gzipKb)

for (const chunk of chunks) {
  console.log(`${chunk.name === entry ? '* ' : '  '}${chunk.name.padEnd(40)} ${chunk.gzipKb.toFixed(1)} KB gzipped`)
}

const entryKb = chunks.find((chunk) => chunk.name === entry)?.gzipKb ?? 0
if (entryKb > ENTRY_BUDGET_KB) {
  console.error(`check-bundle: entry chunk is ${entryKb.toFixed(1)} KB gzipped, over the ${ENTRY_BUDGET_KB} KB budget`)
  process.exit(1)
}
console.log(`check-bundle: entry chunk ${entryKb.toFixed(1)} KB gzipped, within the ${ENTRY_BUDGET_KB} KB budget`)
