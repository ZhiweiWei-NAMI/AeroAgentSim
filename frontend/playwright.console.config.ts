import { defineConfig } from '@playwright/test';
// Allow isolated kernel worktrees to shadow the shared editable installation.
const pythonPath=process.env.PYTHONPATH ?? 'src';
const quotedPythonPath="'"+pythonPath.replace(/'/g, "'\\''")+"'";
const python=process.env.AEROAGENTSIM_PYTHON ?? 'python';
const quotedPython="'"+python.replace(/'/g, "'\\''")+"'";
const port=process.env.AEROAGENTSIM_CONSOLE_PORT ?? '8002';
if (!/^\d+$/.test(port) || Number(port)<1 || Number(port)>65535) throw Error('Invalid console port');
const consoleUrl=`http://127.0.0.1:${port}`;
process.env.NO_PROXY=process.env.no_proxy='127.0.0.1,localhost';
export default defineConfig({testDir:'./e2e',testMatch:'traffic-console.spec.ts',outputDir:process.env.AEROAGENTSIM_CONSOLE_OUTPUT ?? 'test-results/console',workers:1,timeout:1_200_000,
 use:{screenshot:'only-on-failure',baseURL:consoleUrl,viewport:{width:1280,height:800},launchOptions:{executablePath:process.env.AEROAGENTSIM_CHROMIUM,args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']}},
 webServer:{command:`cd .. && PYTHONPATH=${quotedPythonPath} AEROAGENTSIM_CONSOLE_URL=${consoleUrl} ${quotedPython} -m uvicorn tests.authoring.console_server:app --host 127.0.0.1 --port ${port}`,url:`${consoleUrl}/v1/runs`,reuseExistingServer:false,timeout:120_000}});
