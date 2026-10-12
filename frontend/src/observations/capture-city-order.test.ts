import * as T from 'three';
import {it,expect} from 'vitest';
import {orderCaptureCity,orderedCityGeometry} from './capture-city-order';
function geometry(reverse=false){
 const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute([4,0,0,5,0,0,4,1,0,0,0,0,1,0,0,0,1,0],3));g.setIndex(reverse?[3,4,5,0,1,2]:[0,1,2,3,4,5]);g.userData.sharedAsset=true;return g;
}
it('produces the same face order from different async merge orders and preserves shared buffers',()=>{
 const source=geometry(),ordered=orderedCityGeometry(source);
 expect([...ordered.index!.array]).toEqual([...orderedCityGeometry(geometry(true)).index!.array]);
 expect([...source.index!.array]).toEqual([0,1,2,3,4,5]);expect(source.userData.sharedAsset).toBe(true);expect(ordered.userData.sharedAsset).toBe(false);
});
it('sets draw order from real mesh content across asynchronous insertion orders',()=>{
 function scene(reverse:boolean){const root=new T.Group();const a=new T.Mesh(geometry(),new T.MeshStandardMaterial({color:'red'})),b=new T.Mesh(geometry(),new T.MeshStandardMaterial({color:'blue'}));a.name=b.name='city-buildings';root.add(...(reverse?[b,a]:[a,b]));orderCaptureCity(root);return [a.renderOrder,b.renderOrder];}
 expect(scene(false)).toEqual(scene(true));
});
