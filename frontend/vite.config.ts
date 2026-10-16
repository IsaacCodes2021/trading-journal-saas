import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import { devApi, liveData } from './dev/live-data'
import { devSessionStub } from './dev/session-stub'

const API = 'http://localhost:8000'

export default defineConfig(({ mode, command }) => {
  const api = devApi(loadEnv(mode, process.cwd(), 'TJ_')['TJ_DEV_API'])
  const realApi = api !== 'stub'
  const live = liveData(command, mode, realApi)
  return {
    // Pages read the real API (true) or the sample journal (false); see dev/live-data.ts for the rule.
    define: { __TJ_LIVE_DATA__: JSON.stringify(live) },
    // Live: the default source is the real API's, so nothing in the build imports the sample journal statically.
    resolve: {
      alias: live
        ? [
            {
              find: './defaultSource',
              replacement: fileURLToPath(new URL('./src/lib/data/defaultSource.live.ts', import.meta.url)),
            },
          ]
        : [],
    },
    // The session stub is dev-server only (see dev/session-stub.ts); TJ_DEV_API=real or signin turns it off.
    plugins: [react(), tailwindcss(), devSessionStub(!realApi)],
    server: {
      // Dev only: in production the API container serves the SPA itself, so the origin is the same (ADR-011).
      proxy: {
        // With TJ_DEV_API=real the dev server signs the browser in: it sends the development session cookie, which
        // the API accepts only when started with TJ_DEV_USER_ID (backend/tradejournal/api/dev.py, never production).
        // With TJ_DEV_API=signin nothing is added: the browser signs in through Kinde and keeps its own real session.
        '/api': { target: API, headers: api === 'dev-user' ? { cookie: '__Host-tj_session=dev-session' } : {} },
        '/auth': API,
        '/files': API,
      },
    },
    build: {
      sourcemap: false,
      // Two pages: the app, and the payment page (pay.html), which alone loads Paddle's script under its own
      // Content-Security-Policy (backend/tradejournal/api/routers/pay.py). They share no chunk with each other's code.
      rollupOptions: {
        input: {
          main: fileURLToPath(new URL('./index.html', import.meta.url)),
          pay: fileURLToPath(new URL('./pay.html', import.meta.url)),
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test-setup.ts'],
      globals: false,
      exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
    },
  }
})
