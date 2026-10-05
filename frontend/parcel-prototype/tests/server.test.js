import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
test('HTTP server serves only intended assets with restrictive policy',async()=>{
  const child=spawn(process.execPath,['server.mjs'],{cwd:new URL('../',import.meta.url),env:{...process.env,PORT:'4391'},stdio:['ignore','pipe','pipe']});
  try{
    await Promise.race([once(child.stdout,'data'),once(child,'error').then(([e])=>{throw e;}),new Promise((_,reject)=>{const timer=setTimeout(()=>reject(new Error('server startup timeout')),3000);timer.unref();})]);
    for(const url of ['/','/src/app.js','/src/view.js','/src/styles.css','/favicon.svg']){const res=await fetch(`http://127.0.0.1:4391${url}`);assert.equal(res.status,200);assert.match(res.headers.get('content-security-policy'),/connect-src 'none'/);}
    for(const url of ['/package.json','/README.md','/tests/fixture.test.js','/%2e%2e/p02_integration/architecture_v3.md'])assert.equal((await fetch(`http://127.0.0.1:4391${url}`)).status,404);
    assert.equal((await fetch('http://127.0.0.1:4391/',{method:'POST'})).status,405);
  }finally{child.kill();}
});
