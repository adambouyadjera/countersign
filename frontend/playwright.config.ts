import { defineConfig, devices } from '@playwright/test'

// E2E runs against its own dev server in mock mode (fixture replay), on a port
// that `npm run dev` never uses, so a running real-mode server is never reused.
const E2E_PORT = 5174

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${E2E_PORT}`,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npx vite --port ${E2E_PORT} --strictPort`,
    url: `http://localhost:${E2E_PORT}`,
    env: { VITE_USE_MOCK: '1' },
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
