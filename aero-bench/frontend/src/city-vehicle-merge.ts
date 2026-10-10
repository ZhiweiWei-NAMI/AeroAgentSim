import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

interface SurfacePart { mesh: THREE.Mesh; start: number; count: number }

/** Merge static visible surfaces in template space; materials remain shared with lamp controls. */
export function mergeVehicleTemplateMeshes(root: THREE.Group,
  retainedNames: ReadonlySet<string> = new Set()): void {
  root.updateWorldMatrix(true, true);
  const inverse = root.matrixWorld.clone().invert();
  const animated = new Set<THREE.Object3D>();
  root.traverse(node => {
    for (const clip of node.animations) for (const track of clip.tracks) {
      const binding = THREE.PropertyBinding.parseTrackName(track.name);
      const target = THREE.PropertyBinding.findNode(root, binding.nodeName);
      if (target instanceof THREE.Object3D) target.traverse(child => animated.add(child));
    }
  });
  const buckets = new Map<THREE.Material, Map<string, SurfacePart[]>>();
  root.traverseVisible(node => {
    if (!(node instanceof THREE.Mesh) || node instanceof THREE.SkinnedMesh
        || node instanceof THREE.InstancedMesh || animated.has(node) || retainedNames.has(node.name)
        || Object.keys(node.geometry.morphAttributes).length > 0
        || node.geometry.drawRange.start !== 0 || node.geometry.drawRange.count !== Infinity) return;
    const geometry: THREE.BufferGeometry = node.geometry;
    const count = geometry.index?.count ?? geometry.getAttribute("position").count;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    // Mirrored parts need original winding/front-face handling; transparent parts
    // need original per-object depth sorting. Keep those meshes intact.
    if (inverse.clone().multiply(node.matrixWorld).determinant() <= 0) return;
    const groups = Array.isArray(node.material) ? geometry.groups
      : [{ start: 0, count, materialIndex: 0 }];
    // Only flatten material groups that partition the entire triangle stream.
    // Partial, overlapping or absent groups retain their original render behavior.
    let end = 0;
    for (const group of [...groups].sort((a, b) => a.start - b.start)) {
      if (group.start !== end || group.count <= 0 || group.count % 3 !== 0
          || materials[group.materialIndex ?? 0] === undefined) return;
      end += group.count;
    }
    if (end !== count || groups.some(group => materials[group.materialIndex ?? 0]!.transparent)) return;
    const layout = Object.keys(geometry.attributes).sort().map(name => {
      const attribute = geometry.getAttribute(name);
      return [name, attribute.itemSize, attribute.normalized, attribute.array.constructor.name,
        attribute instanceof THREE.BufferAttribute ? attribute.gpuType : null];
    });
    const key = JSON.stringify([node.castShadow, node.receiveShadow, node.layers.mask,
      node.renderOrder, node.frustumCulled, layout]);
    for (const group of groups) {
      const material = materials[group.materialIndex ?? 0]!;
      let byState = buckets.get(material);
      if (byState === undefined) { byState = new Map(); buckets.set(material, byState); }
      const parts = byState.get(key) ?? [];
      parts.push({ mesh: node, start: group.start, count: group.count }); byState.set(key, parts);
    }
  });
  const replaced = new Set<THREE.Mesh>();
  for (const [material, byState] of buckets) for (const parts of byState.values()) {
    const first = parts[0]!.mesh;
    if (parts.length < 2 && !Array.isArray(first.material)) continue;
    const fragments = parts.map(({ mesh, start, count }) => {
      const geometry = mesh.geometry.index === null ? mesh.geometry.clone() : mesh.geometry.toNonIndexed();
      // Preserve every source attribute and each group's exact triangle/UV assignment.
      if (Array.isArray(mesh.material)) for (const name of Object.keys(geometry.attributes)) {
        const attribute = geometry.getAttribute(name) as THREE.BufferAttribute;
        const sliced = new THREE.BufferAttribute(attribute.array.slice(start * attribute.itemSize,
          (start + count) * attribute.itemSize), attribute.itemSize, attribute.normalized);
        sliced.gpuType = attribute.gpuType;
        geometry.setAttribute(name, sliced);
      }
      geometry.clearGroups();
      return geometry.applyMatrix4(inverse.clone().multiply(mesh.matrixWorld));
    });
    const geometry = mergeGeometries(fragments);
    for (const fragment of fragments) fragment.dispose();
    if (geometry === null) throw new Error(`Vehicle template geometry cannot be merged: ${material.name}`);
    const merged = new THREE.Mesh(geometry, material);
    merged.name = `Merged vehicle ${material.name}`;
    merged.castShadow = first.castShadow; merged.receiveShadow = first.receiveShadow;
    merged.layers.mask = first.layers.mask; merged.renderOrder = first.renderOrder;
    merged.frustumCulled = first.frustumCulled;
    // Merged surfaces sit at the template origin and never move relative to it.
    merged.matrixAutoUpdate = false;
    root.add(merged);
    for (const part of parts) replaced.add(part.mesh);
  }
  for (const mesh of replaced) {
    // Keep import metadata and child transforms in their original hierarchy. A replaced mesh
    // with neither leaves no node: each clone would recompute its matrix every frame for nothing.
    // Source geometries may still be shared by other templates; never dispose them here.
    if (mesh.children.length === 0 && Object.keys(mesh.userData).length === 0) { mesh.removeFromParent(); continue; }
    const metadata = new THREE.Group();
    metadata.copy(mesh, false); metadata.userData = mesh.userData;
    if (mesh.children.length > 0) metadata.add(...mesh.children.slice());
    const parent = mesh.parent!;
    const index = parent.children.indexOf(mesh);
    parent.remove(mesh); parent.add(metadata);
    parent.children.splice(parent.children.indexOf(metadata), 1);
    parent.children.splice(index, 0, metadata);
  }
  root.updateWorldMatrix(true, true);
}
