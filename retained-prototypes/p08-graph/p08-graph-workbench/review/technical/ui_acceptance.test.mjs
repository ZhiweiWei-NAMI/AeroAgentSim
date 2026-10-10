/** Independent acceptance tests. Runs against production read-only model without DOM. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {makeIndex,queryNodes,chooseScene,factFields,layoutScene,projectPoint} from '../../ui/model.js';
const node=(id,kind='entity',more={})=>({id,kind,semantic_level:'configured_instance',label:id,payload:{id},...more});
const edge=(id,source,target,relation='REL',more={})=>({id,source,target,relation,role:'participant',payload:{id},...more});
const input=(id,nodes,edges)=>({scenario:{id},data:{schema_version:'p08.typed-graph/v1',nodes,edges}});
const sceneFor=(idx,opts={})=>chooseScene(idx,{matches:queryNodes(idx),limit:100,edgeLimit:100,...opts});

test('parallel edge occurrences and self-loops survive indexing without endpoint dedup',()=>{
 const idx=makeIndex([input('A',[node('a'),node('b')],[edge('e1','a','b'),edge('e2','a','b','REL',{role:'different'}),edge('e3','a','a')])]);
 assert.equal(idx.edges.size,3);assert.equal(idx.adjacent.get('a').length,3);assert.equal(sceneFor(idx).edges.length,3);
});
test('edge payload, direction, relation and role survive scene projection exactly',()=>{
 const e=edge('ast','child','parent','argument_of',{role:'argument',argument_index:2,payload:{order:2,window_s:null}});
 const idx=makeIndex([input('A',[node('child','constant'),node('parent','operator')],[e])]);
 const scene=sceneFor(idx,{focus:'child',hops:1});assert.strictEqual(scene.edges[0],e);assert.equal(scene.edges[0].source,'child');
});
test('filtering away middle nodes does not synthesize shortcut edges',()=>{
 const idx=makeIndex([input('A',[node('a','wanted'),node('b','hidden'),node('c','wanted')],[edge('ab','a','b'),edge('bc','b','c')])]);
 const scene=chooseScene(idx,{matches:queryNodes(idx,{type:'wanted'}),focus:'a',hops:2,limit:20});
 assert.deepEqual(new Set(scene.nodes.map(n=>n.id)),new Set(['a','c']));assert.equal(scene.edges.length,0);assert.equal(scene.boundaryEdges,2);
});
test('bounded preview reports total matching nodes and exact omitted edge counts',()=>{
 const idx=makeIndex([input('A',[node('a'),node('b'),node('c')],[edge('ab','a','b'),edge('bc','b','c'),edge('ac','a','c')])]);
 const scene=sceneFor(idx,{limit:2,edgeLimit:0});assert.equal(scene.neighborhoodTotal,3);assert.equal(scene.omittedNodes,1);assert.equal(scene.eligibleEdgeCount,1);assert.equal(scene.omittedEdges,1);assert.equal(scene.boundaryEdges,2);
});
test('scene retains selected focus outside filter and makes override explicit',()=>{
 const idx=makeIndex([input('A',[node('a','hidden'),node('b','wanted')],[edge('ab','a','b')])]);
 const scene=chooseScene(idx,{matches:queryNodes(idx,{type:'wanted'}),focus:'a',hops:1});
 assert.equal(scene.focusOutsideFilter,true);assert.equal(scene.nodes.length,2);
});
test('every source node kind and edge relation is cataloged even outside visible cap',()=>{
 const ns=Array.from({length:101},(_,i)=>node('n'+i,'kind'+i));
 const es=ns.slice(1).map((n,i)=>edge('e'+i,'n0',n.id,'relation'+i));
 const idx=makeIndex([input('A',ns,es)]);assert.equal(idx.types.size,101);assert.equal(idx.relations.size,100);
 for(const n of ns)assert.equal(queryNodes(idx,{type:n.kind})[0].id,n.id);
 for(const e of es)assert.ok(idx.adjacent.get(e.source).includes(e));
});
test('unknown, missing, null and false values are retained distinctly',()=>{
 assert.deepEqual(factFields(node('x','fact',{payload:{value:false,truth:'Unknown',unit:null,missing_fields:['observed_at']}})),{value:false,unit:null,truth:'Unknown',missing_fields:['observed_at']});
 assert.ok(!Object.hasOwn(factFields(node('x','fact',{payload:{}})),'value'));
});
test('shared canonical nodes do not import another case edge into selected case',()=>{
 const a=node('a'),b=node('b');
 const idx=makeIndex([input('A',[a,b],[edge('A-edge','a','b')]),input('B',[a,b],[edge('B-edge','a','b')])]);
 const caseIds=new Set(['A']);const scene=chooseScene(idx,{matches:queryNodes(idx,{caseIds}),caseIds,limit:10,edgeLimit:10});
 assert.deepEqual(scene.edges.map(e=>e.id),['A-edge']);
});
test('same canonical ID cannot silently mask differing semantic level or identity',()=>{
 const idx=makeIndex([input('A',[node('same','entity',{identity:{generation:1}})],[]),input('B',[node('same','entity',{semantic_level:'runtime_record',identity:{generation:'1'}})],[])]);
 assert.ok(idx.conflicts.some(c=>c.id==='same'));
});
test('dangling endpoints are diagnosed without creating entity nodes',()=>{
 const idx=makeIndex([input('A',[node('a')],[edge('bad','a','absent')])]);assert.equal(idx.dangling.length,1);assert.equal(idx.nodes.has('absent'),false);
});
test('large multi-kind graph indexing and bounded projections are finite',()=>{
 const ns=Array.from({length:20000},(_,i)=>node('n'+i,'kind'+(i%50)));
 const es=Array.from({length:64000},(_,i)=>edge('e'+i,'n'+(i%20000),'n'+((i*37+1)%20000),'rel'+(i%45)));
 const start=performance.now();const idx=makeIndex([input('large',ns,es)]);const scene=sceneFor(idx,{limit:600,edgeLimit:700});const pos=layoutScene(scene);
 assert.equal(idx.nodes.size,20000);assert.equal(idx.edges.size,64000);assert.equal(scene.nodes.length,600);assert.ok(scene.edges.length<=700);
 for(const p of pos.values()){const screen=projectPoint(p,{yaw:0.2,pitch:0.1,panX:0,panY:0,zoom:1},1400,900);assert.ok(Number.isFinite(screen.x)&&Number.isFinite(screen.y));}
 console.log(JSON.stringify({benchmark:'model_only_no_render',source_nodes:ns.length,source_edges:es.length,visible_nodes:scene.nodes.length,visible_edges:scene.edges.length,elapsed_ms:Math.round((performance.now()-start)*100)/100}));
});
