import { defineConfig } from '@playwright/test';
const baseURL=process.env.AEROAGENTSIM_STUDIO_URL;
if(!baseURL)throw Error('Set AEROAGENTSIM_STUDIO_URL to the real backend serving the built console.');
process.env.NO_PROXY=process.env.no_proxy='127.0.0.1,localhost';
export default defineConfig({testDir:'./e2e',testMatch:'studio-guided.spec.ts',workers:1,timeout:240_000,outputDir:process.env.AEROAGENTSIM_PLAYWRIGHT_OUTPUT ?? 'test-results/studio',use:{baseURL,actionTimeout:15_000,viewport:{width:1280,height:1000},launchOptions:{...(process.env.AEROAGENTSIM_CHROMIUM ? {executablePath:process.env.AEROAGENTSIM_CHROMIUM} : {}),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']}}});
