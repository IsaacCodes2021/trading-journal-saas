import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const API = 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Dev only: in production the API container serves the SPA itself, so the origin is the same.
    proxy: { '/api': API, '/auth': API, '/fake-idp': API, '/files': API },
  },
  build: { sourcemap: false },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: false,
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'], // Playwright specs run separately
  },
})
