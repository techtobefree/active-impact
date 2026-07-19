// Active Impact end-to-end (browser) test config.
// Point BASE_URL at a running instance (default: the app's canonical :8000).
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  fullyParallel: false, // flows are stateful; serial keeps screenshots coherent
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 7_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8000',
    viewport: { width: 390, height: 844 }, // phone-sized — mobile-first PWA
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    // Use the system Google Chrome (no browser download needed here); drop
    // `channel` to use Playwright's bundled chromium (npx playwright install chromium).
    channel: 'chrome',
    launchOptions: { args: ['--no-sandbox', '--disable-dev-shm-usage'] },
  },
});
