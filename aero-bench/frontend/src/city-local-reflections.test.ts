// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { CityLocalReflections } from "./city-local-reflections";

function rendererStub(): THREE.WebGLRenderer {
  const priorTarget = new THREE.WebGLRenderTarget(4, 4);
  return {
    getRenderTarget: () => priorTarget,
    getActiveCubeFace: () => 2,
    getActiveMipmapLevel: () => 1,
    setRenderTarget: vi.fn(),
    xr: { enabled: true },
    autoClear: false,
    shadowMap: { autoUpdate: true },
  } as unknown as THREE.WebGLRenderer;
}

function placedBuilding(group: THREE.Group, material: THREE.Material | THREE.Material[], x: number):
  { visual: THREE.Group; mesh: THREE.Mesh } {
  const visual = new THREE.Group();
  visual.position.x = x;
  visual.userData.collisionBox = { building_id: `building-${x}` };
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 8, 2), material);
  visual.add(mesh);
  group.add(visual);
  return { visual, mesh };
}

beforeEach(() => {
  vi.spyOn(THREE.PMREMGenerator.prototype, "fromCubemap").mockImplementation((_texture, target) => {
    const result = target ?? new THREE.WebGLRenderTarget(768, 1024, { type: THREE.HalfFloatType });
    result.texture.mapping = THREE.CubeUVReflectionMapping;
    return result;
  });
});
afterEach(() => vi.restoreAllMocks());

