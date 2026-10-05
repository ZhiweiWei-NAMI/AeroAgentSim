// Dataset-free geometry checks; source data remains untouched.
import test from 'node:test';
import assert from 'node:assert/strict';
import {Force3D, project} from '../ui/model.js';
import {StarRenderer} from '../ui/renderer.js';

test('one domain group has an origin anchor, multiple groups retain separation',()=>{
 const scene={mode:'overview',groups:[{id:'delivery'}],nodes:[{id:'n',domain:'delivery'}],edges:[]};
 assert.deepEqual(new Force3D(scene).anchors.get('delivery'),{x:0,y:0,z:0});
 const anchors=new Force3D({...scene,groups:[{id:'delivery'},{id:'city'}]}).anchors;
 assert.notDeepEqual(anchors.get('delivery'),anchors.get('city'));
});

test('fit centers the real projected bounds even when points are off origin',()=>{
 const points=new Map([['a',{x:400,y:80,z:-20}],['b',{x:630,y:-120,z:50}],['c',{x:570,y:200,z:-80}]]);
 const renderer=Object.create(StarRenderer.prototype);
 Object.assign(renderer,{width:900,height:600,scene:{mode:'overview'},force:{points},camera:{}});
 renderer.fit();
 const projected=[...points.values()].map(p=>project(p,renderer.camera,renderer.width,renderer.height));
 const xs=projected.map(p=>p.x),ys=projected.map(p=>p.y);
 assert.ok(Math.abs((Math.min(...xs)+Math.max(...xs))/2-450)<1e-9);
 assert.ok(Math.abs((Math.min(...ys)+Math.max(...ys))/2-300)<1e-9);
 assert.ok(Math.min(...xs)>=50 && Math.max(...xs)<=850);
 assert.ok(Math.min(...ys)>=70 && Math.max(...ys)<=530);
});
