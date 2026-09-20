import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  use: {
    baseURL: 'http://127.0.0.1:8780',
    viewport: { width: 1280, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `${process.env.FEM_TEST_PYTHON || 'python'} -m app.demo --port 8780`,
    url: 'http://127.0.0.1:8780/health',
    reuseExistingServer: false,
  },
});
