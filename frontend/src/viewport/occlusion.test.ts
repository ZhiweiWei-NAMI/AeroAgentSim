import { describe, expect, it } from 'vitest';
import * as T from 'three';
import { StaticOcclusion } from './occlusion';

// Compare segment queries directly with Three's raycaster on visible fixtures.
function blockedByRaycaster(roots: T.Object3D[], raycaster: T.Raycaster, from: T.Vector3, to: T.Vector3): boolean {
  const far=from.distanceTo(to);if(far===0)return false;
  raycaster.set(from,to.clone().sub(from).divideScalar(far));raycaster.near=0;raycaster.far=far;
  return roots.some(root=>raycaster.intersectObject(root,true).length>0);
}

function expectParity(occlusion: StaticOcclusion, roots: T.Object3D[], raycaster: T.Raycaster, from: T.Vector3, to: T.Vector3) {
  const left = occlusion.blocked(from.clone(), to.clone());
  const right = blockedByRaycaster(roots, raycaster, from, to);
  expect([left, right], `segment ${from.toArray()} -> ${to.toArray()}`).toEqual([right, right]);
}

function sweepParity(occlusion: StaticOcclusion, roots: T.Object3D[], raycaster: T.Raycaster, origin: T.Vector3) {
  // Deterministic fan of segments aimed at the scene center from x = +6.
  for (let deg = -40; deg <= 40; deg += 4) {
    const offset = new T.Vector3(0, deg * 0.075, 0);
    expectParity(occlusion, roots, raycaster, origin.clone().add(new T.Vector3(6, offset.y, 5)), origin.clone().add(offset));
  }
}

function boxMesh(material: T.Material | T.Material[], index: boolean): T.Mesh {
  const geometry = index ? new T.BoxGeometry(1, 1, 1) : new T.BoxGeometry(1, 1, 1).toNonIndexed();
  if(Array.isArray(material))for(const group of geometry.groups)group.materialIndex=group.materialIndex===0?0:1;
  geometry.computeBoundingSphere();
  geometry.computeBoundingBox();
  const mesh = new T.Mesh(geometry, material);
  mesh.updateWorldMatrix(true, true);
  return mesh;
}

describe('StaticOcclusion parity vs THREE.Raycaster', () => {
  it('indexed mesh: deterministic ray sweep agrees with Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial(), true);
    const roots = [mesh];
    occlusion.setObjects(roots);
    sweepParity(occlusion, roots, new T.Raycaster(), new T.Vector3());
    // Sanity: the center segment genuinely pierces the box for both.
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(true);
    occlusion.dispose();
  });

  it('non-indexed mesh: deterministic ray sweep agrees with Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial(), false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    sweepParity(occlusion, roots, new T.Raycaster(), new T.Vector3());
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(true);
    occlusion.dispose();
  });

  it('rotated mesh: sweep agrees with Raycaster (world matrices, not node transforms)', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial(), true);
    mesh.rotation.set(0, Math.PI / 2, Math.PI / 6);
    mesh.position.set(2, 1, -3);
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    // Sweep aims at the mesh center so segments actually cross the rotated box.
    sweepParity(occlusion, roots, new T.Raycaster(), mesh.position.clone());
    occlusion.dispose();
  });

  it('nonuniform scale: sweep agrees with Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial(), true);
    mesh.scale.set(4, 0.5, 2);
    mesh.position.set(1, 0, 0);
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    sweepParity(occlusion, roots, new T.Raycaster(), mesh.position.clone());
    occlusion.dispose();
  });

  it('mirrored transform (negative determinant): sweep agrees with Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial({ side: T.DoubleSide }), true);
    mesh.scale.set(-2, 1, 1);
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    sweepParity(occlusion, roots, new T.Raycaster(), new T.Vector3());
    occlusion.dispose();
  });

  it('FrontSide / BackSide / DoubleSide agree with Raycaster from both directions', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const inside = new T.Vector3(0, 0, 0);
    const front = new T.Vector3(0, 0, 4); // +z face is a FrontSide face of BoxGeometry
    const back = new T.Vector3(0, 0, -4);
    for (const side of [T.FrontSide, T.BackSide, T.DoubleSide] as const) {
      const mesh = boxMesh(new T.MeshBasicMaterial({ side }), true);
      const roots = [mesh];
      occlusion.setObjects(roots);
      expectParity(occlusion, roots, raycaster, front, inside);
      expectParity(occlusion, roots, raycaster, back, inside);
      occlusion.dispose();
    }
  });
});

