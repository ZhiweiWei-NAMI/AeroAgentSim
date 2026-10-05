import * as THREE from "three";
import type { PlacementBox } from "./city-workspace-geometry";

/** Conservative world-space boxes of actual static assets, independent of visibility/LOD. */
export function cityStaticObstacles(trees: THREE.Group, roads: THREE.Group | null,
                                    traffic: THREE.Group | null): PlacementBox[] {
  const result: PlacementBox[] = [];
  const add = (bounds: THREE.Box3, id: string): void => {
    const size = bounds.getSize(new THREE.Vector3()), centre = bounds.getCenter(new THREE.Vector3());
    if (bounds.isEmpty() || Math.min(size.x, size.y, size.z) <= 0) throw new Error(`Empty static obstacle: ${id}`);
    result.push({ id, kind: "street_asset", x: centre.x, z: centre.z, widthM: size.x,
      depthM: size.z, heightM: size.y, baseY: bounds.min.y, rotationDeg: 0 });
  };
  trees.updateWorldMatrix(true, true);
  trees.children.forEach((tree, index) => add(new THREE.Box3().setFromObject(tree), `tree:${index}`));
  roads?.updateWorldMatrix(true, true);
  const local = new THREE.Matrix4(), world = new THREE.Matrix4();
  roads?.traverse(node => {
    if (!(node instanceof THREE.InstancedMesh) || !node.name.startsWith("street_light_8 ")) return;
    node.geometry.computeBoundingBox();
    for (let index = 0; index < node.count; index++) {
      node.getMatrixAt(index, local);
      world.multiplyMatrices(node.matrixWorld, local);
      add(node.geometry.boundingBox!.clone().applyMatrix4(world), `lamp:${index}:${node.name}`);
    }
  });
  traffic?.updateWorldMatrix(true, true);
  // Signal fixtures clone one template per location, so most meshes share their
  // BufferGeometry. Compute each distinct geometry's local box once; the per-mesh world
  // transform is still applied per mesh, so every box equals the per-vertex computation.
  const geometryBoxes = new Map<THREE.BufferGeometry, THREE.Box3>();
  traffic?.children.forEach(node => {
    if (node.userData.target?.kind !== "traffic_signal") return;
    node.traverse(part => {
      if (!(part instanceof THREE.Mesh)) return;
      const geometry: THREE.BufferGeometry = part.geometry;
      let box = geometryBoxes.get(geometry);
      if (box === undefined) {
        geometry.computeBoundingBox();
        box = geometry.boundingBox!;
        geometryBoxes.set(geometry, box);
      }
      add(box.clone().applyMatrix4(part.matrixWorld),
        `signal:${node.userData.target.id}:${part.name}`);
    });
  });
  return result;
}
