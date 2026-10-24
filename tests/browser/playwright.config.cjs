const {defineConfig} = require('playwright/test');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '*.spec.cjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 45000,
  expect: {timeout: 10000},
  outputDir: 'test-results',
  reporter: [['list'], ['html', {open: 'never'}]],
  use: {
    baseURL: 'http://127.0.0.1:8029',
    browserName: 'chromium',
    viewport: {width: 1440, height: 1000},
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Optional local installed Chromium; CI uses the version installed by the
    // locked Playwright package. No developer-specific path is checked in.
    launchOptions: process.env.CRUCIBLE_BROWSER_EXECUTABLE ? {executablePath: process.env.CRUCIBLE_BROWSER_EXECUTABLE} : {},
  },
  webServer: {
    command: '"' + (process.env.CRUCIBLE_BROWSER_PYTHON || 'python') + '" -B server.py',
    cwd: __dirname,
    url: 'http://127.0.0.1:8029/health',
    reuseExistingServer: false,
    // Uvicorn handles SIGINT and returns through the temporary DB cleanup.
    gracefulShutdown: {signal: 'SIGINT', timeout: 5000},
    timeout: 60000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
