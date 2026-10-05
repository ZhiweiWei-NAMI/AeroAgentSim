// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { entityVisualSpec, fitEntityVisual } from "./entity-visuals";

describe("entity viewer models", () => {
  it("uses the declared X500 model and centers its one-metre presentation bounds", () => {
    const spec = entityVisualSpec("uav", "asset.model-uav");
    expect(spec?.path).toBe("/models/city-runtime/holybro-x500-textured-preview.glb");
    const source = new THREE.Group();
    source.add(new THREE.Mesh(new THREE.BoxGeometry(0.56, 0.286, 0.56)));
    const model = fitEntityVisual(source, spec!);
    const bounds = new THREE.Box3().setFromObject(model);
    expect(bounds.getSize(new THREE.Vector3()).toArray()).toEqual([1, 0.35, 1]);
    expect(bounds.getCenter(new THREE.Vector3()).length()).toBeLessThan(1e-9);
  });

  it("uses the textured passenger car and places its wheels at ground level", () => {
    const spec = entityVisualSpec("ugv", "asset.model-vehicle");
    expect(spec?.path).toBe("/models/city-runtime/Car_6-preview.glb");
    const source = new THREE.Group();
    source.add(new THREE.Mesh(new THREE.BoxGeometry(1.8, 1.36, 4)));
    const model = fitEntityVisual(source, spec!);
    const bounds = new THREE.Box3().setFromObject(model);
    const size = bounds.getSize(new THREE.Vector3());
    expect(size.x).toBeCloseTo(4.5);
    expect(size.y).toBeCloseTo(1.5);
    expect(size.z).toBeCloseTo(1.8);
    expect(bounds.min.y).toBeCloseTo(0);
  });

  it("uses the supplied citizen model for a declared pedestrian", () => {
    const spec = entityVisualSpec("pedestrian", "asset.model-pedestrian");
    expect(spec?.path).toBe("/models/city-runtime/casual27_m_highpoly_walk.glb");
    const source = new THREE.Group();
    source.add(new THREE.Mesh(new THREE.BoxGeometry(0.5, 1.6, 0.4)));
    const model = fitEntityVisual(source, spec!);
    const bounds = new THREE.Box3().setFromObject(model);
    expect(bounds.min.y).toBeCloseTo(0);
    const size = bounds.getSize(new THREE.Vector3());
    expect(size.x).toBeCloseTo(0.5 * 1.72 / 1.6);
    expect(size.y).toBeCloseTo(1.72);
    expect(size.z).toBeCloseTo(0.4 * 1.72 / 1.6);
  });

  it("rejects model identifiers without a viewer visual", () => {
    expect(() => entityVisualSpec("ugv", "asset.unknown")).toThrow("No viewer model");
    expect(() => entityVisualSpec("pedestrian", "asset.model-person")).toThrow("No viewer model");
  });
});
