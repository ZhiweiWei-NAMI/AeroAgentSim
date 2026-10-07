import * as THREE from "three";
import { extractCityWindowGeometry, type NormalizedWindowRect } from "./city-window-geometry";

// Rectangles measured from the supplied atlas crops used by metered facades.
export function cityWindowRects(variant: number): NormalizedWindowRect[] {
  const rects: NormalizedWindowRect[] = [];
  const add = (x: number, y: number, w: number, h: number, tw: number, th: number): void => {
    if (x < 0 || y < 0 || x + w > tw || y + h > th) return;
    rects.push({ u0: x / tw, u1: (x + w) / tw, v0: 1 - (y + h) / th, v1: 1 - y / th });
  };
  if (variant === 0) {
    for (let row = 0; row < 4; row++) for (let col = 0; col < 4; col++) {
      add(6 + col * 47, 11 + row * 49, 27, 28, 188, 196);
    }
  } else if (variant === 1) {
    for (let row = 0; row < 4; row++) for (let col = 0; col < 4; col++) {
      add(4 + col * 19, 9 + row * 39, 14, 15, 76, 156);
    }
  } else if (variant === 2) {
    for (let row = 0; row < 4; row++) for (let col = 0; col < 4; col++) {
      add(3 + col * 33, 3 + row * 32, 29, 27, 140, 128);
    }
  } else throw new Error(`Unknown supplied facade variant ${variant}`);
  return rects;
}

/** Coated opaque architectural glass: reflection, without pretending an interior exists. */
export function createCityPaneMaterial(emissiveMap: THREE.Texture | null): THREE.MeshPhysicalMaterial {
  const material = new THREE.MeshPhysicalMaterial({
    name: "Separate coated architectural glass panes",
    color: 0x9caab5, roughness: .065, metalness: .65,
    ior: 1.52, clearcoat: 1, clearcoatRoughness: .07,
    emissive: 0xffffff, emissiveMap, emissiveIntensity: 0,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
  });
  material.userData.glassFacade = true;
  return material;
}

export function hasCityWindowWall(mesh: THREE.Mesh, facade: THREE.Material): boolean {
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  return mesh.geometry.groups.some(group => group.count > 0
    && group.materialIndex !== undefined && materials[group.materialIndex] === facade);
}

/** Two extra draw calls per nearby wall mesh; distant buildings retain the atlas. */
export function attachCityWindowDetail(mesh: THREE.Mesh, variant: number,
                                       pane: THREE.MeshPhysicalMaterial,
                                       frame: THREE.MeshStandardMaterial): number {
  const detail = extractCityWindowGeometry(mesh.geometry, [0], cityWindowRects(variant), true,
    { paneOffsetM: 0, frameOutwardOffsetM: 0, frameDepthM: 0 });
  if (!detail.windowCount) { detail.panes.dispose(); detail.frames.dispose(); return 0; }
  const group = new THREE.Group();
  const panes = new THREE.Mesh(detail.panes, pane);
  panes.name = "Independent reflecting window panes";
  panes.receiveShadow = true;
  panes.raycast = () => undefined;
  const frames = new THREE.Mesh(detail.frames, frame);
  frames.name = "Metal window frames and reveals";
  frames.receiveShadow = true;
  frames.raycast = () => undefined;
  group.add(panes, frames);
  const lod = new THREE.LOD();
  lod.name = "Window detail within 180 metres";
  lod.addLevel(group, 0);
  lod.addLevel(new THREE.Group(), 180, .1);
  mesh.add(lod);
  return detail.windowCount;
}
