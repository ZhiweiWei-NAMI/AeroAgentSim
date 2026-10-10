// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { createFinishMaterial, defaultFinishSettings } from "./material-finishes";

describe("generic preview finishes", () => {
  it("keeps color maps in sRGB and shares the ORM channels for roughness and metalness", async () => {
    const loaded: Record<string, THREE.Texture> = {};
    const loader = {
      async loadAsync(url: string) {
        const texture = new THREE.Texture();
        loaded[url] = texture;
        return texture;
      },
    } as THREE.TextureLoader;
    const settings = defaultFinishSettings({ id: "cc0textures:Metal002", label: "Metal002", family: "cc0textures" });
    const material = await createFinishMaterial({
      id: "cc0textures:Metal002", label: "Metal002", family: "cc0textures",
      color_url: "/color.jpg", normal_url: "/normal.jpg", orm_url: "/orm.jpg",
    }, { ...settings, repeat: 3 }, loader);
    expect(material.map).toBe(loaded["/color.jpg"]);
    expect(material.map?.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(material.normalMap).toBe(loaded["/normal.jpg"]);
    expect(material.roughnessMap).toBe(loaded["/orm.jpg"]);
    expect(material.metalnessMap).toBe(loaded["/orm.jpg"]);
    expect(material.map?.repeat.toArray()).toEqual([3, 3]);
    expect(material.map?.wrapS).toBe(THREE.RepeatWrapping);
    for (const texture of Object.values(loaded)) texture.dispose();
    material.dispose();
  });
});
