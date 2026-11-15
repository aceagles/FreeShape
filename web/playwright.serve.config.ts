import { defineConfig, devices } from '@playwright/test'

// The specs that run against `python -m freeshape serve`: web/e2e/s<n>_*.spec.ts (scripts/web/e2e.sh sets
// FS_BASE_URL to the server it started). WebKit gates, with a 1024 x 768 touch viewport; Chromium is an optional
// second pass (PW_CHROMIUM=1) that is reported and never gates. The static checks in e2e/static use playwright.config.ts.
const base = { viewport: { width: 1024, height: 768 }, hasTouch: true }

export default defineConfig({
  testDir: 'e2e',
  testMatch: /\/s\d+_[^/]*\.spec\.ts$/,
  // The server holds one Document; specs share it, so they run one after another.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: 'list',
  outputDir: 'test-results/serve',
  use: { baseURL: process.env.FS_BASE_URL ?? 'http://127.0.0.1:8765', trace: 'retain-on-failure' },
  projects: [
    { name: 'webkit', use: { ...devices['Desktop Safari'], ...base } },
    ...(process.env.PW_CHROMIUM ? [{ name: 'chromium', use: { ...devices['Desktop Chrome'], ...base } }] : []),
  ],
})
