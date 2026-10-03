// Runs the security gates in security-checks.mjs against the real tree.
//   pnpm check:security                 source scan (no build needed); also scans dist/ if it exists
//   pnpm check:security --require-dist  CI: the build must exist, so the dist scan cannot be silently skipped
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { scanDist, scanEnvFiles, scanSource } from './security-checks.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))

function readTree(dir) {
  const files = {}
  const walk = (current) => {
    for (const name of readdirSync(current)) {
      const full = join(current, name)
      if (statSync(full).isDirectory()) walk(full)
      else files[relative(dir, full).split(sep).join('/')] = readFileSync(full, 'utf8')
    }
  }
  if (existsSync(dir)) walk(dir)
  return files
}

const source = Object.fromEntries(
  Object.entries(readTree(join(root, 'src'))).map(([path, text]) => [`src/${path}`, text]),
)
source['index.html'] = readFileSync(join(root, 'index.html'), 'utf8')

const problems = [...scanSource(source), ...scanEnvFiles(readdirSync(root))]

const dist = join(root, 'dist')
if (existsSync(dist)) {
  problems.push(...scanDist(readTree(dist)))
} else if (process.argv.includes('--require-dist')) {
  problems.push('dist/ does not exist: run the build before this check')
}

if (problems.length > 0) {
  console.error(problems.map((problem) => `check-security: ${problem}`).join('\n'))
  process.exit(1)
}
console.log(
  `check-security: ${Object.keys(source).length} source files${existsSync(dist) ? ' and the build' : ''} clean`,
)
