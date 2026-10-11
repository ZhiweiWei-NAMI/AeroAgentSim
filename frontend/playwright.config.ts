import { defineConfig } from '@playwright/test';
import { existsSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

// Console tests need the real API as well as the built frontend. All test
// artifacts and browser profiles live outside the worktree.
const scratch = process.env.AEROAGENTSIM_E2E_ROOT ?? '/tmp/aas-q/s2/e2e';
mkdirSync(join(scratch, 'config'), { recursive: true });
process.env.AEROAGENTSIM_E2E_ROOT = scratch;
process.env.TMPDIR = scratch;
process.env.XDG_CONFIG_HOME = join(scratch, 'config');
const bypass = [process.env.no_proxy, process.env.NO_PROXY, '127.0.0.1', 'localhost'].filter(Boolean).join(',');
process.env.NO_PROXY = process.env.no_proxy = bypass;
const shell = join(homedir(), '.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell');
const cached = join(homedir(), '.cache/ms-playwright/chromium-1234/chrome-linux/chrome');
const cachedNew = join(homedir(), '.cache/ms-playwright/chromium-1234/chrome-linux64/chrome');
const executablePath = process.env.AEROAGENTSIM_CHROMIUM ?? process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? (existsSync(shell) ? shell : existsSync(cached) ? cached : existsSync(cachedNew) ? cachedNew : undefined);
const port = process.env.AEROAGENTSIM_E2E_PORT ?? '4179';
if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) throw Error('Invalid console e2e port');
const baseURL = `http://127.0.0.1:${port}`;
const quote = (value: string) => "'" + value.replace(/'/g, "'\\''") + "'";
const python = process.env.AEROAGENTSIM_PYTHON ?? resolve('..', '.venv/bin/python');
const pythonPath = process.env.PYTHONPATH ?? 'aerokernel:src';
export default defineConfig({
  testDir: './e2e', testIgnore: '**/flows/**', outputDir: join(scratch, 'artifacts'),
  workers: 1, timeout: 240_000, reporter: 'list',
  use: { baseURL, viewport: { width: 1440, height: 900 },
    launchOptions: { executablePath, args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] } },
  webServer: {
    // Builds into the scratch directory: frontend/dist may be serving someone's console.
    command: `${process.env.AEROAGENTSIM_E2E_SKIP_BUILD === '1' ? '' : `npx vite build --outDir ${quote(join(scratch, 'dist'))} --emptyOutDir && `}cd .. && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=${quote(pythonPath)} AEROAGENTSIM_CHROMIUM=${quote(executablePath ?? '')} AEROAGENTSIM_CONSOLE_URL=${quote(baseURL)} ${quote(python)} -m aeroagentsim.services.cli serve --out ${quote(join(scratch, 'runs'))} --frontend ${quote(join(scratch, 'dist'))} --port ${port}`,
    url: `${baseURL}/v1/templates`, reuseExistingServer: false, timeout: 180_000,
  },
});
