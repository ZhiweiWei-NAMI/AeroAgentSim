import { expect,it } from 'vitest';
import { draftChanges, proposalWorkspace } from './draftChanges';
import type { Workspace } from '../studio/api';

it('T2-AS-007: compares real capacity and position edits at the entity field, without depending on array order',()=>{
  const before={entities:[{id:'pad',facts:{'lowalt.site_capacity':2,'lowalt.site_position':[0,0,0]}},{id:'unchanged',facts:{value:5}}]};
  const after={entities:[{id:'unchanged',facts:{value:5}},{id:'pad',facts:{'lowalt.site_position':[50,0,0],'lowalt.site_capacity':4}}]};
  expect(draftChanges(before,after)).toEqual([
    {path:['entities','pad','facts','lowalt.site_capacity'],before:2,after:4},
    {path:['entities','pad','facts','lowalt.site_position'],before:[0,0,0],after:[50,0,0]},
  ]);
});
it('does not mark a structurally identical draft as changed, and ignores rebuilt behaviour IR only',()=>{
  const before={behaviours:[{id:'rules',chains:[]}],engines:{behaviour:{plugin:'behaviour',config:{packages:[{source:'plan/files/rules'}]}}},entities:[{id:'a',facts:{value:0}},{id:'b',facts:{value:false}}]};
  const after={entities:[{facts:{value:false},id:'b'},{facts:{value:0},id:'a'}],engines:{behaviour:{config:{packages:[{source:'workspace/rules'}]},plugin:'behaviour'}},behaviours:[{chains:[],id:'rules'}]};
  expect(draftChanges(before,after)).toEqual([]);
  expect(draftChanges(before,{...after,behaviours:[{id:'rules',chains:[{id:'changed'}]}]})).not.toEqual([]);
});
it('reports removal and addition distinctly, preserving false, zero and null as recorded values',()=>{
  expect(draftChanges({wind:0,flag:false,removed:1},{wind:null,flag:false,added:0})).toEqual([
    {path:['added'],before:undefined,after:0},{path:['removed'],before:1,after:undefined},{path:['wind'],before:0,after:null},
  ]);
});
it('T4-R2-016: passes through proposal geography instead of retaining the old workspace geography',()=>{
  const old:Workspace={id:'w',name:'Existing',scenario:{},region:{extract:'old',bounds:[1,2,3,4],alt:0,level_height_m:3},scene:{origin:{lat:1,lon:2,alt:0},bounds:[1,2,3,4],geojson:{type:'FeatureCollection',features:[]},roads:[],ground:{width_m:100,depth_m:100},diagnostics:[],attribution:'OSM'}};
  const scene:Workspace['scene']={...old.scene!,origin:{lat:31.3,lon:121.5,alt:0}};
  const region={extract:'wujiaochang',bounds:[121,31,122,32],alt:0,level_height_m:3.5};
  const next=proposalWorkspace(old,{name:'New',scenario:{new:true},scene,region},'s',true);
  expect(next.scene).toBe(scene);expect(next.region).toBe(region);expect(next.scenario).toEqual({new:true});
  const unavailable=proposalWorkspace(old,{name:'New',scenario:{}},'s',true);
  expect(unavailable.scene).toBeUndefined();expect(unavailable.region).toBeUndefined();
});
