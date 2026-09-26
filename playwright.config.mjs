import { defineConfig } from '@playwright/test';

// Serves the repository so the harness can load the real panel module and preview bundle.
export default defineConfig({
  testDir: 'tests/frontend',
  timeout: 60_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    browserName: 'chromium',
    viewport: { width: 1280, height: 860 },
  },
  webServer: {
    command: 'python3 -m http.server 8765 --bind 127.0.0.1',
    url: 'http://127.0.0.1:8765/tests/frontend/harness.html',
    reuseExistingServer: !process.env.CI,
  },
});
