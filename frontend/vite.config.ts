import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import { devSessionStub } from './dev/session-stub'

const API = 'http://localhost:8000'

export default defineConfig(({ mode }) => ({
  // The session stub is dev-server only (see dev/session-stub.ts); TJ_DEV_API=real turns it off.
  plugins: [react(), tailwindcss(), devSessionStub(loadEnv(mode, process.cwd(), 'TJ_')['TJ_DEV_API'] !== 'real')],
  server: {
    // Dev only: in production the API container serves the SPA itself, so the origin is the same (ADR-011).
    proxy: { '/api': API, '/auth': API, '/files': API },
  },
  build: { sourcemap: false },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: false,
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
}))
