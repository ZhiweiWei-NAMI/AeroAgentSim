import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { attachMeteredRoofs, createMeteredBuilding, meteredFacadeGeometry, needsMeteredFacadePart, roofParts,
  syncMeteredRoofVisibility } from "./city-building-facade";

describe("metre-scaled supplied building facades", () => {
  it("keeps glass curtain walls free of masonry relief and low walls inside their envelope", () => {
    const glass = meteredFacadeGeometry(18, 14, 21, false);
    expect(glass.getAttribute("position").count).toBe(24);
    expect(glass.groups.some(group => group.materialIndex === 1)).toBe(false);
    const low = meteredFacadeGeometry(18, 14, .8);
    low.computeBoundingBox();
    expect(low.boundingBox!.max.y).toBeLessThanOrEqual(.4 + 1e-6);
    expect(low.boundingBox!.min.y).toBeGreaterThanOrEqual(-.4 - 1e-6);
    glass.dispose(); low.dispose();
  });
  it("keeps thin OSM parts instead of fitting a whole tower into them", () => {
    expect(needsMeteredFacadePart(29.56, 2.36)).toBe(true);
    expect(needsMeteredFacadePart(.56, .36)).toBe(true);
    expect(needsMeteredFacadePart(25, 18)).toBe(false);
  });
  it("maintains a three metre window bay and 3.5 metre floor with four windows per tile", () => {
    const geometry = meteredFacadeGeometry(24, 12, 28);
    const uv = geometry.getAttribute("uv");
    expect(uv.getX(16)).toBeCloseTo(0);
    expect(uv.getX(17)).toBeCloseTo(2);
    expect(uv.getY(16)).toBeCloseTo(2);
    expect(geometry.groups.length).toBeLessThanOrEqual(3);
    geometry.dispose();
  });
  it("keeps facade relief inside the OSM envelope and batches all triangles into three material groups", () => {
    const geometry = meteredFacadeGeometry(18, 14, 17);
    geometry.computeBoundingBox();
    expect(geometry.boundingBox!.min.toArray()).toEqual([-9, -8.5, -7]);
    expect(geometry.boundingBox!.max.toArray()).toEqual([9, 8.5, 7]);
    const indexCount = geometry.getIndex()!.count;
    let nextIndex = 0;
    for (const group of geometry.groups) {
      expect(group.start).toBe(nextIndex);
      expect(group.count % 3).toBe(0);
      nextIndex += group.count;
    }
    expect(nextIndex).toBe(indexCount);
    expect(geometry.groups.length).toBeLessThanOrEqual(3);
    const uv = geometry.getAttribute("uv");
    for (let index = 0; index < uv.count; index++) {
      expect(Number.isFinite(uv.getX(index))).toBe(true);
      expect(Number.isFinite(uv.getY(index))).toBe(true);
    }
    const detailU = Array.from({ length: 4 }, (_, index) => uv.getX(24 + index));
    const detailV = Array.from({ length: 4 }, (_, index) => uv.getY(24 + index));
    expect(Math.max(...detailU) - Math.min(...detailU)).toBeCloseTo(.12 / 12);
    expect(Math.max(...detailV) - Math.min(...detailV)).toBeCloseTo(17 / 14);
    geometry.dispose();
  });
  it("keeps sub-two metre façade parts inside their measured box", () => {
    const geometry = meteredFacadeGeometry(.56, .36, 12);
    geometry.computeBoundingBox();
    expect(geometry.boundingBox!.min.x).toBeCloseTo(-.28);
    expect(geometry.boundingBox!.min.y).toBeCloseTo(-6);
    expect(geometry.boundingBox!.min.z).toBeCloseTo(-.18);
    expect(geometry.boundingBox!.max.x).toBeCloseTo(.28);
    expect(geometry.boundingBox!.max.y).toBeCloseTo(6);
    expect(geometry.boundingBox!.max.z).toBeCloseTo(.18);
    const indexCount = geometry.getIndex()!.count;
    expect(geometry.groups.reduce((end, group) => {
      expect(group.start).toBe(end);
      expect(group.count % 3).toBe(0);
      return end + group.count;
    }, 0)).toBe(indexCount);
    geometry.dispose();
  });
  it("keeps roof equipment and parapets inside every declared width, depth and height", () => {
    for (const [w, d, h] of [[18, 14, 21], [29.56, 2.36, 105], [.56, .36, 12], [8, 8, 8]]) {
      const plan = roofParts(w!, d!, h!);
      for (const item of plan.parts) {
        expect(Math.abs(item.position[0]) + item.size[0] / 2).toBeLessThanOrEqual(w! / 2 + 1e-8);
        expect(Math.abs(item.position[2]) + item.size[2] / 2).toBeLessThanOrEqual(d! / 2 + 1e-8);
        expect(item.position[1] + item.size[1] / 2).toBeLessThanOrEqual(h! + 1e-8);
        expect(item.position[1] - item.size[1] / 2).toBeGreaterThanOrEqual(plan.wallHeight - 1e-8);
      }
      const model = createMeteredBuilding(new THREE.Vector3(w, h, d), Array.from({ length: 5 }, () => new THREE.MeshStandardMaterial()));
      const box = new THREE.Box3().setFromObject(model);
      expect(box.min.y).toBeCloseTo(0);
      expect(box.max.y).toBeLessThanOrEqual(h! + 1e-5);
      expect(box.max.x - box.min.x).toBeCloseTo(w!);
      expect(box.max.z - box.min.z).toBeCloseTo(d!);
    }
  });
  it("batches roof details while retaining each building's rotated placement and visibility", () => {
    const city = new THREE.Group();
    const materials = Array.from({ length: 5 }, () => new THREE.MeshStandardMaterial());
    const first = createMeteredBuilding(new THREE.Vector3(18, 21, 14), materials, true);
    first.position.set(50, 4, 20); first.rotation.y = Math.PI / 2;
    const second = createMeteredBuilding(new THREE.Vector3(18, 21, 14), materials, true);
    second.position.x = -50;
    city.add(first, second);
    attachMeteredRoofs(city);
    const batches = city.children.filter(node => node instanceof THREE.InstancedMesh) as THREE.InstancedMesh[];
    expect(batches).toHaveLength(3);
    const matrix = new THREE.Matrix4();
    batches[0]!.getMatrixAt(0, matrix);
    expect(new THREE.Vector3().setFromMatrixPosition(matrix).x).toBeCloseTo(43.12);
    first.visible = false;
    syncMeteredRoofVisibility(city);
    batches[0]!.getMatrixAt(0, matrix);
    expect(matrix.determinant()).toBe(0);
    batches[0]!.getMatrixAt(4, matrix);
    expect(matrix.determinant()).toBeGreaterThan(0);
  });
  it("varies rooftop service groups by stable building identity without crossing the OSM envelope", () => {
    const layouts = [0, 1, 2].map(seed => roofParts(18, 14, 21, seed));
    const equipmentLayouts = layouts.map(layout => JSON.stringify(layout.parts.filter(part => part.kind !== "rim")));
    expect(new Set(equipmentLayouts).size).toBe(3);
    for (const { wallHeight, parts } of layouts) {
      for (const item of parts) {
        expect(Math.abs(item.position[0]) + item.size[0] / 2).toBeLessThanOrEqual(9 + 1e-8);
        expect(Math.abs(item.position[2]) + item.size[2] / 2).toBeLessThanOrEqual(7 + 1e-8);
        expect(item.position[1] + item.size[1] / 2).toBeLessThanOrEqual(21 + 1e-8);
        expect(item.position[1] - item.size[1] / 2).toBeGreaterThanOrEqual(wallHeight - 1e-8);
      }
      const equipment = parts.filter(part => part.kind === "plant" || part.kind === "duct");
      for (let first = 0; first < equipment.length; first++) for (let second = first + 1; second < equipment.length; second++) {
        const a = equipment[first]!;
        const b = equipment[second]!;
        const overlaps = [0, 1, 2].every(axis => Math.abs(a.position[axis]! - b.position[axis]!) < (a.size[axis]! + b.size[axis]!) / 2 - 1e-8);
        expect(overlaps).toBe(false);
      }
    }
  });
});
