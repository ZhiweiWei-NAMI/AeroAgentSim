// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { meteredFacadeGeometry } from "./city-building-facade";
import { attachCityWindowDetail, cityWindowRects, createCityPaneMaterial, hasCityWindowWall } from "./city-window-detail";

describe("supplied facade window detail", () => {
  it("selects only building parts with an actual metered wall group", () => {
    const facade = new THREE.MeshStandardMaterial();
    const stone = new THREE.MeshStandardMaterial();
    const roof = new THREE.MeshStandardMaterial();
    const narrow = new THREE.Mesh(meteredFacadeGeometry(2, 2, 20, false), [facade, stone, roof]);
    const broad = new THREE.Mesh(meteredFacadeGeometry(24, 24, 20, true), [facade, stone, roof]);
    expect(narrow.geometry.groups.some(group => group.materialIndex === 0)).toBe(false);
    expect(hasCityWindowWall(narrow, facade)).toBe(false);
    expect(hasCityWindowWall(broad, facade)).toBe(true);
    narrow.geometry.dispose(); broad.geometry.dispose();
    facade.dispose(); stone.dispose(); roof.dispose();
  });

  it("places independent glass on each metered variant with distance-limited frames", () => {
    for (const variant of [0, 1, 2]) {
      const mesh = new THREE.Mesh(meteredFacadeGeometry(24, 24, 28, variant !== 2));
      const emission = new THREE.Texture();
      const glass = createCityPaneMaterial(emission);
      const frame = new THREE.MeshStandardMaterial();
      const count = attachCityWindowDetail(mesh, variant, glass, frame);
      expect(count).toBeGreaterThan(100);
      const lod = mesh.children[0] as THREE.LOD;
      expect(lod).toBeInstanceOf(THREE.LOD);
      expect(lod.levels[1]!.distance).toBe(180);
      const pane = lod.levels[0]!.object.children[0] as THREE.Mesh;
      expect(pane.material).toBe(glass);
      const wallBounds = new THREE.Box3().setFromBufferAttribute(mesh.geometry.getAttribute("position") as THREE.BufferAttribute);
      for (const child of lod.levels[0]!.object.children as THREE.Mesh[]) {
        child.geometry.computeBoundingBox();
        expect(wallBounds.clone().expandByScalar(1e-5).containsBox(child.geometry.boundingBox!)).toBe(true);
        const hits: THREE.Intersection[] = [];
        child.raycast(new THREE.Raycaster(), hits); expect(hits).toEqual([]);
      }
      expect(glass.polygonOffset).toBe(true);
      expect(glass.emissiveMap).toBe(emission);
      expect(glass.transparent).toBe(false);
      const camera = new THREE.PerspectiveCamera();
      camera.position.set(0, 0, 300); camera.updateMatrixWorld();
      lod.update(camera);
      expect(lod.levels[0]!.object.visible).toBe(false);
      camera.position.set(0, 0, 20); camera.updateMatrixWorld(); lod.update(camera);
      expect(lod.levels[0]!.object.visible).toBe(true);
    }
  });
  it("keeps the pane rectangles inside their source atlas tile", () => {
    for (const variant of [0, 1, 2]) for (const r of cityWindowRects(variant)) {
      expect(r.u0).toBeGreaterThanOrEqual(0); expect(r.v0).toBeGreaterThanOrEqual(0);
      expect(r.u1).toBeLessThanOrEqual(1); expect(r.v1).toBeLessThanOrEqual(1);
    }
    expect(() => cityWindowRects(3)).toThrow();
  });
});
