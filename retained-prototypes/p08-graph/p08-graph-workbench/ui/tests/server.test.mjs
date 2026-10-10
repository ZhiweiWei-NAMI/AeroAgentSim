import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
// HTTP route smoke test only. This is not browser navigation or a pixel/visual test.
test('loopback static server serves UI and canonical manifest, rejects unrelated paths',async()=>{
  const child=spawn(process.execPath,[new URL('../server.mjs',import.meta.url).pathname],{env:{...process.env,PORT:'14318'},stdio:['ignore','pipe','pipe']});
  let timer;try{await Promise.race([once(child.stdout,'data'),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('server startup timeout')),4000)})]);clearTimeout(timer);
    const page=await fetch('http://127.0.0.1:14318/ui/');assert.equal(page.status,200);assert.match(await page.text(),/实例关系图工作台/);
    const manifest=await fetch('http://127.0.0.1:14318/data/manifest.json');assert.equal(manifest.status,200);assert.equal((await manifest.json()).graph_schema,'p08.typed-graph/v1');
    assert.equal((await fetch('http://127.0.0.1:14318/other.json')).status,404);
  }finally{clearTimeout(timer);child.kill();await once(child,'exit');}
});
