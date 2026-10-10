import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { setCityAdvertisingLighting } from "./city-advertising";

describe("advertising lighting", () => {
  it("keeps the twilight output and gives night a separate intensity without changing inventory", () => {
    const buildings = new THREE.Group(), sign = new THREE.Group();
    const light = new THREE.PointLight(0xffffff, 4);
    const other = new THREE.PointLight(0xffffff, 10);
    light.userData.cityAdvertisingLight = true;
    sign.add(light); buildings.add(sign, other);
    for (const [timeOfDay, intensity] of [["day", 0], ["twilight", 4], ["night", 5.2], ["day", 0]] as const) {
      setCityAdvertisingLighting(buildings, timeOfDay);
      expect(light.intensity).toBe(intensity);
      expect(other.intensity).toBe(10);
      const visible: THREE.Object3D[] = [];
      buildings.traverseVisible(node => { if (node instanceof THREE.Light) visible.push(node); });
      expect(visible).toEqual([light, other]);
    }
  });
});
