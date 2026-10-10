import test from 'node:test';import assert from 'node:assert/strict';
import {GraphRenderer} from '../../ui/renderer.js';
const ctx=new Proxy({measureText:t=>({width:String(t).length*6})},{get:(o,k)=>k in o?o[k]:(()=>{}),set:(o,k,v)=>(o[k]=v,true)});
const canvas=()=>({style:{},getContext:()=>ctx,getBoundingClientRect:()=>({width:900,height:600,left:0,top:0}),addEventListener(){},removeEventListener(){}});
globalThis.requestAnimationFrame=()=>1;globalThis.cancelAnimationFrame=()=>{};
const node=id=>({id,label:id,kind:'entity',semantic_level:'configured_instance',payload:{id}});
const edge=(id,source,target)=>({id,source,target,relation:'related'});
const geometryKey=e=>[e.a,e.b].map(p=>`${p.x.toFixed(2)},${p.y.toFixed(2)}`).sort().join(';');
test('anti-parallel edges use distinct visible/hit-test strokes',()=>{
 const renderer=new GraphRenderer(canvas());renderer.setScene({nodes:[node('a'),node('b')],edges:[edge('ab','a','b'),edge('ba','b','a')],distance:new Map()});renderer.draw();
 assert.equal(renderer.hitEdges.length,2);assert.notEqual(geometryKey(renderer.hitEdges[0]),geometryKey(renderer.hitEdges[1]));renderer.destroy();
});
test('same-direction parallel edges use separate strokes without dropping IDs',()=>{
 const renderer=new GraphRenderer(canvas());renderer.setScene({nodes:[node('a'),node('b')],edges:[edge('e1','a','b'),edge('e2','a','b'),edge('e3','a','b')],distance:new Map()});renderer.draw();
 assert.equal(new Set(renderer.hitEdges.map(geometryKey)).size,3);assert.deepEqual(renderer.hitEdges.map(e=>e.id),['e1','e2','e3']);renderer.destroy();
});
