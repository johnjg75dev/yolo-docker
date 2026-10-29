import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:8889', ...(process.env.CHROMIUM_EXECUTABLE ? { launchOptions: { executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] } } : {}) },
  webServer: [
    { command: 'cd .. && PYTHONPATH=. .venv/bin/python frontend/e2e/server.py', port: 8889, reuseExistingServer: false, timeout: 30000 },
  ],
});
