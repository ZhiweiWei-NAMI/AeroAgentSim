import * as T from 'three';

/** The shared city's async loader can merge faces in download completion order.
 * Order capture-owned index buffers by actual vertex data so coplanar faces and
 * material draw order cannot depend on network timing. Shared viewport buffers
 * remain untouched; there is still one mesh/lighting/rendering implementation. */
export function orderedCityGeometry(source: T.BufferGeometry): T.BufferGeometry {
  const index=source.index;
  if(!index || index.count%3 || source.groups.length)throw Error('Capture city needs indexed, ungrouped triangles');
  const attributes=Object.keys(source.attributes).sort().map(key=>source.getAttribute(key));
  const faces=Array.from({length:index.count/3},(_,i)=>i);
  faces.sort((a,b)=>{
    for(let corner=0;corner<3;corner++)for(const attribute of attributes){
      const x=index.getX(a*3+corner),y=index.getX(b*3+corner);
      for(let c=0;c<attribute.itemSize;c++){const difference=attribute.getComponent(x,c)-attribute.getComponent(y,c);if(difference)return difference;}
    }
    return 0;
  });
  const geometry=source.clone();geometry.userData={...geometry.userData,sharedAsset:false};
  geometry.setIndex(faces.flatMap(face=>[index.getX(face*3),index.getX(face*3+1),index.getX(face*3+2)]));
  geometry.computeBoundingBox();return geometry;
}

export function orderCaptureCity(root:T.Object3D) {
  const meshes:T.Mesh[]=[];
  root.traverse(node=>{if(node instanceof T.Mesh&&node.name==='city-buildings'){
    node.geometry=orderedCityGeometry(node.geometry);meshes.push(node);
  }});
  const key=(mesh:T.Mesh)=>{
    const geometry=mesh.geometry,index=geometry.index!,box=geometry.boundingBox!;
    const material=mesh.material as T.MeshStandardMaterial;
    const vertices=Object.keys(geometry.attributes).sort().flatMap(name=>{const a=geometry.getAttribute(name);return [0,index.count-1].flatMap(i=>Array.from({length:a.itemSize},(_,c)=>a.getComponent(index.getX(i),c)));});
    return JSON.stringify([box.min.toArray(),box.max.toArray(),material.color.toArray(),material.emissive.toArray(),material.roughness,material.metalness,material.opacity,material.transparent,material.side,vertices]);
  };
  const keys=new Map(meshes.map(mesh=>[mesh,key(mesh)]));
  meshes.sort((a,b)=>keys.get(a)!<keys.get(b)!?-1:keys.get(a)!>keys.get(b)!?1:0);
  meshes.forEach((mesh,i)=>{mesh.renderOrder=i+1;});
}
