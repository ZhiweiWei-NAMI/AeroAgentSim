import { describe, expect, it } from 'vitest';
import * as T from 'three';
import { StaticOcclusion } from './occlusion';

describe('display camera clearance from loaded city geometry', () => {
  const building = () => { const mesh = new T.Mesh(new T.BoxGeometry(20, 60, 20), new T.MeshBasicMaterial()); mesh.position.set(22, 30, 24); return mesh; };
  it('raises a follow endpoint and an intermediate camera above the actual roof', () => {
    const mesh = building(), index = new StaticOcclusion(); index.setObjects([mesh]);
    const inside = new T.Vector3(22, 14, 24), result = index.liftCamera(inside);
    expect(result.toArray()).toEqual([22, 64, 24]);
    expect(inside.toArray()).toEqual([22, 14, 24]);
    expect(index.liftCamera(new T.Vector3(10, 30, 24)).y).toBe(64);
    expect(index.liftCamera(new T.Vector3(22, 80, 24)).y).toBe(80);
  });
  it('keeps an unobstructed street camera close, and accounts for progressive attachment', () => {
    const index = new StaticOcclusion(), position = new T.Vector3(22, 14, 24);
    expect(index.liftCamera(position).toArray()).toEqual(position.toArray());
    index.addObjects([building()]);
    expect(index.liftCamera(position).y).toBe(64);
    expect(index.liftCamera(new T.Vector3(80, 14, 24)).toArray()).toEqual([80, 14, 24]);
  });
  it('preserves camera clearance after a floating origin shift, including instanced buildings', () => {
    const mesh = new T.InstancedMesh(new T.BoxGeometry(20, 60, 20), new T.MeshBasicMaterial(), 1);
    mesh.setMatrixAt(0, new T.Matrix4().makeTranslation(22, 30, 24));
    const index = new StaticOcclusion(); index.setObjects([mesh]);
    index.translate(new T.Vector3(-2000, 0, 1000));
    expect(index.liftCamera(new T.Vector3(-1978, 14, 1024)).toArray()).toEqual([-1978, 64, 1024]);
  });
});
