import {defineConfig} from '@playwright/test';
import {join} from 'node:path';
import {homedir} from 'node:os';
process.env.NO_PROXY='127.0.0.1,localhost';process.env.no_proxy=process.env.NO_PROXY;
export default defineConfig({
 testDir:'./e2e/flows',workers:1,retries:0,timeout:600_000,
 outputDir:join(process.env.AEROAGENTSIM_DOCS_WORKDIR??'/tmp/aas-q/e3e','playwright'),
 use:{
  baseURL:process.env.AEROAGENTSIM_DOCS_URL,actionTimeout:15_000,
  viewport:{width:1280,height:800},video:{mode:'on',size:{width:1280,height:800}},
  screenshot:'only-on-failure',
  launchOptions:{executablePath:process.env.AEROAGENTSIM_CHROMIUM??join(homedir(),'.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell'),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']},
 },
});
