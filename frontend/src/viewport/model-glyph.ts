import * as T from 'three';

/** Coarse silhouette derived from the actual bound asset, normalized for screen-size LOD. */
export function modelGlyph(model: T.Object3D): T.BufferGeometry {
  const vertices: T.Vector3[]=[],triangles:number[]=[];
  model.updateMatrixWorld(true);
  model.traverse(node=>{
    if(!(node instanceof T.Mesh))return;
    const position=node.geometry.getAttribute('position'),index=node.geometry.getIndex(),base=vertices.length;
    for(let i=0;i<position.count;i++)vertices.push(new T.Vector3().fromBufferAttribute(position,i).applyMatrix4(node.matrixWorld));
    const count=index?index.count:position.count;
    for(let i=0;i<count;i++)triangles.push(base+(index?index.getX(i):i));
  });
  if(!vertices.length)throw Error('Bound model has no silhouette geometry');
  const box=new T.Box3().setFromPoints(vertices),size=box.getSize(new T.Vector3());
  const cells=new Map<string,{index:number;sum:T.Vector3;count:number}>(),mapping:number[]=[];
  for(const vertex of vertices){
    const key=[vertex.x,vertex.y,vertex.z].map((value,axis)=>{
      const extent=size.getComponent(axis);return extent>0?Math.round((value-box.min.getComponent(axis))/extent*14):0;
    }).join('/');
    let cell=cells.get(key);if(!cell){cell={index:cells.size,sum:new T.Vector3(),count:0};cells.set(key,cell);}
    cell.sum.add(vertex);cell.count++;mapping.push(cell.index);
  }
  const positions:number[]=[],indices:number[]=[],seen=new Set<string>();
  const scale=1.6/Math.max(size.x,size.y,size.z);
  if(!Number.isFinite(scale))throw Error('Bound model has degenerate silhouette bounds');
  for(const cell of cells.values())positions.push(...cell.sum.multiplyScalar(scale/cell.count).toArray());
  for(let i=0;i<triangles.length;i+=3){const a=mapping[triangles[i]],b=mapping[triangles[i+1]],c=mapping[triangles[i+2]];if(a===b||a===c||b===c)continue;const key=[a,b,c].sort((a,b)=>a-b).join('/');if(seen.has(key))continue;seen.add(key);indices.push(a,b,c);}
  if(!indices.length)throw Error('Bound model has no nondegenerate silhouette triangles');
  const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.Float32BufferAttribute(positions,3));geometry.setIndex(indices);geometry.computeVertexNormals();geometry.computeBoundingSphere();
  return geometry;
}