describe('StaticOcclusion material handling vs THREE.Raycaster', () => {
  it('multi-material groups: visibility and face split agree with Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const inside = new T.Vector3(0, 0, 0);
    // Segments that enter through the +x face (group 0) and the -x face (group 1).
    const fromRight = new T.Vector3(4, 0, 0.25);
    const fromLeft = new T.Vector3(-4, 0, 0.25);
    const build = (leftVisible: boolean, rightVisible: boolean) => {
      const rightMaterial = new T.MeshBasicMaterial({ visible: rightVisible });
      const leftMaterial = new T.MeshBasicMaterial({ visible: leftVisible });
      const mesh = boxMesh([rightMaterial, leftMaterial], true);
      const roots = [mesh];
      occlusion.setObjects(roots);
      return { roots, mesh };
    };
    // Both groups visible: blocked from both directions, matching Raycaster.
    let built = build(true, true);
    expectParity(occlusion, built.roots, raycaster, fromRight, inside);
    expectParity(occlusion, built.roots, raycaster, fromLeft, inside);
    occlusion.dispose();
    // Group 0 invisible: entry face +x is not an occluder for the right ray…
    built = build(true, false);
    expect(occlusion.blocked(fromRight, inside)).toBe(false);
    // …while the left ray still enters through the visible group-1 face.
    expect(occlusion.blocked(fromLeft, inside)).toBe(true);
    occlusion.dispose();
    // Only group 0 visible: mirrored outcome.
    built = build(false, true);
    expect(occlusion.blocked(fromRight, inside)).toBe(true);
    expect(occlusion.blocked(fromLeft, inside)).toBe(false);
    occlusion.dispose();
  });

  it('drawRange (indexed): only the drawn triangle range occludes, like Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const material = new T.MeshBasicMaterial();
    const geometry = new T.BoxGeometry(1, 1, 1);
    geometry.computeBoundingSphere();
    // BoxGeometry index order: +x, -x, +y, -y, +z, -z faces (6 indices each).
    geometry.setDrawRange(24, 12); // keep only the +/-z faces
    const mesh = new T.Mesh(geometry, material);
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    const inside = new T.Vector3(0, 0, 0);
    // Segments entering through a drawn (+/-z) face occlude…
    expectParity(occlusion, roots, raycaster, new T.Vector3(0, 0, 4), inside);
    expectParity(occlusion, roots, raycaster, new T.Vector3(0, 0, -4), inside);
    // …segments entering through an excluded (+/-x) face do not.
    expectParity(occlusion, roots, raycaster, new T.Vector3(4, 0, 0), inside);
    occlusion.dispose();
  });

  it('drawRange (non-indexed): only the drawn vertex range occludes, like Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const material = new T.MeshBasicMaterial();
    const geometry = new T.BoxGeometry(1, 1, 1).toNonIndexed();
    geometry.computeBoundingSphere();
    geometry.setDrawRange(6, 6); // keep only the -x face (vertices 6..11)
    const mesh = new T.Mesh(geometry, material);
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    const inside = new T.Vector3(0, 0, 0);
    expectParity(occlusion, roots, raycaster, new T.Vector3(-4, 0, 0), inside);
    expectParity(occlusion, roots, raycaster, new T.Vector3(4, 0, 0), inside);
    occlusion.dispose();
  });
});

