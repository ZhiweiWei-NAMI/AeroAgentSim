import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { fitCityCharacter } from "./city-character";

describe("city character fit", () => {
  it("preserves a person's width and depth when fitting a T-pose by height", () => {
    const source = new THREE.Group();
    source.add(new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.8, 0.34)));
    const fitted = fitCityCharacter(source, 1.72);
    const bounds = new THREE.Box3().setFromObject(fitted);
    const size = bounds.getSize(new THREE.Vector3());
    expect(size.y).toBeCloseTo(1.72);
    expect(size.x / size.y).toBeCloseTo(1.4 / 1.8);
    expect(size.z / size.y).toBeCloseTo(0.34 / 1.8);
    expect(bounds.min.y).toBeCloseTo(0);
  });
});
