import { defineConfig } from '@playwright/test'
const external = process.env.E2E_BASE_URL  // set to test an already-running deployment (e.g. nginx) instead of starting dev servers
export default defineConfig({
  testDir: './e2e', timeout: 60_000, retries: 0, reporter: 'list',
  use: { baseURL: external ?? 'http://localhost:5173', trace: 'retain-on-failure', launchOptions: { executablePath: process.env.PW_CHROMIUM_PATH } }, // PW_CHROMIUM_PATH: optional, to reuse a preinstalled Chromium
  webServer: external ? undefined : [
    { command: 'rm -f e2e.db && ROBOTOPS_ENV=dev DATABASE_URL=sqlite:///./e2e.db python -m uvicorn app.main:app --port 8000', cwd: '../backend', url: 'http://localhost:8000/healthz', reuseExistingServer: false, timeout: 60_000 },
    { command: 'npm run dev -- --port 5173 --strictPort', url: 'http://localhost:5173', reuseExistingServer: false, timeout: 60_000 },
  ],
})