describe('StaticOcclusion instancing vs THREE.Raycaster', () => {
  it('instanced mesh: every instance occludes like its Raycaster counterpart', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const geometry = new T.BoxGeometry(1, 1, 1);
    geometry.computeBoundingSphere();
    const material = new T.MeshBasicMaterial();
    const mesh = new T.InstancedMesh(geometry, material, 4);
    mesh.setMatrixAt(0, new T.Matrix4().makeTranslation(0, 0, 0));
    mesh.setMatrixAt(1, new T.Matrix4().makeTranslation(2, 0, 0));
    const mirrored = new T.Matrix4().makeTranslation(-2, 0, 0).multiply(new T.Matrix4().makeScale(-1, 1, 1));
    mesh.setMatrixAt(2, mirrored);
    mesh.setMatrixAt(3,new T.Matrix4().makeTranslation(8,0,0));mesh.count=3;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    expect(mesh.count).toBe(3); // The fourth allocated slot is not drawn.
    expect(occlusion.blocked(new T.Vector3(8,0,4),new T.Vector3(8,0,0))).toBe(false);
    sweepParity(occlusion, roots, raycaster, new T.Vector3());
    occlusion.dispose();
  });

  it('mesh-level transform composes with instance matrices like Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const geometry = new T.BoxGeometry(1, 1, 1);
    geometry.computeBoundingSphere();
    const mesh = new T.InstancedMesh(geometry, new T.MeshBasicMaterial(), 1);
    mesh.setMatrixAt(0, new T.Matrix4().makeTranslation(1, 0, 0));
    mesh.position.set(0, 2, 0);
    mesh.rotation.set(0, Math.PI / 4, 0);
    mesh.scale.set(2, 1, 1);
    mesh.updateWorldMatrix(true, false);
    const roots = [mesh];
    occlusion.setObjects(roots);
    sweepParity(occlusion, roots, raycaster, mesh.position.clone());
    occlusion.dispose();
  });
});

describe('StaticOcclusion visibility follows rendering (intentional Raycaster difference)', () => {
  // INTENTIONAL DIFFERENCE, documented per contract: StaticOcclusion is built
  // with traverseVisible, so invisible meshes (or subtrees) never occlude
  // labels. THREE.Raycaster has no notion of `visible`: raycasting the same
  // invisible mesh directly still returns intersections. Parity is therefore
  // asserted only on visible fixtures,
  // never against a raw Raycaster over invisible geometry.
  it('invisible leaf mesh does not occlude until made visible', () => {
    const occlusion = new StaticOcclusion();
    const material = new T.MeshBasicMaterial();
    const mesh = boxMesh(material, true);
    mesh.visible = false;
    const roots = [mesh];
    occlusion.setObjects(roots);
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(false);
    mesh.visible = true;
    occlusion.setObjects(roots);
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(true);
    occlusion.dispose();
  });

  it('invisible parent hides visible children; visible parent exposes them', () => {
    const occlusion = new StaticOcclusion();
    const parent = new T.Group();
    parent.visible = false;
    const leftChild = boxMesh(new T.MeshBasicMaterial(), true);
    leftChild.position.set(-3, 0, 0);
    const rightChild = boxMesh(new T.MeshBasicMaterial(), true);
    rightChild.position.set(3, 0, 0);
    parent.add(leftChild, rightChild);
    parent.updateWorldMatrix(true, true);
    const roots = [parent];
    occlusion.setObjects(roots);
    expect(occlusion.blocked(new T.Vector3(-6, 0, 5), new T.Vector3(-3, 0, 0))).toBe(false);
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(3, 0, 0))).toBe(false);
    parent.visible = true;
    occlusion.setObjects(roots);
    expect(occlusion.blocked(new T.Vector3(-6, 0, 5), new T.Vector3(-3, 0, 0))).toBe(true);
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(3, 0, 0))).toBe(true);
    occlusion.dispose();
  });
});

