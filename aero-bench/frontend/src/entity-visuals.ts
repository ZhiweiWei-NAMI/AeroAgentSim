import * as THREE from "three";
import { createSharedCityGltfLoader } from "./city-asset-cache";
import { cacheStaticTransforms } from "./city-rendering";
import type { EntityKind } from "./state/layers";
import { fitCityCharacter } from "./city-character";

interface EntityVisualSpec {
  readonly path: string;
  readonly size: readonly [number, number, number];
  readonly rotationY: number;
  readonly groundAligned: boolean;
  readonly fitByHeight?: boolean;
}

const modelSpecs: Readonly<Record<string, EntityVisualSpec>> = {
  "asset.model-uav": {
    path: "/models/city-runtime/holybro-x500-textured-preview.glb",
    size: [1, 0.35, 1],
    rotationY: 0,
    groundAligned: false,
  },
  "asset.model-vehicle": {
    path: "/models/city-runtime/Car_6-preview.glb",
    size: [4.5, 1.5, 1.8],
    rotationY: Math.PI / 2,
    groundAligned: true,
  },
  "asset.model-pedestrian": {
    path: "/models/city-runtime/casual27_m_highpoly_walk.glb",
    size: [0.6, 1.72, 0.42],
    rotationY: 0,
    groundAligned: true,
    fitByHeight: true,
  },
};

const loader = createSharedCityGltfLoader();

export function entityVisualSpec(kind: EntityKind, modelAssetId: string | null): EntityVisualSpec | null {
  if (kind !== "uav" && kind !== "ugv" && kind !== "pedestrian") return null;
  if (modelAssetId === null) throw new Error(`No viewer model asset declared for ${kind}`);
  const spec = modelSpecs[modelAssetId];
  if (spec === undefined) throw new Error(`No viewer model for ${modelAssetId}`);
  return spec;
}

export function fitEntityVisual(scene: THREE.Group, spec: EntityVisualSpec): THREE.Group {
  if (spec.fitByHeight) {
    const character = fitCityCharacter(scene, spec.size[1]);
    character.rotation.y = spec.rotationY;
    return character;
  }
  const mount = new THREE.Group();
  const rotated = new THREE.Group();
  rotated.rotation.y = spec.rotationY;
  rotated.add(scene);
  mount.add(rotated);
  mount.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(mount);
  if (bounds.isEmpty()) throw new Error(`Viewer model has no geometry: ${spec.path}`);
  const sourceSize = bounds.getSize(new THREE.Vector3());
  if (sourceSize.x <= 0 || sourceSize.y <= 0 || sourceSize.z <= 0) {
    throw new Error(`Viewer model has invalid dimensions: ${spec.path}`);
  }
  mount.scale.set(spec.size[0] / sourceSize.x, spec.size[1] / sourceSize.y, spec.size[2] / sourceSize.z);
  mount.updateMatrixWorld(true);
  bounds.setFromObject(mount);
  const center = bounds.getCenter(new THREE.Vector3());
  mount.position.set(-center.x, spec.groundAligned ? -bounds.min.y : -center.y, -center.z);
  // The mounted chain is final once fitted; the caller moves only the entity root.
  cacheStaticTransforms(mount);
  mount.traverse(object => { if (object instanceof THREE.Mesh) { object.castShadow = true; object.receiveShadow = true; } });
  return mount;
}

export async function loadEntityVisual(kind: EntityKind, modelAssetId: string | null): Promise<THREE.Group | null> {
  const spec = entityVisualSpec(kind, modelAssetId);
  if (spec === null) return null;
  const gltf = await loader.loadAsync(spec.path);
  return fitEntityVisual(gltf.scene, spec);
}
