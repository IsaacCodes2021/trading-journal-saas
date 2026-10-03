// Pre-launch gates from docs/design/04-security.md section 4 that a script can enforce. Pure functions over
// { path: text } maps, so they are unit-tested (security-checks.test.mjs) without touching the disk.

// Anything that turns a string into live markup or code. The strict CSP blocks eval at run time; failing the build is
// earlier and clearer. Raw HTML goes through SafeHtml (DOMPurify) and nowhere else.
const SINKS =
  /dangerouslySetInnerHTML|\.innerHTML\b|\.outerHTML\b|insertAdjacentHTML|document\.write\b|\beval\(|new Function\(/

// Where a sink is allowed. Test files are not bundled, and may read `innerHTML` to assert on markup.
const SINK_ALLOWED = (path) => path === 'src/components/SafeHtml.tsx' || /\.test\.[cm]?[jt]sx?$/.test(path)

// Vite inlines import.meta.env.VITE_* into the public bundle at build time, so it can hold nothing secret. The app
// has no use for it today; the first one needs a deliberate edit here.
const BUILD_ENV = /import\.meta\.env\.VITE_|process\.env/

// Credential shapes that must never be in what we ship (gitleaks scans the repository; this scans the build).
const SECRET_SHAPES = [
  [/-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/, 'a private key'],
  [/\bAKIA[0-9A-Z]{16}\b/, 'an AWS access key id'],
  [/\bgh[pousr]_[A-Za-z0-9]{36,}\b/, 'a GitHub token'],
  [/\b[sr]k_live_[A-Za-z0-9]{16,}\b/, 'a live payment key'],
  [/\bxox[abprs]-[A-Za-z0-9-]{10,}\b/, 'a Slack token'],
]

const SOURCE_FILE = /\.(?:[cm]?[jt]sx?|html)$/

function lines(text) {
  return text.split('\n').map((content, index) => ({ number: index + 1, content }))
}

/** @param {Record<string, string>} files source files by repo-relative path, e.g. `src/main.tsx` */
export function scanSource(files) {
  const found = []
  for (const [path, text] of Object.entries(files)) {
    if (!SOURCE_FILE.test(path)) continue
    for (const { number, content } of lines(text)) {
      if (SINKS.test(content) && !SINK_ALLOWED(path)) {
        found.push(`${path}:${number}: raw HTML or code injection is only allowed in src/components/SafeHtml.tsx`)
      }
      if (BUILD_ENV.test(content) && !/\.test\.[cm]?[jt]sx?$/.test(path)) {
        found.push(`${path}:${number}: build-time environment values end up in the public bundle; none are allowed`)
      }
    }
  }
  return found
}

/** @param {string[]} names file names directly in the front-end folder, e.g. `.env.local` */
export function scanEnvFiles(names) {
  return names
    .filter((name) => name.startsWith('.env') && name !== '.env.example')
    .map((name) => `${name}: Vite loads .env files into the bundle; keep only .env.example`)
}

/** @param {Record<string, string>} files built files by path relative to dist/, e.g. `assets/index-abc.js` */
export function scanDist(files) {
  const found = []
  for (const [path, text] of Object.entries(files)) {
    if (path.endsWith('.map')) found.push(`dist/${path}: source maps must not be built or served`)
    if (/sourceMappingURL=/.test(text)) found.push(`dist/${path}: refers to a source map`)
    for (const [shape, what] of SECRET_SHAPES) {
      if (shape.test(text)) found.push(`dist/${path}: contains what looks like ${what}`)
    }
  }
  return found
}
