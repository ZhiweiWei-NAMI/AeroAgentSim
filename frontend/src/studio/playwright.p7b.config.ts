import { defineConfig } from '@playwright/test';
import { existsSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
const bypass = [process.env.no_proxy, process.env.NO_PROXY, '127.0.0.1', 'localhost'].filter(Boolean).join(',');
process.env.NO_PROXY = process.env.no_proxy = bypass;
mkdirSync('src/studio/.tmp/config', { recursive: true });
process.env.TMPDIR = '/proc/self/cwd/src/studio/.tmp';
process.env.XDG_CONFIG_HOME = join(process.cwd(), 'src/studio/.tmp/config');
const shell = join(homedir(), '.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell');
const chrome = join(homedir(), '.cache/ms-playwright/chromium-1234/chrome-linux64/chrome');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? (existsSync(shell) ? shell : existsSync(chrome) ? chrome : undefined);
export default defineConfig({
  testDir: '.', testMatch: 'p7b.e2e.ts', outputDir: '.e2e-results', workers: 1, timeout: 120_000,
  use: { baseURL: 'http://127.0.0.1:4179', viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath, args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] } },
  webServer: [
    { cwd: process.cwd(), command: 'cd .. && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.venv/lib/python3.11/site-packages:tests/authoring /mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8017', url: 'http://127.0.0.1:8017/v1/studio/catalog', reuseExistingServer: false, timeout: 60_000 },
    { cwd: process.cwd(), command: 'npm run preview -- --host 127.0.0.1 --port 4179 --strictPort --outDir src/studio/.build', url: 'http://127.0.0.1:4179', reuseExistingServer: false, timeout: 60_000 },
  ],
});
