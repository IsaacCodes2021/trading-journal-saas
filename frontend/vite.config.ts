import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import { devApi, liveData } from './dev/live-data'
import { devSessionStub } from './dev/session-stub'

const API = 'http://localhost:8000'

export default defineConfig(({ mode, command }) => {
  const api = devApi(loadEnv(mode, process.cwd(), 'TJ_')['TJ_DEV_API'])
  const realApi = api !== 'stub'
  return {
    // Pages read the real API (true) or the sample journal (false); see dev/live-data.ts for the rule.
    define: { __TJ_LIVE_DATA__: JSON.stringify(liveData(command, mode, realApi)) },
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
    build: { sourcemap: false },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test-setup.ts'],
      globals: false,
      exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
    },
  }
})
