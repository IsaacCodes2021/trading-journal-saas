import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import { devSessionStub } from './dev/session-stub'

const API = 'http://localhost:8000'

export default defineConfig(({ mode }) => {
  const realApi = loadEnv(mode, process.cwd(), 'TJ_')['TJ_DEV_API'] === 'real'
  return {
    // The session stub is dev-server only (see dev/session-stub.ts); TJ_DEV_API=real turns it off.
    plugins: [react(), tailwindcss(), devSessionStub(!realApi)],
    server: {
      // Dev only: in production the API container serves the SPA itself, so the origin is the same (ADR-011).
      proxy: {
        // Against a real API the dev server signs the browser in: it sends the development session cookie, which
        // the API accepts only when started with TJ_DEV_USER_ID (backend/tradejournal/api/dev.py, never production).
        '/api': { target: API, headers: realApi ? { cookie: '__Host-tj_session=dev-session' } : {} },
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
