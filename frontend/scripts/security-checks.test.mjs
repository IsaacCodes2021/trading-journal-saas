import { expect, it } from 'vitest'
import { scanDist, scanEnvFiles, scanSource } from './security-checks.mjs'

const hit = (files) => scanSource(files).map((line) => line.split(': ')[0])

it('allows raw HTML in SafeHtml only', () => {
  const code = 'return <div dangerouslySetInnerHTML={{ __html: html }} />'
  expect(hit({ 'src/components/SafeHtml.tsx': code })).toEqual([])
  expect(hit({ 'src/features/trades/Notes.tsx': code })).toEqual(['src/features/trades/Notes.tsx:1'])
})

it.each([
  'node.innerHTML = value',
  'node.outerHTML = value',
  "el.insertAdjacentHTML('beforeend', value)",
  'document.write(value)',
  'eval(value)',
  'new Function(value)',
])('rejects %s', (code) => {
  expect(hit({ 'src/lib/x.ts': `const a = 1\n${code}\n` })).toEqual(['src/lib/x.ts:2'])
})

it('does not mistake look-alikes for sinks', () => {
  expect(hit({ 'src/lib/x.ts': 'const evaluate = 1; const innerHtmlLength = 2; // retrieval(' })).toEqual([])
})

it('lets tests read markup, since they are not bundled', () => {
  expect(hit({ 'src/x.test.tsx': 'expect(container.innerHTML).toContain("a")' })).toEqual([])
})

it('refuses build-time environment values in the app but not in tests', () => {
  expect(hit({ 'src/lib/api.ts': 'const key = import.meta.env.VITE_API_KEY' })).toEqual(['src/lib/api.ts:1'])
  expect(hit({ 'src/lib/api.ts': 'const x = process.env.SECRET' })).toEqual(['src/lib/api.ts:1'])
  expect(hit({ 'src/x.test.ts': 'import.meta.env.VITE_X' })).toEqual([])
  expect(hit({ 'src/lib/api.ts': 'const dev = import.meta.env.DEV' })).toEqual([])
})

it('looks at html and js files and ignores the rest', () => {
  expect(hit({ 'index.html': '<script>document.write(1)</script>' })).toEqual(['index.html:1'])
  expect(hit({ 'src/styles.css': '/* eval( */' })).toEqual([])
})

it('flags .env files except the template', () => {
  expect(scanEnvFiles(['.env', '.env.local', '.env.production', '.env.example', 'package.json'])).toHaveLength(3)
})

// Fake credentials are assembled at run time, so this file does not itself look like a leak to the secret scanner
// (which reads the whole git history, so a literal here could never be cleaned up afterwards).
const FAKE_SECRETS = [
  ['-----BEGIN', 'PRIVATE KEY-----'].join(' '),
  'AKIA' + 'ABCDEFGHIJKLMNOP',
  'ghp_' + 'a'.repeat(36),
  'sk_' + 'live_' + 'abcdefghijklmnop1234',
]

it('flags source maps in the build', () => {
  expect(scanDist({ 'assets/index-abc.js': 'console.log(1)' })).toEqual([])
  expect(scanDist({ 'assets/index-abc.js.map': '{}' })).toHaveLength(1)
  expect(scanDist({ 'assets/index-abc.js': 'x\n//# sourceMappingURL=index-abc.js.map' })).toHaveLength(1)
})

it.each(FAKE_SECRETS)('flags a secret-shaped string in the build (%#)', (secret) => {
  expect(scanDist({ 'assets/a.js': `const k="${secret}"` })).toHaveLength(1)
})
