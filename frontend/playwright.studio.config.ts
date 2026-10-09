import { defineConfig } from '@playwright/test';
import { join } from 'node:path';
import { homedir } from 'node:os';
const baseURL=process.env.AEROAGENTSIM_STUDIO_URL;
if(!baseURL)throw Error('Set AEROAGENTSIM_STUDIO_URL to the real backend serving the built console.');
process.env.NO_PROXY=process.env.no_proxy='127.0.0.1,localhost';
export default defineConfig({testDir:'./e2e',testMatch:'studio-guided.spec.ts',workers:1,timeout:240_000,outputDir:process.env.AEROAGENTSIM_PLAYWRIGHT_OUTPUT ?? '/tmp/aas-q/e3f/playwright',use:{baseURL,actionTimeout:15_000,viewport:{width:1280,height:1000},launchOptions:{executablePath:process.env.AEROAGENTSIM_CHROMIUM ?? join(homedir(),'.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell'),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']}}});