describe('StaticOcclusion segment handling and lifecycle', () => {
  it('misses, zero-length segments, endpoints and empty sets agree with Raycaster', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const mesh = boxMesh(new T.MeshBasicMaterial(), true);
    const roots = [mesh];
    occlusion.setObjects(roots);
    const inside = new T.Vector3(0, 0, 0);
    // Clean misses past the box.
    expectParity(occlusion, roots, raycaster, new T.Vector3(6, 3, 5), new T.Vector3(0, 3, 0));
    expectParity(occlusion, roots, raycaster, new T.Vector3(6, 0, 5), new T.Vector3(0, 1.5, 0));
    // Endpoints exactly on the surface (segment ends at the entry face).
    expectParity(occlusion, roots, raycaster, new T.Vector3(6, 0, 5), new T.Vector3(0.5, 0, 0.25));
    // Endpoint behind the box, far beyond the far face: blocked (hit within segment).
    expectParity(occlusion, roots, raycaster, new T.Vector3(6, 0, 5), new T.Vector3(-4, 0, 0));
    // Zero-length segment never blocks.
    const point = new T.Vector3(0.2, 0, 0);
    expect(occlusion.blocked(point, point.clone())).toBe(false);
    expect(blockedByRaycaster(roots, raycaster, point, point.clone())).toBe(false);
    // No objects registered at all.
    expect(new StaticOcclusion().blocked(new T.Vector3(0, 0, 5), new T.Vector3(0, 0, 0))).toBe(false);
    occlusion.dispose();
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), inside)).toBe(false);
  });

  it('singular (zero-scale) transform never occludes', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial(), true);
    mesh.scale.set(0, 1, 1);
    mesh.updateWorldMatrix(true, false);
    occlusion.setObjects([mesh]);
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(false);
    occlusion.dispose();
  });

  it('rebuild after floating-origin rebase keeps parity', () => {
    const occlusion = new StaticOcclusion();
    const raycaster = new T.Raycaster();
    const material = new T.MeshBasicMaterial();
    const mesh = boxMesh(material, true);
    mesh.rotation.set(0, Math.PI / 6, 0);
    const parent = new T.Group();
    parent.add(mesh);
    parent.updateWorldMatrix(true, true);
    const roots = [parent];
    const origin = new T.Vector3();
    const at = (offset: T.Vector3) => origin.clone().add(offset);
    const rebase = (offset: T.Vector3) => {
      origin.add(offset);
      parent.position.add(offset);
      parent.updateWorldMatrix(true, false);
      occlusion.setObjects(roots); // rebuild triggered by the rebase
    };
    occlusion.setObjects(roots);
    const query=()=>{
      expect(occlusion.blocked(at(new T.Vector3(6,0,5)),at(new T.Vector3()))).toBe(true);
      expect(occlusion.blocked(at(new T.Vector3(6,2.5,5)),at(new T.Vector3(0,2.5,0)))).toBe(false);
    };
    query();
    sweepParity(occlusion, roots, raycaster, origin.clone());
    // Large floating-origin style rebase, then the same local query geometry.
    rebase(new T.Vector3(12000, 0, -8000));
    query();
    sweepParity(occlusion, roots, raycaster, origin.clone());
    // A second rebase in the opposite direction.
    rebase(new T.Vector3(-15000, 500, 9000));
    query();
    occlusion.dispose();
  });

  it('a multi-level object BVH agrees with Raycaster across a grid',()=>{
    const root=new T.Group(),geometry=new T.BoxGeometry(1,1,1),material=new T.MeshBasicMaterial();
    for(let i=0;i<64;i++){const mesh=new T.Mesh(geometry,material);mesh.position.set(i%8*3,0,Math.floor(i/8)*3);root.add(mesh);}
    root.updateMatrixWorld(true);const index=new StaticOcclusion();index.setObjects([root]);
    for(let i=0;i<64;i++){const to=new T.Vector3(i%8*3,0,Math.floor(i/8)*3);expectParity(index,[root],new T.Raycaster(),to.clone().add(new T.Vector3(0,4,0)),to);}
    expect(index.blocked(new T.Vector3(1.5,4,1.5),new T.Vector3(1.5,0,1.5))).toBe(false);index.dispose();
  });

  it('dispose clears occluders; reuse after dispose re-registers them', () => {
    const occlusion = new StaticOcclusion();
    const mesh = boxMesh(new T.MeshBasicMaterial(), true);
    const roots = [mesh];
    occlusion.setObjects(roots);
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(true);
    occlusion.dispose();
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(false);
    expect(occlusion.blocked(new T.Vector3(6, 3, 5), new T.Vector3(0, 3, 0))).toBe(false);
    occlusion.setObjects(roots); // reuse after dispose
    expect(occlusion.blocked(new T.Vector3(6, 0, 5), new T.Vector3(0, 0, 0))).toBe(true);
    occlusion.dispose();
  });
});
