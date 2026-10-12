import {defineConfig} from '@playwright/test';
import {join} from 'node:path';
process.env.NO_PROXY='127.0.0.1,localhost';process.env.no_proxy=process.env.NO_PROXY;
// Browser executable: only the AEROAGENTSIM_CHROMIUM environment variable may
// select it. The record-docs-media script (tools/docs) resolves Playwright's own Chromium
// before falling back to a scratch browsers path; no home-directory or pinned
// revision default is baked in here.
const executablePath=process.env.AEROAGENTSIM_CHROMIUM;
const workDir=process.env.AEROAGENTSIM_DOCS_WORKDIR;
if(!workDir)throw Error('Set AEROAGENTSIM_DOCS_WORKDIR to the recording scratch directory.');
export default defineConfig({
 testDir:'./e2e/flows',workers:1,retries:0,timeout:1_200_000,
 expect:{timeout:30_000},
 outputDir:join(workDir,'playwright'),
 use:{
  baseURL:process.env.AEROAGENTSIM_DOCS_URL,actionTimeout:15_000,
  viewport:{width:1280,height:800},video:{mode:'on',size:{width:1280,height:800}},
  screenshot:'only-on-failure',
  launchOptions:{...(executablePath?{executablePath}:{}),args:['--use-gl=swiftshader','--enable-unsafe-swiftshader','--no-proxy-server']},
 },
});
