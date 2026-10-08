import { defineConfig } from '@playwright/test';
import { existsSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

// The managed session's HTTP proxy must not intercept the local test server.
const bypass = [process.env.no_proxy, process.env.NO_PROXY, '127.0.0.1', 'localhost'].filter(Boolean).join(',');
process.env.NO_PROXY = process.env.no_proxy = bypass;
mkdirSync('.tmp/config', { recursive: true });
// Keep artifacts in frontend while avoiding Linux's 108-byte socket-path limit.
if (process.platform === 'linux') process.env.TMPDIR = '/proc/self/cwd/.tmp';
process.env.XDG_CONFIG_HOME = join(process.cwd(), '.tmp/config');

const cached = join(homedir(), '.cache/ms-playwright/chromium-1234/chrome-linux/chrome');
const cachedNew = join(homedir(), '.cache/ms-playwright/chromium-1234/chrome-linux64/chrome');
const shell = join(homedir(), '.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? (existsSync(shell) ? shell : existsSync(cached) ? cached : existsSync(cachedNew) ? cachedNew : undefined);
export default defineConfig({
  testDir: './e2e', outputDir: './test-results/artifacts', workers: 1, timeout: 90_000,
  use: { baseURL: 'http://127.0.0.1:4179', viewport: { width: 1440, height: 900 },
    launchOptions: { executablePath, args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] } },
  webServer: { command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4179 --strictPort', url: 'http://127.0.0.1:4179', reuseExistingServer: false, timeout: 180_000 },
});
