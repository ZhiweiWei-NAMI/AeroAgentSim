// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { meshGeometry, meshIdentity } from "./mesh";
import type { O2WMesh } from "./runtime";

function mesh(): O2WMesh {
  return {
    positions: () => [0, 0, 0, 1, 0, 0, 0, 0, 1],
    normals: () => [0, 1, 0, 0, 1, 0, 0, 1, 0],
    indices: () => [0, 1, 2], uvs: () => [0, 0, 1, 0, 0, 1],
    color: () => [1, 1, 1], baseColorTexture: () => null,
    opacityTexture: () => null, normalTexture: () => null, ormTexture: () => null,
    transparency: () => false, clampTextures: () => false,
    elementId: () => "w17", modelClass: () => "Building", materialName: () => "CONCRETE",
  };
}

describe("official mesh coordinate conversion", () => {
  it("preserves outward roof lighting when converting clockwise left-handed faces", () => {
    const geometry = meshGeometry(mesh());
    const position = geometry.getAttribute("position");
    const a = new THREE.Vector3().fromBufferAttribute(position, 0);
    const b = new THREE.Vector3().fromBufferAttribute(position, 1);
    const c = new THREE.Vector3().fromBufferAttribute(position, 2);
    const normal = new THREE.Vector3().fromBufferAttribute(geometry.getAttribute("normal"), 0);
    expect(b.sub(a).cross(c.sub(a)).dot(normal)).toBeGreaterThan(0);
    geometry.dispose();
  });

  it("uses explicit OSM IDs rather than material colors to identify buildings", () => {
    const identity = meshIdentity(mesh(), new Map([["w17", { type: "way", id: 17, nodes: [1, 2, 3, 1], tags: { building: "yes", "aero:building_id": "building.01" } }]]));
    expect(identity).toEqual({ layer: "buildings", target: { kind: "building", id: "building.01" } });
  });
});
