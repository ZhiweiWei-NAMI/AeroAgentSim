import { afterEach, describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { createTrees, loadOsmBuildings, loadSumoRoads } from './source-geometry';
import { disposeObject } from '../viewport/assets';
afterEach(()=>{vi.unstubAllGlobals();});
const source=(text:string)=>vi.stubGlobal('fetch',async()=>({ok:true,text:async()=>text}));
describe('source geometry in run ENU metres',()=>{
  it('uses actual SUMO lane shape and declared width; counts default/absent/internal geometry',async()=>{
    source('<net><location netOffset="12,20"/><edge id="a"><lane shape="0,0 10,0" width="4"/><lane shape="0,4 10,4"/><lane/></edge><edge function="internal"><lane shape="1,1 2,2"/></edge></net>');
    const roads=await loadSumoRoads('/net.xml',new AbortController().signal);
    expect(roads.userData).toMatchObject({laneCount:2,defaultWidthLanes:1,missingShapeLanes:1,internalLanesSkipped:1,netOffset:'12,20'});
    const g=(roads.children[0] as T.Mesh).geometry;expect(g.getAttribute('position').getZ(0)).toBe(-2);expect(g.getAttribute('normal').getY(0)).toBeCloseTo(1);
    disposeObject(roads);
  });
  it('rejects malformed supplied widths instead of replacing them with the SUMO default',async()=>{
    source('<net><edge><lane shape="0,0 10,0" width="bad"/></edge></net>');
    await expect(loadSumoRoads('/net.xml',new AbortController().signal)).rejects.toThrow('width');
  });
  it('does not replace a malformed explicit height with a levels estimate',async()=>{
    source(JSON.stringify({type:'FeatureCollection',features:[{geometry:{type:'Polygon',coordinates:[[[0,0],[.001,0],[0,.001],[0,0]]]},properties:{height:'bad','building:levels':4}}]}));
    await expect(loadOsmBuildings('/osm.json',{lat:0,lon:0,alt:0},new AbortController().signal)).rejects.toThrow('height');
  });
  it('renders declared tree positions using instances without inventing placements',()=>{
    const trees=createTrees([[12,20,3]]),matrix=new T.Matrix4();(trees.children[0] as T.InstancedMesh).getMatrixAt(0,matrix);
    const point=new T.Vector3().setFromMatrixPosition(matrix);expect(point.x).toBe(12);expect(point.y).toBeCloseTo(4.2);expect(point.z).toBe(-20);disposeObject(trees);
  });
  it('preserves GeoJSON holes and identifies estimated/missing heights',async()=>{
    const polygon={type:'Polygon',coordinates:[[[0,0],[.001,0],[.001,.001],[0,.001],[0,0]],[[.0002,.0002],[.0008,.0002],[.0008,.0008],[.0002,.0008],[.0002,.0002]]]};
    source(JSON.stringify({type:'FeatureCollection',features:[{id:'a',geometry:polygon,properties:{'building:levels':4}},{id:'b',geometry:polygon,properties:{}}]}));
    const buildings=await loadOsmBuildings('/osm.json',{lat:0,lon:0,alt:0},new AbortController().signal);
    expect(buildings.userData).toMatchObject({buildingCount:1,estimatedHeightCount:1,skippedMissingHeight:1});
    const mesh=buildings.children[0] as T.Mesh;expect(mesh.userData).toMatchObject({height:12,estimatedHeight:true});
    const shapes=(mesh.geometry as T.ExtrudeGeometry).parameters.shapes as T.Shape;expect(shapes.holes).toHaveLength(1);disposeObject(buildings);
  });
});
