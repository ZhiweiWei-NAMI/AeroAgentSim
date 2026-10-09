import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
const browser=join(homedir(),'.cache/ms-playwright/chromium-1234/chrome-linux64/chrome');
process.env.NO_PROXY=process.env.no_proxy='127.0.0.1,localhost';
export default defineConfig({testDir:'./e2e',testMatch:'traffic-console.spec.ts',outputDir:'/tmp/aas-q/e2/playwright',workers:1,timeout:1_200_000,
 use:{screenshot:'only-on-failure',baseURL:'http://127.0.0.1:8002',viewport:{width:1440,height:1000},launchOptions:{executablePath:existsSync(browser)?browser:undefined,args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']}},
 webServer:{command:'cd .. && PYTHONPATH=src AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph AEROAGENTSIM_CONSOLE_URL=http://127.0.0.1:8002 /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python -m uvicorn tests.authoring.console_server:app --host 127.0.0.1 --port 8002',url:'http://127.0.0.1:8002/v1/runs',reuseExistingServer:false,timeout:120_000}});
