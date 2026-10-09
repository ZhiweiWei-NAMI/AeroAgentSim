import * as T from 'three';
import type { PackedBatch } from './mesh-pack';

/** Estimated display equipment on existing flat roof triangles. No physical roof data is added. */
export function createRoofDetails(buildings: ReadonlyArray<{ geometry: T.BufferGeometry; batch: PackedBatch }>) {
  const candidates = new Map<string, { point: T.Vector3; clearance: number }[]>();
  const a = new T.Vector3(), b = new T.Vector3(), c = new T.Vector3(), ab = new T.Vector3(), ac = new T.Vector3();
  for (const { geometry, batch } of buildings) {
    const positions = geometry.getAttribute('position'), index = geometry.getIndex();
    let start = 0;
    for (const range of batch.ranges) {
      const end = range.end * 3, id = range.target?.kind === 'building' ? range.target.id : undefined;
      if (id) for (let i = start; i < end; i += 3) {
        a.fromBufferAttribute(positions, index ? index.getX(i) : i);
        b.fromBufferAttribute(positions, index ? index.getX(i + 1) : i + 1);
        c.fromBufferAttribute(positions, index ? index.getX(i + 2) : i + 2);
        if (a.y < 12 || Math.max(a.y,b.y,c.y)-Math.min(a.y,b.y,c.y) > 0.02) continue;
        const cross = ab.subVectors(b,a).cross(ac.subVectors(c,a));
        if (cross.y < 10) continue;
        const longest = Math.max(a.distanceTo(b),b.distanceTo(c),c.distanceTo(a));
        const clearance = cross.length() / longest / 3;
        if (clearance < 1.25) continue;
        const point = a.clone().add(b).add(c).multiplyScalar(1/3);
        const list = candidates.get(id) ?? []; list.push({point,clearance});candidates.set(id,list);
      }
      start = end;
    }
  }
  const points: T.Vector3[] = [];
  for (const list of candidates.values()) {
    const top = Math.max(...list.map(item=>item.point.y));
    const chosen: T.Vector3[] = [];
    for (const item of list.sort((a,b)=>b.clearance-a.clearance)) {
      if (item.point.y < top - 0.1 || chosen.some(p=>p.distanceTo(item.point)<8)) continue;
      chosen.push(item.point);if(chosen.length===2)break;
    }
    points.push(...chosen);
  }
  const root = new T.Group(); root.name='display-roof-equipment'; root.userData={displayDecoration:true,estimatedEquipmentCount:points.length};
  if(!points.length)return root;
  const units = new T.InstancedMesh(new T.BoxGeometry(2.4,0.85,1.8), new T.MeshStandardMaterial({color:'#8f9699',roughness:0.7,metalness:0.2}),points.length);
  const vents = new T.InstancedMesh(new T.CylinderGeometry(0.45,0.45,0.12,12),new T.MeshStandardMaterial({color:'#353d43',roughness:0.6,metalness:0.25}),points.length);
  const matrix = new T.Matrix4();
  points.forEach((point,i)=>{matrix.makeTranslation(point.x,point.y+0.425,point.z);units.setMatrixAt(i,matrix);matrix.makeTranslation(point.x,point.y+0.91,point.z);vents.setMatrixAt(i,matrix);});
  for(const mesh of [units,vents]){mesh.userData.displayDecoration=true;mesh.castShadow=true;mesh.receiveShadow=true;mesh.computeBoundingSphere();root.add(mesh);}
  return root;
}
