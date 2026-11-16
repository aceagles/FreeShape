import { defineConfig, devices } from '@playwright/test'
import { modeState } from './e2e/support/mode.ts'

// e2e:static: checks that need no `freeshape serve`: the built client, rendered by `vite preview`.
// WebKit is the gating engine (every browser on the iPad is WebKit); Chromium is optional: PW_CHROMIUM=1.
// Desktop Safari reports a fine pointer with hover, which would open every page in Desktop mode: these checks are of the Tablet layout, so seed Tablet mode.
const tablet = { storageState: modeState('http://127.0.0.1:4173', 'tablet') }

export default defineConfig({
  testDir: 'e2e/static',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: 'http://127.0.0.1:4173', trace: 'retain-on-failure' },
  projects: [
    { name: 'webkit', use: { ...devices['Desktop Safari'], viewport: { width: 1024, height: 768 }, ...tablet } },
    ...(process.env.PW_CHROMIUM
      ? [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 768 }, ...tablet } }]
      : []),
  ],
  webServer: {
    command: 'npm run build && npx vite preview --host 127.0.0.1',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
