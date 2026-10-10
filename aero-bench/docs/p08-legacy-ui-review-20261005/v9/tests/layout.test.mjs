import test from 'node:test';
import assert from 'node:assert/strict';
import {clearPlacement,overlapArea,tooltipPlacement,wrapLabel} from '../ui/layout.js';

test('tooltip avoids both edge endpoints and labels while staying in frame',()=>{
 const obstacles=[{x:150,y:130,w:40,h:40,weight:100},{x:265,y:240,w:40,h:40,weight:100},
                  {x:260,y:270,w:110,h:23,weight:1}];
 const box=tooltipPlacement({x:260,y:240},{w:180,h:100},{w:500,h:400},obstacles);
 assert.ok(obstacles.every(o=>overlapArea(box,o)===0));
 assert.ok(box.x>=8&&box.y>=8&&box.x+box.w<=492&&box.y+box.h<=392);
});

test('active text wraps without deleting names or IDs; labels cannot cover nodes',()=>{
 const name='完整的选中规则名称_and-identifier';
 assert.equal(wrapLabel(name,50,s=>s.length*10).join(''),name);
 assert.equal(clearPlacement([{x:50,y:50,w:100,h:22}],{x:0,y:0,w:300,h:200},[],
  [{x:70,y:50,w:15,h:15}]),undefined);
 assert.deepEqual(clearPlacement([{x:50,y:50,w:100,h:22},{x:50,y:90,w:100,h:22}],
  {x:0,y:0,w:300,h:200},[],[{x:70,y:50,w:15,h:15}]),{x:50,y:90,w:100,h:22});
});
