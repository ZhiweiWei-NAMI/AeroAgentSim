// Synthetic, dataset-free checks for the source-only backup. No catalog or case records.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {once} from 'node:events';
import {makeIndex, localScene, Force3D, project, matches} from '../ui/model.js';
import {safeDataURL, chooseScene} from '../ui/canonical-model.js';
import {displayNodeLabel, displayEdgeLabel, displayLabels} from '../ui/display.js';
import {facetValue, timeText, truthText} from '../ui/coherent.js';
import {createApp} from '../ui/app.js';
import {StarRenderer} from '../ui/renderer.js';

const makeSynthetic = () => ({nodes:[
  {id:'synthetic:resource',kind:'resource',label:'synthetic:resource',payload:{dimension:'cpu_millicores'}},
  {id:'synthetic:command',kind:'command_attempt',label:'synthetic:command',payload:{arguments:{operation:'inspect_custom_fixture'}}},
  {id:'synthetic:execution',kind:'behavior_execution',label:'synthetic:execution',payload:{}},
  {id:'synthetic:candidate',kind:'event_occurrence',label:'synthetic:candidate',payload:{emitted:false}},
],edges:[
  {id:'synthetic:edge1',source:'synthetic:execution',target:'synthetic:command',relation:'requested_by_attempt'},
  {id:'synthetic:edge2',source:'synthetic:command',target:'synthetic:resource',relation:'uses_resource'},
]});
const indexed = () => {const data=makeSynthetic(); return {data,index:makeIndex([{data,scenario:{id:'synthetic'}}])};};

test('UI and renderer entry points import without starting an app in Node',()=>{
  assert.equal(typeof createApp,'function'); assert.equal(typeof StarRenderer,'function');
});
test('index preserves synthetic record identity and original edge direction',()=>{
  const {data,index}=indexed(); assert.equal(index.nodes.size,4); assert.equal(index.edges.size,2);
  assert.equal(index.nodes.get('synthetic:command'),data.nodes[1]); assert.equal(index.dangling.length,0);
  assert.equal(index.edges.get('synthetic:edge1').source,'synthetic:execution');
  assert.equal(matches(index,{query:'synthetic command'}).length,1);
});
test('bounded neighborhood returns only existing records and reports omissions',()=>{
  const {index}=indexed(); const scene=localScene(index,{focus:'synthetic:command',hops:2,limit:2});
  assert.equal(scene.nodes.length,2); assert.equal(scene.omittedNodes,1);
  for(const node of scene.nodes) assert.equal(node,index.nodes.get(node.id));
  for(const edge of scene.edges) assert.equal(edge,index.edges.get(edge.id));
  const canonical=chooseScene(index,{matches:[...index.nodes.values()],limit:2});
  assert.equal(canonical.nodes.length,2); assert.equal(canonical.omittedNodes,2);
});
test('force layout is deterministic, finite, and does not mutate source records',()=>{
  const {data,index}=indexed(); const before=JSON.stringify(data);
  const scene=localScene(index,{focus:'synthetic:command',hops:2,limit:10});
  const a=new Force3D(scene),b=new Force3D(scene); a.tick(40); b.tick(40);
  assert.deepEqual([...a.points],[...b.points]); assert.equal(JSON.stringify(data),before);
  for(const p of a.points.values()) {assert.ok([p.x,p.y,p.z].every(Number.isFinite));
    const q=project(p,{yaw:.2,pitch:.1,zoom:1,panX:0,panY:0},800,600);
    assert.ok([q.x,q.y,q.depth].every(Number.isFinite));}
});
test('labels follow explicit edges, preserve unknown names, and leave source untouched',()=>{
  const {data,index}=indexed(); const before=JSON.stringify(data); const labels=displayLabels(index);
  assert.equal(labels.get('synthetic:resource'),'CPU 算力');
  assert.equal(labels.get('synthetic:command'),'指令 · inspect custom fixture');
  assert.equal(labels.get('synthetic:execution'),'行为 · inspect custom fixture');
  assert.equal(labels.get('synthetic:candidate'),'候选事件');
  assert.equal(displayEdgeLabel({relation:'unmapped_source_relation'}),'unmapped source relation');
  assert.equal(displayNodeLabel({id:'synthetic:authority',kind:'authority_record',payload:{}}),'权限证据声明');
  assert.equal(JSON.stringify(data),before);
});
test('canonical URL helper rejects cross-origin and non-HTTP data',()=>{
  assert.equal(safeDataURL('../data/sample.json','https://example.test/ui/'),'https://example.test/data/sample.json');
  assert.throws(()=>safeDataURL('https://elsewhere.test/sample.json','https://example.test/ui/'));
  assert.throws(()=>safeDataURL('file:///tmp/sample.json','https://example.test/ui/'));
});
test('semantic formatting preserves unknown state and declared clock',()=>{
  assert.equal(facetValue({facets:{truth:{status:'unknown',value:true}}},'truth'),undefined);
  assert.equal(truthText(null),'Unknown'); assert.equal(truthText(false),'False');
  assert.equal(timeText({value:3,unit:'s',clock_domain:'synthetic'}),'3 s · synthetic');
});
test('loopback source server serves exact UI, rejects writes, and exposes missing data',async()=>{
  process.env.PORT='0'; const {server}=await import('../ui/server.mjs');
  if(!server.listening) await once(server,'listening');
  const address=server.address(); assert.equal(address.address,'127.0.0.1');
  const origin='http://127.0.0.1:'+address.port;
  try {
    const response=await fetch(origin+'/ui/',{headers:{'Accept-Encoding':'gzip'}});
    assert.equal(response.status,200); assert.equal(response.headers.get('content-encoding'),'gzip');
    assert.equal(await response.text(),fs.readFileSync(new URL('../ui/index.html',import.meta.url),'utf8'));
    assert.equal((await fetch(origin+'/data/native-index.json')).status,404);
    assert.equal((await fetch(origin+'/README.md')).status,404);
    assert.equal((await fetch(origin+'/ui/',{method:'POST'})).status,405);
  } finally {server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));}
});
