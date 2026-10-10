// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { removeAssetAmbientLights } from "./city-lighting";

describe("imported asset illumination", () => {
  it("does not multiply global fill when a model is cloned into a fleet", () => {
    const asset = new THREE.Group(), nested = new THREE.Group();
    const geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial();
    const body = new THREE.Mesh(geometry, material);
    const headlight = new THREE.SpotLight();
    nested.add(new THREE.AmbientLight(0x646464), new THREE.AmbientLight(0x646464), body, headlight);
    asset.add(nested, new THREE.AmbientLight(0x646464));
    removeAssetAmbientLights(asset);
    expect(body.parent).toBe(nested);
    expect(headlight.parent).toBe(nested);
    const city = new THREE.Scene(), fill = new THREE.AmbientLight(0xffffff, 0.04);
    city.add(fill);
    for (let index = 0; index < 30; index++) city.add(asset.clone(true));
    const globalFill: THREE.AmbientLight[] = [];
    city.traverse(node => { if (node instanceof THREE.AmbientLight) globalFill.push(node); });
    expect(globalFill).toEqual([fill]);
    expect(nested.children).toEqual([body, headlight]);
    geometry.dispose(); material.dispose();
  });
});
