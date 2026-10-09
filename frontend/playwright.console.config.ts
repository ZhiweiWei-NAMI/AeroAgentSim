import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
const browser=join(homedir(),'.cache/ms-playwright/chromium-1234/chrome-linux64/chrome');
// Allow isolated kernel worktrees to shadow the shared editable installation.
const pythonPath=process.env.PYTHONPATH ?? 'src';
const quotedPythonPath="'"+pythonPath.replace(/'/g, "'\\''")+"'";
const port=process.env.AEROAGENTSIM_CONSOLE_PORT ?? '8002';
if (!/^\d+$/.test(port) || Number(port)<1 || Number(port)>65535) throw Error('Invalid console port');
const consoleUrl=`http://127.0.0.1:${port}`;
process.env.NO_PROXY=process.env.no_proxy='127.0.0.1,localhost';
export default defineConfig({testDir:'./e2e',testMatch:'traffic-console.spec.ts',outputDir:process.env.AEROAGENTSIM_CONSOLE_OUTPUT ?? '/tmp/aas-q/e3g/console-playwright',workers:1,timeout:1_200_000,
 use:{screenshot:'only-on-failure',baseURL:consoleUrl,viewport:{width:1280,height:800},launchOptions:{executablePath:process.env.AEROAGENTSIM_CHROMIUM ?? (existsSync(browser)?browser:undefined),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']}},
 webServer:{command:`cd .. && PYTHONPATH=${quotedPythonPath} AEROAGENTSIM_CONSOLE_URL=${consoleUrl} /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python -m uvicorn tests.authoring.console_server:app --host 127.0.0.1 --port ${port}`,url:`${consoleUrl}/v1/runs`,reuseExistingServer:false,timeout:120_000}});
