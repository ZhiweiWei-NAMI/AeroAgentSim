/** DOM integration using jsdom + renderer stub. NOT a pixel/browser render pass. */
import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
import {JSDOM} from '/workspace/shared/AeroAgentSim-workbench/frontend/console-prototype/node_modules/jsdom/lib/api.js';
globalThis.__P08_TEST__=true;
const {createApp}=await import('../../ui/app.js');
const ROOT=new URL('../../',import.meta.url);const html=await readFile(new URL('ui/index.html',ROOT),'utf8');
class StubRenderer{constructor(){this.labels=false;}setScene(s){this.scene=s}setSelection(s){this.selection=s}setMode(){}fit(){}zoom(){}schedule(){}destroy(){}}
const clone=x=>JSON.parse(JSON.stringify(x));
async function actualFetch(url){const u=new URL(url);const path=u.pathname.replace(/^\//,'');try{return {ok:true,status:200,json:async()=>JSON.parse(await readFile(new URL(path,ROOT),'utf8'))}}catch{return {ok:false,status:404}}}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<100;i++){if(fn())return;await delay(10)}throw new Error('condition not reached')}
function appFor(fetchImpl){const dom=new JSDOM(html,{url:'http://p08.test/ui/'});const app=createApp({document:dom.window.document,fetchImpl,Renderer:StubRenderer,manifestURL:'http://p08.test/data/manifest.json'});return {app,dom,$:id=>dom.window.document.getElementById(id)}}

test('actual data: counts, global coverage, raw unknown/AST inspection and exact edge drilldown',async()=>{
 const {app,dom,$}=appFor(actualFetch);await app.start();await until(()=>app.state.searchReady);
 try{
 assert.equal($('error').hidden,true,$('error').textContent);
 const c=app.state.manifest.counts;assert.ok($('accepted-count').textContent.includes(c.after_canonical_nodes.toLocaleString('en-US')),$('accepted-count').textContent);assert.ok($('accepted-count').textContent.includes(c.after_resolved_edges.toLocaleString('en-US')));
 assert.equal(app.state.scenarios.length,75);assert.equal(app.state.searchIndex.length,c.after_canonical_nodes);
 assert.equal($('type').options.length-1,new Set(app.state.searchIndex.map(n=>n.kind)).size);
 assert.ok($('loaded-count').textContent.includes(app.state.index.nodes.size.toLocaleString('en-US')));
 assert.ok(app.state.scene.nodes.length<=Number($('budget').value));assert.ok($('omissions').textContent.includes('上限'));
 const fact=[...app.state.index.nodes.values()].find(n=>n.kind==='fact');await app.selectNode(fact.id);assert.ok($('detail').textContent.includes(fact.id));assert.ok($('detail').textContent.includes('完整原始记录'));
 const rule=[...app.state.index.nodes.values()].find(n=>n.kind==='rule_definition'&&JSON.stringify(n.payload).includes('definition_ast'));assert.ok(rule);await app.selectNode(rule.id);assert.ok($('detail').textContent.includes('源原生 AST'));assert.ok($('detail').querySelectorAll('.ast-leaf').length>0);
 const e=[...app.state.index.edges.values()].find(e=>e.relation==='argument_of');assert.ok(e);app.selectEdge(e.id);assert.deepEqual([...$('detail').querySelectorAll('[data-node]')].map(x=>x.dataset.node),[e.source,e.target]);assert.ok($('detail').textContent.includes(e.role));assert.ok($('detail').textContent.includes('source → target'));
 $('global-search').checked=false;$('step').value='0';app.state.focus=null;app.render();const stepIds=new Set(app.state.steps[0].node_ids);assert.ok(app.state.results.every(n=>stepIds.has(n.id)));assert.ok(app.state.scene.edges.every(e=>app.state.index.edges.get(e.id)===e));
 }finally{app.destroy();dom.window.close();}
});

test('actual global index result loads exact other case and keeps inspected original payload',async()=>{
 const {app,dom,$}=appFor(actualFetch);await app.start();await until(()=>app.state.searchReady);
 try{const candidate=app.state.searchIndex.find(n=>n.case_ids.includes('MI20'));assert.ok(candidate);await app.selectNode(candidate.id,{fromGlobal:true});assert.equal(app.state.selected.id,candidate.id);assert.equal(app.state.loaded[0].scenario.id,'MI20');assert.ok($('detail').textContent.includes(candidate.id));assert.equal($('error').hidden,true);}finally{app.destroy();dom.window.close();}
});

const n=id=>({id,kind:'entity',semantic_level:'configured_instance',label:id,payload:{id}});
function fixture(){const manifest={counts:{nodes:2,edges:0},scenarios:[{id:'A',label:'A',category:'authored',edge_count:1,path:'A.json'},{id:'B',label:'B',path:'B.json'}]};return {'manifest.json':manifest,'search-index.json':{nodes:[]},'A.json':{nodes:[n('a')],edges:[]},'B.json':{nodes:[n('b')],edges:[]}}}
test('newer navigation wins if older request resolves after abort',async()=>{
 const data=fixture();let hold=false,resume;const fetcher=async url=>{const key=new URL(url).pathname.split('/').pop();if(hold&&key==='A.json')await new Promise(r=>resume=r);return{ok:true,status:200,json:async()=>clone(data[key])}};
 const {app,dom}=appFor(fetcher);await app.start();hold=true;const old=app.loadCases(['A']);await until(()=>resume);const current=app.loadCases(['B']);await current;resume();await old;assert.equal(app.state.loaded[0].scenario.id,'B');assert.ok(app.state.index.nodes.has('b'));assert.ok(!app.state.index.nodes.has('a'));app.destroy();dom.window.close();
});
test('cancelled load keeps last complete graph and can retry',async()=>{
 const data=fixture();let hold=false;const fetcher=async(url,{signal}={})=>{const key=new URL(url).pathname.split('/').pop();if(hold&&key==='B.json')await new Promise((resolve,reject)=>{signal.addEventListener('abort',()=>{const e=new Error('aborted');e.name='AbortError';reject(e)},{once:true})});return{ok:true,status:200,json:async()=>clone(data[key])}};
 const {app,dom,$}=appFor(fetcher);await app.start();hold=true;const next=app.loadCases(['B']);$('cancel-load').click();await next;assert.equal(app.state.loaded[0].scenario.id,'A');assert.ok($('load-status').textContent.includes('取消'));hold=false;await app.loadCases(['B']);assert.equal(app.state.loaded[0].scenario.id,'B');app.destroy();dom.window.close();
});
test('failed case load is surfaced and never replaces current graph with partial data',async()=>{
 const data=fixture();const fetcher=async url=>{const key=new URL(url).pathname.split('/').pop();return key==='B.json'?{ok:false,status:500}:{ok:true,status:200,json:async()=>clone(data[key])}};
 const {app,dom,$}=appFor(fetcher);await app.start();await app.loadCases(['A','B']);assert.equal(app.state.loaded.length,1);assert.equal(app.state.loaded[0].scenario.id,'A');assert.equal($('error').hidden,false);app.destroy();dom.window.close();
});
