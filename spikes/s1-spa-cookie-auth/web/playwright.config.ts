import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1, // the toy API keeps its data in memory, so tests run one at a time
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:8000',
    channel: 'msedge', // the Edge already installed on this machine: no browser download
    headless: true,
  },
  webServer: {
    // Windows dev machine paths. Starts the API (which also serves the built SPA) unless it is already running.
    command: '..\\..\\..\\backend\\.venv\\Scripts\\python.exe -m uvicorn main:app --port 8000 --log-level warning',
    cwd: '../api',
    url: 'http://localhost:8000/healthz',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