describe("local street reflections", () => {
  it("publishes the fixed production probe limits used by the browser measurement", () => {
    expect(CityLocalReflections.profile).toEqual({
      probeSizePx: 256,
      reflectiveRadiusM: 75,
      captureFarM: 140,
      maxReflectiveBuildings: 12,
      cubeFacesPerRefresh: 6,
    });
    expect(Object.isFrozen(CityLocalReflections.profile)).toBe(true);
  });

  it("clones only marked city glass near the focus and leaves shared source materials under the mood setter", () => {
    const renderer = rendererStub(), scene = new THREE.Scene(), buildings = new THREE.Group();
    scene.add(buildings);
    const day = new THREE.Texture(), dusk = new THREE.Texture();
    const glass = new THREE.MeshPhysicalMaterial({ envMap: day, envMapIntensity: 1.15, clearcoat: 1 });
    glass.userData.glassFacade = true;
    const wall = new THREE.MeshStandardMaterial({ metalness: 0.8 });
    const falseGlass = new THREE.MeshStandardMaterial();
    falseGlass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const near = placedBuilding(buildings, [glass, wall], 8);
    const advertisingLight = new THREE.PointLight(0xffd27c, 15);
    near.visual.add(advertisingLight);
    const unrelated = placedBuilding(buildings, falseGlass, 12);
    const distant = placedBuilding(buildings, glass, 120);
    const visibleLightCount = (): number => {
      let count = 0;
      scene.traverseVisible(node => { if (node instanceof THREE.Light) count++; });
      return count;
    };
    expect(visibleLightCount()).toBe(1);
    const original = near.mesh.material;
    const reflections = new CityLocalReflections(renderer, scene);
    reflections.configure(buildings, new THREE.Vector3(), true);
    const reflected = near.mesh.material as THREE.Material[];
    const clone = reflected[0] as THREE.MeshStandardMaterial;
    expect(clone).not.toBe(glass);
    expect(reflected[1]).toBe(wall);
    expect(glass.envMap).toBe(day);
    expect(unrelated.mesh.material).toBe(falseGlass);
    expect(distant.mesh.material).toBe(glass);

    const capture = vi.spyOn(THREE.CubeCamera.prototype, "update").mockImplementation(function (this: THREE.CubeCamera) {
      expect(near.visual.visible).toBe(true);
      expect(near.mesh.material).toBe(original);
      expect(advertisingLight.visible).toBe(true);
      expect(visibleLightCount()).toBe(1);
      expect(unrelated.visual.visible).toBe(true);
      expect(distant.mesh.material).toBe(glass);
      expect(this.layers.mask).toBe(1);
      expect(this.position.toArray()).toEqual([0, 5, 0]);
      expect(renderer.autoClear).toBe(true);
      expect(renderer.shadowMap.autoUpdate).toBe(false);
    });
    expect(reflections.refresh()).toBe(true);
    expect(capture).toHaveBeenCalledTimes(1);
    expect(clone).toBeInstanceOf(THREE.MeshPhysicalMaterial);
    expect((clone as THREE.MeshPhysicalMaterial).clearcoat).toBe(1);
    expect(clone.envMap).toBeInstanceOf(THREE.Texture);
    expect(clone.envMap!.mapping).toBe(THREE.CubeUVReflectionMapping);
    expect(clone.envMap!.type).toBe(THREE.HalfFloatType);
    const localMap = clone.envMap;
    expect(near.mesh.material).toBe(reflected);
    expect(near.visual.visible).toBe(true);
    expect(visibleLightCount()).toBe(1);
    expect(reflections.refresh()).toBe(false);
    expect(capture).toHaveBeenCalledTimes(1);

    glass.envMap = dusk;
    glass.emissiveIntensity = 1.45;
    glass.envMapIntensity = 0.3;
    glass.color.setHex(0x587a9c);
    glass.emissive.setHex(0xfab35a);
    glass.roughness = 0.27;
    reflections.invalidate();
    expect(reflections.refresh()).toBe(true);
    expect(clone.envMap).toBe(localMap);
    expect(clone.emissiveIntensity).toBe(1.45);
    expect(clone.envMapIntensity).toBe(0.3);
    expect(clone.color.getHex()).toBe(0x587a9c);
    expect(clone.emissive.getHex()).toBe(0xfab35a);
    expect(clone.roughness).toBe(0.27);
    expect(glass.envMap).toBe(dusk);
    expect(capture).toHaveBeenCalledTimes(2);
    reflections.dispose();
    expect(near.mesh.material).toBe(original);
  });

  it("includes a nearby facade when its building centre lies outside the probe radius", () => {
    const buildings = new THREE.Group(), scene = new THREE.Scene();
    scene.add(buildings);
    const glass = new THREE.MeshPhysicalMaterial(); glass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const { mesh } = placedBuilding(buildings, glass, 100);
    mesh.scale.x = 40; // The near facade is at x=60; centre-only selection misses it.
    const reflections = new CityLocalReflections(rendererStub(), scene);
    reflections.configure(buildings, new THREE.Vector3(), true);
    expect(mesh.material).not.toBe(glass);
    reflections.dispose();
    expect(mesh.material).toBe(glass);
  });

  it("retries an unchanged enabled configuration after streamed facade materials register", () => {
    const buildings = new THREE.Group(), scene = new THREE.Scene();
    scene.add(buildings);
    const glass = new THREE.MeshPhysicalMaterial(); glass.userData.glassFacade = true;
    const { visual, mesh } = placedBuilding(buildings, glass, 4);
    const reflections = new CityLocalReflections(rendererStub(), scene);

    reflections.configure(buildings, new THREE.Vector3(), true);
    expect(reflections.selectedBuildings()).toEqual([]);
    expect(mesh.material).toBe(glass);

    buildings.userData.windowMaterials = [glass];
    reflections.configure(buildings, new THREE.Vector3(), true);
    expect(reflections.selectedBuildings()).toEqual([visual]);
    expect(mesh.material).not.toBe(glass);

    reflections.dispose();
    expect(mesh.material).toBe(glass);
  });

  it("caps the reflected building count and restores materials when disabled", () => {
    const buildings = new THREE.Group(), scene = new THREE.Scene();
    scene.add(buildings);
    const glass = new THREE.MeshStandardMaterial();
    glass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const meshes = Array.from({ length: 13 }, (_, index) => placedBuilding(buildings, glass, index * 5).mesh);
    const reflections = new CityLocalReflections(rendererStub(), scene);
    reflections.configure(buildings, new THREE.Vector3(), true);
    expect(meshes.slice(0, 12).every(mesh => mesh.material !== glass)).toBe(true);
    expect(meshes[12]!.material).toBe(glass);
    const disposeTarget = vi.spyOn(THREE.WebGLCubeRenderTarget.prototype, "dispose");
    const disposeSource = vi.spyOn(glass, "dispose");
    reflections.configure(buildings, new THREE.Vector3(), false);
    expect(meshes.every(mesh => mesh.material === glass)).toBe(true);
    expect(reflections.refresh()).toBe(false);
    expect(disposeTarget).toHaveBeenCalledTimes(1);
    expect(disposeSource).not.toHaveBeenCalled();
    reflections.dispose();
    expect(disposeTarget).toHaveBeenCalledTimes(1);
    expect(() => reflections.invalidate()).toThrow(/disposed/);
  });

  it("reuses owned HDR and filtered targets across camera relocation and frees them on disposal", () => {
    const buildings = new THREE.Group(), scene = new THREE.Scene(); scene.add(buildings);
    const glass = new THREE.MeshPhysicalMaterial(); glass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const {mesh} = placedBuilding(buildings, glass, 4);
    vi.spyOn(THREE.CubeCamera.prototype, "update").mockImplementation(() => undefined);
    const reflections = new CityLocalReflections(rendererStub(), scene);
    reflections.configure(buildings, new THREE.Vector3(), true); reflections.refresh();
    const texture = (mesh.material as THREE.MeshPhysicalMaterial).envMap;
    const filtered = vi.mocked(THREE.PMREMGenerator.prototype.fromCubemap).mock.results[0]!.value as THREE.WebGLRenderTarget;
    const dispose = vi.spyOn(filtered, "dispose");
    for (let x = 1; x <= 5; x++) {
      reflections.configure(buildings, new THREE.Vector3(x, 0, 0), true); reflections.refresh();
      expect((mesh.material as THREE.MeshPhysicalMaterial).envMap).toBe(texture);
      expect(THREE.PMREMGenerator.prototype.fromCubemap).toHaveBeenLastCalledWith(expect.any(THREE.CubeTexture), filtered);
    }
    expect(dispose).not.toHaveBeenCalled();
    reflections.dispose(); expect(dispose).toHaveBeenCalledOnce();
  });

  it("measures two distinct probes over non-overlapping facade sets without cross-sampling", () => {
    const renderer = rendererStub(), scene = new THREE.Scene(), buildings = new THREE.Group();
    scene.add(buildings);
    const glass = new THREE.MeshPhysicalMaterial(); glass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const firstBuilding = placedBuilding(buildings, glass, 0);
    const secondBuilding = placedBuilding(buildings, glass, 220);
    const first = new CityLocalReflections(renderer, scene);
    first.configure(buildings, new THREE.Vector3(), true);
    const firstSelection = first.selectedBuildings();
    expect(firstSelection).toEqual([firstBuilding.visual]);
    expect(Object.isFrozen(firstSelection)).toBe(true);
    const second = new CityLocalReflections(renderer, scene,
      { excludeBuildings: new Set(firstSelection) });
    second.configure(buildings, new THREE.Vector3(220, 0, 0), true);
    expect(second.selectedBuildings()).toEqual([secondBuilding.visual]);
    expect(second.selectedBuildings().some(building => firstSelection.includes(building))).toBe(false);

    let capture = 0;
    vi.spyOn(THREE.CubeCamera.prototype, "update").mockImplementation(function (this: THREE.CubeCamera) {
      capture++;
      expect(firstBuilding.mesh.material).toBe(glass);
      expect(secondBuilding.mesh.material).toBe(glass);
      expect(this.position.x).toBe(capture === 1 ? 0 : 220);
    });
    expect(first.refresh()).toBe(true);
    const firstEnhanced = firstBuilding.mesh.material;
    expect(second.refresh()).toBe(true);
    const secondEnhanced = secondBuilding.mesh.material;
    expect(firstBuilding.mesh.material).toBe(firstEnhanced);
    expect(secondBuilding.mesh.material).toBe(secondEnhanced);
    expect(capture).toBe(2);

    first.dispose();
    expect(firstBuilding.mesh.material).toBe(glass);
    expect(secondBuilding.mesh.material).toBe(secondEnhanced);
    second.invalidate();
    expect(second.refresh()).toBe(true);
    second.dispose();
    expect(secondBuilding.mesh.material).toBe(glass);
  });

  it("rejects overlapping probe ownership instead of stacking reflection clones", () => {
    const renderer = rendererStub(), scene = new THREE.Scene(), buildings = new THREE.Group();
    scene.add(buildings);
    const glass = new THREE.MeshPhysicalMaterial(); glass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const { mesh } = placedBuilding(buildings, glass, 0);
    const first = new CityLocalReflections(renderer, scene);
    first.configure(buildings, new THREE.Vector3(), true);
    const enhanced = mesh.material;
    const overlapping = new CityLocalReflections(renderer, scene);
    expect(() => overlapping.configure(buildings, new THREE.Vector3(), true)).toThrow(/already owned/);
    expect(mesh.material).toBe(enhanced);
    overlapping.dispose(); first.dispose();
    expect(mesh.material).toBe(glass);
  });

  it("restores renderer and scene state when a cube capture throws", () => {
    const renderer = rendererStub(), scene = new THREE.Scene(), buildings = new THREE.Group();
    scene.add(buildings);
    const glass = new THREE.MeshStandardMaterial();
    glass.userData.glassFacade = true;
    buildings.userData.windowMaterials = [glass];
    const { visual, mesh } = placedBuilding(buildings, glass, 4);
    const reflections = new CityLocalReflections(renderer, scene);
    reflections.configure(buildings, new THREE.Vector3(), true);
    const clone = mesh.material;
    const target = renderer.getRenderTarget();
    let cubeTarget: THREE.WebGLCubeRenderTarget | null = null;
    const capture = vi.spyOn(THREE.CubeCamera.prototype, "update").mockImplementation(function (this: THREE.CubeCamera) {
      expect(visual.visible).toBe(true);
      expect(mesh.material).toBe(glass);
      cubeTarget = this.renderTarget;
      this.renderTarget.texture.generateMipmaps = false;
      renderer.xr.enabled = false;
      throw new Error("GPU capture failed");
    });
    expect(() => reflections.refresh()).toThrow("GPU capture failed");
    expect(mesh.material).toBe(clone);
    expect(visual.visible).toBe(true);
    expect(renderer.autoClear).toBe(false);
    expect(renderer.shadowMap.autoUpdate).toBe(true);
    expect(renderer.xr.enabled).toBe(true);
    expect(renderer.setRenderTarget).toHaveBeenCalledWith(target, 2, 1);
    expect(cubeTarget!.texture.generateMipmaps).toBe(true);
    capture.mockImplementation(() => undefined);
    expect(reflections.refresh()).toBe(true);
    reflections.dispose();
  });
});
