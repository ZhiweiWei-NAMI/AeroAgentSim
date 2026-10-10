import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { clone as cloneSkeleton } from "three/addons/utils/SkeletonUtils.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { TrafficShadowCasters } from "./city-traffic-shadow";
import { DEPTH_FIELDS, type DepthSource } from "./city-shadow-pass";
import { WebGLShadowMap } from "three/src/renderers/webgl/WebGLShadowMap.js";
import { mergeVehicleTemplateMeshes } from "./city-vehicle-merge";
import { CityTrafficPreview, collisionBox, prepareCityVehicleTemplate } from "./city-presentation";
import { visiblePickObjects } from "./city-rendering";

const assets = {
  sedan: "/models/city-runtime/Car_6-preview.glb",
  taxi: "/models/city-runtime/Taxi_1-day-preview.glb",
  police: "/models/city-runtime/Police_1-day-preview.glb",
  bus: "/models/incoming/urban-traffic/fbx/0c0ace7261ddca04ea015e96dfcfbfee.fbx",
  truck: "/models/incoming/urban-traffic/fbx/d85819a07026b954c8699700056d1aec.fbx",
} as const;
type MotorType = keyof typeof assets;
const originals = new Map<MotorType, THREE.Group>();
const mergedTemplates = new Map<MotorType, THREE.Group>();
const TOLERANCE = 1e-6;
let bicycleTemplate: THREE.Group;

/** A WebGL2 fence that is already signalled; `onSignalled` records when the warm-up waited on it. */
function signalledGl(onSignalled: () => void = () => {}) {
  return { SYNC_GPU_COMMANDS_COMPLETE: 0x9117, SYNC_STATUS: 0x9114, SIGNALED: 0x9119,
    fenceSync: () => ({}), flush() {}, isContextLost: () => false, deleteSync() {},
    getSyncParameter() { onSignalled(); return 0x9119; } };
}

function meshes(root: THREE.Object3D): THREE.Mesh[] {
  const result: THREE.Mesh[] = [];
  root.traverseVisible(node => {
    if (node instanceof THREE.Mesh && node.geometry.getAttribute("position").count > 0) result.push(node);
  });
  return result;
}

function drawCalls(root: THREE.Object3D, shadow = false): number {
  return meshes(root).filter(mesh => !shadow || mesh.castShadow).reduce((total, mesh) => total
    + (Array.isArray(mesh.material) ? mesh.geometry.groups.filter(group =>
      (mesh.material as THREE.Material[])[group.materialIndex!]!.visible).length
      : Number(mesh.material.visible)), 0);
}

function materials(root: THREE.Object3D): Set<THREE.Material> {
  return new Set(meshes(root).flatMap(mesh => Array.isArray(mesh.material)
    ? mesh.geometry.groups.map(group => (mesh.material as THREE.Material[])[group.materialIndex!]!)
    : [mesh.material]));
}

// Compare triangle-corner multisets, including material object identity, shadow flags,
// world position/normal, UVs, tangents and vertex colors. Indices may be expanded by merging.
function surfaces(root: THREE.Object3D): Map<THREE.Material, Map<string, number[][]>> {
  root.updateWorldMatrix(true, true);
  const result = new Map<THREE.Material, Map<string, number[][]>>();
  for (const mesh of meshes(root)) {
    const geometry = mesh.geometry, position = geometry.getAttribute("position");
    const normal = geometry.getAttribute("normal");
    const normalMatrix = new THREE.Matrix3().getNormalMatrix(mesh.matrixWorld);
    const groups = Array.isArray(mesh.material) ? geometry.groups
      : [{ start: 0, count: geometry.index?.count ?? position.count, materialIndex: 0 }];
    for (const group of groups) {
      const material = Array.isArray(mesh.material) ? mesh.material[group.materialIndex!]! : mesh.material;
      let states = result.get(material);
      if (states === undefined) { states = new Map(); result.set(material, states); }
      const state = JSON.stringify([mesh.castShadow, mesh.receiveShadow, mesh.layers.mask, mesh.renderOrder]);
      const corners = states.get(state) ?? []; states.set(state, corners);
      for (let offset = group.start; offset < group.start + group.count; offset++) {
        const index = geometry.index?.getX(offset) ?? offset;
        const p = new THREE.Vector3().fromBufferAttribute(position, index).applyMatrix4(mesh.matrixWorld);
        const n = new THREE.Vector3().fromBufferAttribute(normal, index).applyNormalMatrix(normalMatrix);
        const corner = [...p.toArray(), ...n.toArray()];
        for (const name of Object.keys(geometry.attributes).sort()) {
          if (name === "position" || name === "normal") continue;
          const attribute = geometry.getAttribute(name);
          if (name === "tangent") {
            const tangent = new THREE.Vector3().fromBufferAttribute(attribute, index)
              .transformDirection(mesh.matrixWorld);
            corner.push(...tangent.toArray(), attribute.getW(index));
          } else {
            for (let component = 0; component < attribute.itemSize; component++) {
              corner.push(attribute.getComponent(index, component));
            }
          }
        }
        corners.push(corner);
      }
    }
  }
  // Sort exact values; the comparison consumes tolerance matches in this sorted multiset.
  const compare = (a: number[], b: number[]): number => {
    for (let i = 0; i < a.length; i++) {
      const delta = a[i]! - b[i]!;
      if (delta !== 0) return delta;
    }
    return 0;
  };
  for (const states of result.values()) for (const corners of states.values()) corners.sort(compare);
  return result;
}

function assertEquivalent(original: THREE.Object3D, merged: THREE.Object3D): number {
  const before = surfaces(original), after = surfaces(merged);
  expect(new Set(after.keys())).toEqual(new Set(before.keys()));
  let maximumError = 0;
  for (const [material, states] of before) {
    const actualStates = after.get(material)!;
    expect(new Set(actualStates.keys())).toEqual(new Set(states.keys()));
    for (const [state, corners] of states) {
      const actual = actualStates.get(state)!;
      expect(actual.length).toBe(corners.length);
      const consumed = new Uint8Array(actual.length);
      for (const corner of corners) {
        // Float32 rounding can change lexicographic order at shared positions. Search
        // only the sorted X interval, then consume one matching full corner.
        let low = 0, high = actual.length;
        while (low < high) {
          const mid = (low + high) >>> 1;
          if (actual[mid]![0]! < corner[0]! - TOLERANCE) low = mid + 1;
          else high = mid;
        }
        let match = -1, bestError = Infinity;
        for (let i = low; i < actual.length && actual[i]![0]! <= corner[0]! + TOLERANCE; i++) {
          if (consumed[i] || actual[i]!.length !== corner.length) continue;
          let error = 0;
          for (let j = 0; j < corner.length; j++) {
            error = Math.max(error, Math.abs(actual[i]![j]! - corner[j]!));
            if (error > TOLERANCE) break;
          }
          if (error <= TOLERANCE && error < bestError) { match = i; bestError = error; }
        }
        if (match < 0) throw new Error(`Unmatched surface corner for ${material.name}: ${JSON.stringify(corner)}`);
        consumed[match] = 1;
        maximumError = Math.max(maximumError, bestError);
      }
      expect(consumed.every(value => value === 1)).toBe(true);
    }
  }
  expect(maximumError).toBeLessThanOrEqual(TOLERANCE);
  for (const precise of [false, true]) {
    const a = new THREE.Box3().setFromObject(original, precise);
    const b = new THREE.Box3().setFromObject(merged, precise);
    for (const endpoint of ["min", "max"] as const) {
      expect(a[endpoint].distanceTo(b[endpoint])).toBeLessThanOrEqual(TOLERANCE);
    }
  }
  return maximumError;
}

beforeAll(async () => {
  // Geometry and material definitions come from the real GLB/FBX files. Headless
  // tests replace image decoding only; texture references stay shared across clones.
  const textureLoad = vi.spyOn(THREE.TextureLoader.prototype, "load").mockImplementation((url, onLoad) => {
    const texture = new THREE.Texture<HTMLImageElement>(); texture.name = url;
    queueMicrotask(() => onLoad?.(texture));
    return texture;
  });
  try {
    for (const [type, path] of Object.entries(assets) as [MotorType, string][]) {
      const bytes = readFileSync(resolve("public", path.slice(1)));
      const buffer = new ArrayBuffer(bytes.byteLength);
      new Uint8Array(buffer).set(bytes);
      let source: THREE.Group;
      if (path.endsWith(".fbx")) source = new FBXLoader().parse(buffer, "");
      else {
        const loader = new GLTFLoader();
        loader.register(parser => {
          parser.loadTexture = async index => {
            const texture = new THREE.Texture(); texture.name = `source-texture-${index}`;
            return texture;
          };
          return { name: "headless-image-decoding" };
        });
        source = (await loader.parseAsync(buffer, "")).scene;
      }
      const original = prepareCityVehicleTemplate(source, type);
      original.updateWorldMatrix(true, true);
      originals.set(type, original);
      const merged = original.clone(true);
      mergeVehicleTemplateMeshes(merged);
      mergedTemplates.set(type, merged);
    }
    const bikeBytes = readFileSync(resolve("public/models/city-runtime/Bicycle_Man_34-bike-only.glb"));
    const bikeBuffer = new ArrayBuffer(bikeBytes.byteLength); new Uint8Array(bikeBuffer).set(bikeBytes);
    const bikeLoader = new GLTFLoader();
    bikeLoader.register(parser => {
      parser.loadTexture = async () => new THREE.Texture(); return { name: "headless-bike-image-decoding" };
    });
    bicycleTemplate = prepareCityVehicleTemplate((await bikeLoader.parseAsync(bikeBuffer, "")).scene, "bicycle");
  } finally { textureLoad.mockRestore(); }
}, 30000);

afterAll(() => {
  const geometries = new Set<THREE.BufferGeometry>(), allMaterials = new Set<THREE.Material>();
  for (const template of [...originals.values(), ...mergedTemplates.values(), bicycleTemplate]) template.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) allMaterials.add(material);
  });
  for (const geometry of geometries) geometry.dispose();
  for (const material of allMaterials) material.dispose();
});

describe("real motor-vehicle template merging", () => {
  it.each(Object.keys(assets) as MotorType[])("preserves %s surfaces, materials, shadows and bounds", type => {
    const original = originals.get(type)!, merged = mergedTemplates.get(type)!;
    const error = assertEquivalent(original, merged);
    const hiddenBefore: THREE.Mesh[] = [], hiddenAfter: THREE.Mesh[] = [];
    original.traverse(node => { if (node instanceof THREE.Mesh && !node.visible) hiddenBefore.push(node); });
    merged.traverse(node => { if (node instanceof THREE.Mesh && !node.visible) hiddenAfter.push(node); });
    expect(hiddenAfter.map(node => node.name)).toEqual(hiddenBefore.map(node => node.name));
    for (let i = 0; i < hiddenBefore.length; i++) {
      expect(hiddenAfter[i]!.geometry).toBe(hiddenBefore[i]!.geometry);
      expect(hiddenAfter[i]!.material).toEqual(hiddenBefore[i]!.material);
    }
    const metadata = (root: THREE.Object3D) => {
      const records: { name: string; data: object }[] = [];
      root.traverse(node => {
        if (Object.keys(node.userData).length > 0) records.push({ name: node.name, data: JSON.parse(JSON.stringify(node.userData)) });
      });
      return records;
    };
    expect(metadata(merged)).toEqual(metadata(original));
    console.log(`${type}: visible meshes ${meshes(original).length} -> ${meshes(merged).length}; `
      + `distinct materials ${materials(original).size} -> ${materials(merged).size}; `
      + `main-pass draws ${drawCalls(original)} -> ${drawCalls(merged)}; hidden LOD meshes ${hiddenBefore.length} -> ${hiddenAfter.length}; max surface error ${error}; `
      + `position/normal/UV/color/tangent tolerance ${TOLERANCE}; material identity/shadows/bounds PASS`);
  });

  it("reduces renderable meshes for 50 fixed vehicles while preserving one root, picking and proxies", () => {
    const before = new THREE.Group(), after = new THREE.Group();
    for (let i = 0; i < 50; i++) {
      const type = (Object.keys(assets) as MotorType[])[i % 5]!;
      const a = originals.get(type)!.clone(true), b = mergedTemplates.get(type)!.clone(true);
      for (const root of [a, b]) {
        root.position.set(i * 15, 0.1, -i * 7); root.rotation.y = i * 0.13;
        root.userData.target = { kind: "entity", id: `vehicle.${i}` }; root.userData.entityKind = "ugv";
        root.add(collisionBox(new THREE.Vector3(3, 4, 12), 0xf3ae47));
      }
      before.add(a); after.add(b);
      expect(b.userData).toEqual(a.userData);
      expect(b.children.at(-1)!.userData.collisionProxy).toBe(true);
    }
    const countBefore = meshes(before).length, countAfter = meshes(after).length;
    expect(countAfter).toBeLessThan(countBefore);
    expect(after.children.length).toBe(50);
    const ray = new THREE.Raycaster(new THREE.Vector3(0, 20, 0), new THREE.Vector3(0, -1, 0));
    const hits = [before, after].map(root => {
      root.updateWorldMatrix(true, true);
      const hit = ray.intersectObjects(visiblePickObjects([root]), false)[0]!;
      let object = hit.object;
      while (object.userData.target === undefined) object = object.parent!;
      return { target: object.userData.target, distance: hit.distance };
    });
    expect(hits[1]!.target).toEqual(hits[0]!.target);
    expect(Math.abs(hits[1]!.distance - hits[0]!.distance)).toBeLessThanOrEqual(TOLERANCE);
    console.log(`draw-count probe N=50 (10 of each motor type): renderable meshes ${countBefore} -> ${countAfter}; `
      + `shadow-caster meshes ${meshes(before).filter(mesh => mesh.castShadow).length} -> `
      + `${meshes(after).filter(mesh => mesh.castShadow).length}; `
      + `main-pass draws ${drawCalls(before)} -> ${drawCalls(after)}; `
      + `shadow-pass draws ${drawCalls(before, true)} -> ${drawCalls(after, true)}; roots/picking/proxies PASS`);
  });

  it("keeps prepareRenderer texture uploads and material/vertex shader variants", async () => {
    const coverage = (root: THREE.Object3D) => {
      const variants = new Set<string>();
      root.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return;
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
          variants.add(JSON.stringify([material.uuid, node instanceof THREE.SkinnedMesh,
            Object.keys(node.geometry.attributes).sort(), Object.keys(node.geometry.morphAttributes).sort()]));
        }
      });
      return variants;
    };
    const prepare = async (templates: Map<MotorType, THREE.Group>) => {
      const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
      Reflect.set(preview, "vehicleTemplates", Object.fromEntries([...templates].map(([type, template]) =>
        [type, template.clone(true)])));
      Reflect.set(preview, "personTemplates", []);
      const casters = new TrafficShadowCasters(new THREE.Group(), {});
      Reflect.set(preview, "shadowCasters", casters);
      const uploaded = new Set<THREE.Texture>(), compiled: Set<string>[] = [];
      const renderer = {
        initTexture: (texture: THREE.Texture) => { uploaded.add(texture); },
        compileAsync: vi.fn(async (root: THREE.Object3D) => { compiled.push(coverage(root)); }),
        info: { programs: [] }, shadowMap: { render() {} }, render() {}, getContext: () => signalledGl(),
      };
      await preview.prepareRenderer(renderer as unknown as THREE.WebGLRenderer,
        new THREE.PerspectiveCamera(), new THREE.Scene());
      casters.dispose();
      expect(renderer.compileAsync).toHaveBeenCalledTimes(2);
      return { uploaded, compiled };
    };
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0); return 0;
    });
    try {
      expect(await prepare(mergedTemplates)).toEqual(await prepare(originals));
    } finally { vi.unstubAllGlobals(); }
  });

});

describe("vehicle merge exclusions and shared lamp materials", () => {
  it("keeps named, animated, unpreservable multi-material, skinned, transparent and hidden parts", () => {
    const root = new THREE.Group(), material = new THREE.MeshStandardMaterial();
    const make = (name: string) => {
      const mesh: THREE.Mesh = new THREE.Mesh(new THREE.BoxGeometry(), material); mesh.name = name;
      root.add(mesh); return mesh;
    };
    const named = make("referenced"), wheel = make("wheel"), hidden = make("body_LOD1");
    hidden.visible = false;
    const grouped = make("grouped"); grouped.material = [material, material];
    grouped.geometry.groups[1]!.start = 0; // Overlap cannot preserve the original group contract.
    const transparent = make("window"); transparent.material = new THREE.MeshStandardMaterial({ transparent: true });
    const skinned = new THREE.SkinnedMesh(new THREE.BoxGeometry(), material); root.add(skinned);
    root.animations = [new THREE.AnimationClip("wheel-spin", 1,
      [new THREE.NumberKeyframeTrack("wheel.rotation[x]", [0, 1], [0, 1])])];
    make("body1"); make("body2");
    mergeVehicleTemplateMeshes(root, new Set(["referenced"]));
    for (const mesh of [named, wheel, hidden, grouped, transparent, skinned]) expect(mesh.parent).toBe(root);
    expect(meshes(root).length).toBe(6);
  });

  it("preserves complete material groups and ignores unused transparent material slots", () => {
    const root = new THREE.Group(), opaque = new THREE.MeshStandardMaterial();
    const second = new THREE.MeshStandardMaterial();
    const unused = new THREE.MeshStandardMaterial({ transparent: true });
    for (let i = 0; i < 2; i++) {
      const geometry = new THREE.BoxGeometry();
      const mesh = new THREE.Mesh(geometry, [opaque, second, unused]); mesh.position.x = i * 2;
      for (const [index, group] of geometry.groups.entries()) group.materialIndex = index % 2;
      root.add(mesh);
    }
    const original = root.clone(true);
    mergeVehicleTemplateMeshes(root);
    assertEquivalent(original, root);
    expect(meshes(root).length).toBe(2); expect(drawCalls(root)).toBe(2);
  });

  it("leaves no node for a replaced mesh without metadata or children and keeps merged surfaces static", () => {
    const root = new THREE.Group(), part = new THREE.Group();
    const material = new THREE.MeshStandardMaterial();
    const [plain, named, parent] = [0, 1, 2].map(i => {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(), material);
      mesh.name = `part ${i}`; mesh.position.x = i * 2; part.add(mesh); return mesh;
    });
    named!.userData = { originalName: "Body" };
    const marker = new THREE.Object3D(); marker.name = "marker"; marker.userData = { socket: true }; parent!.add(marker);
    root.add(part);
    const original = root.clone(true);
    mergeVehicleTemplateMeshes(root);
    assertEquivalent(original, root);
    expect(root.getObjectByName(plain!.name)).toBeUndefined();
    expect(root.getObjectByName(named!.name)!.userData).toEqual({ originalName: "Body" });
    expect(root.getObjectByName(parent!.name)!.children).toEqual([marker]);
    expect(part.children.map(node => node.name)).toEqual(["part 1", "part 2"]);
    const merged = meshes(root);
    expect(merged).toHaveLength(1);
    expect(merged[0]!.matrixAutoUpdate).toBe(false);
  });

  it("merges only equal shadow flags and retains lamp material identity through vehicle clones", () => {
    const root = new THREE.Group();
    const lamp = new THREE.MeshStandardMaterial({ emissive: 0xffffff });
    for (const castShadow of [true, false]) for (let i = 0; i < 2; i++) {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(), lamp);
      mesh.position.x = i * 2; mesh.castShadow = castShadow; mesh.receiveShadow = !castShadow;
      root.add(mesh);
    }
    root.position.set(2, 1, -3); root.rotation.y = 0.31;
    const original = root.clone(true);
    mergeVehicleTemplateMeshes(root);
    assertEquivalent(original, root);
    expect(meshes(root).length).toBe(2);
    const clone = root.clone(true);
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    Reflect.set(preview, "vehicleLamps", [{ material: lamp, rear: false }]);
    Reflect.set(preview, "vehicleLightingTimeOfDay", null);
    Reflect.set(preview, "vehicles", new Map()); Reflect.set(preview, "headlightBeams", []);
    preview.setVehicleLighting(new THREE.PerspectiveCamera(), "night");
    for (const mesh of meshes(clone)) {
      expect(mesh.material).toBe(lamp); expect(lamp.emissiveIntensity).toBe(2.4);
    }
  });
});

// Real Three shadow traversal; only GPU state and draw submission are stubbed.
function trafficShadowHarness(scene: THREE.Scene) {
  const draws: { mesh: THREE.Mesh; depth: THREE.Material; group: { start: number; count: number; materialIndex?: number } | null }[] = [];
  const renderer = {
    getRenderTarget: () => null, getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    setRenderTarget() {}, clear() {},
    state: { setBlending() {}, setScissorTest() {}, viewport() {},
      buffers: { depth: { getReversed: () => false, setTest() {} }, color: { setClear() {} } } },
    properties: { get: () => ({}) }, localClippingEnabled: true,
    renderBufferDirect(_camera: THREE.Camera, _scene: unknown, _geometry: THREE.BufferGeometry,
      depth: THREE.Material, mesh: THREE.Mesh, group: { start: number; count: number; materialIndex?: number } | null) {
      draws.push({ mesh, depth: depth.clone(), group });
    },
  };
  const shadowMap = new WebGLShadowMap(renderer as unknown as THREE.WebGLRenderer,
    { update: (mesh: THREE.Mesh) => mesh.geometry } as unknown as ConstructorParameters<typeof WebGLShadowMap>[1],
    { maxTextureSize: 4096 } as ConstructorParameters<typeof WebGLShadowMap>[2]);
  shadowMap.enabled = true;
  const light = new THREE.DirectionalLight(); light.castShadow = true;
  light.position.set(0, 500, 0); scene.add(light, light.target);
  Object.assign(light.shadow.camera, { left: -500, right: 500, top: 500, bottom: -500, near: 1, far: 1000 });
  light.shadow.camera.updateProjectionMatrix();
  const camera = new THREE.PerspectiveCamera();
  const full = Object.assign(renderer, { shadowMap });
  return { renderer: full as unknown as THREE.WebGLRenderer, camera, light, draws,
    run() { draws.length = 0; scene.updateMatrixWorld(true); shadowMap.render([light], scene, camera); return draws; },
    main() {
      draws.length = 0; scene.updateMatrixWorld(true);
      // Main projectObject traversal, sent through the same renderBufferDirect stub.
      scene.traverseVisible(node => {
        if (!(node instanceof THREE.Mesh) || !node.layers.test(camera.layers)) return;
        if (Array.isArray(node.material)) {
          for (const group of node.geometry.groups) {
            const material = node.material[group.materialIndex!]!;
            if (material?.visible) renderer.renderBufferDirect(camera, scene, node.geometry, material, node, group);
          }
        } else if (node.material.visible) renderer.renderBufferDirect(camera, scene, node.geometry, node.material, node, null);
      });
      return draws;
    } };
}

function trafficFixture(count = 15) {
  const root = new THREE.Group(), scene = new THREE.Scene(); scene.add(root);
  // Nonidentity scene/traffic ancestry verifies world-space instance placement.
  root.position.set(3.25, 0.12, -4.125); root.rotation.y = 0.21;
  const vehicles: THREE.Group[] = [], types = Object.keys(assets) as MotorType[];
  for (let i = 0; i < count; i++) {
    const vehicle = mergedTemplates.get(types[i % types.length]!)!.clone(true);
    vehicle.position.set(i * 4.2, 0.2, -i * 1.75); vehicle.rotation.y = i * 0.17;
    vehicle.userData.target = { kind: "entity", id: `vehicle.${i}` }; vehicle.userData.entityKind = "ugv";
    vehicle.add(collisionBox(new THREE.Vector3(3, 4, 12), 0xf3ae47)); root.add(vehicle); vehicles.push(vehicle);
  }
  const harness = trafficShadowHarness(scene);
  const install = () => {
    const casters = new TrafficShadowCasters(root, Object.fromEntries(mergedTemplates));
    for (let i = 0; i < vehicles.length; i++) casters.add(mergedTemplates.get(types[i % types.length]!)!, vehicles[i]!);
    casters.prepareRenderer(harness.renderer); return casters;
  };
  return { root, scene, vehicles, harness, install };
}

function issuedShadowTriangles(draws: ReturnType<ReturnType<typeof trafficShadowHarness>["run"]>) {
  const result = new Map<string, number[][]>();
  for (const { mesh, depth, group } of draws) {
    const material = depth as DepthSource;
    const state = JSON.stringify(DEPTH_FIELDS.map(field => {
      const value = material[field];
      if (field === "map" && material.alphaTest === 0) return null;
      return value instanceof THREE.Texture ? value.uuid : value;
    }));
    const triangles = result.get(state) ?? []; result.set(state, triangles);
    const geometry = mesh.geometry, p = geometry.getAttribute("position");
    const start = Math.max(group?.start ?? 0, geometry.drawRange.start);
    const end = Math.min(geometry.index?.count ?? p.count, (group?.start ?? 0) + (group?.count ?? Infinity),
      geometry.drawRange.start + geometry.drawRange.count);
    const instances = mesh instanceof THREE.InstancedMesh ? mesh.count : 1;
    for (let instance = 0; instance < instances; instance++) {
      const matrix = mesh.matrixWorld.clone();
      if (mesh instanceof THREE.InstancedMesh) matrix.multiply(new THREE.Matrix4().fromArray(mesh.instanceMatrix.array, instance * 16));
      for (let offset = start; offset < end; offset += 3) {
        const triangle: number[] = [];
        for (let corner = 0; corner < 3; corner++) {
          // WebGLRenderer flips frontFace for mirrored object.matrixWorld.
          const windingCorner = mesh.matrixWorld.determinant() < 0 && corner > 0 ? 3 - corner : corner;
          const index = geometry.index?.getX(offset + windingCorner) ?? offset + windingCorner;
          const point = mesh instanceof THREE.SkinnedMesh ? mesh.getVertexPosition(index, new THREE.Vector3())
            : new THREE.Vector3().fromBufferAttribute(p, index);
          triangle.push(...point.applyMatrix4(matrix).toArray());
        }
        triangles.push(triangle);
      }
    }
  }
  for (const triangles of result.values()) triangles.sort((a, b) => a[0]! - b[0]!);
  return result;
}

function equivalentTrafficShadows(a: ReturnType<typeof issuedShadowTriangles>, b: ReturnType<typeof issuedShadowTriangles>) {
  const tolerance = 1e-4; let maximumError = 0, count = 0;
  expect(new Set(b.keys())).toEqual(new Set(a.keys()));
  for (const [state, triangles] of a) {
    const actual = b.get(state)!; expect(actual.length).toBe(triangles.length);
    const consumed = new Uint8Array(actual.length);
    for (const triangle of triangles) {
      let low = 0, high = actual.length;
      while (low < high) {
        const mid = (low + high) >>> 1;
        if (actual[mid]![0]! < triangle[0]! - tolerance) low = mid + 1; else high = mid;
      }
      let match = -1;
      for (let i = low; i < actual.length && actual[i]![0]! <= triangle[0]! + tolerance; i++) {
        if (consumed[i]) continue;
        let error = 0;
        for (let j = 0; j < 9; j++) error = Math.max(error, Math.abs(actual[i]![j]! - triangle[j]!));
        if (error <= tolerance) { match = i; maximumError = Math.max(maximumError, error); break; }
      }
      expect(match, `unmatched oriented triangle in depth state ${state}`).toBeGreaterThanOrEqual(0);
      consumed[match] = 1; count++;
    }
  }
  return { maximumError, count, tolerance };
}

/** Drops triangles wholly outside one frustum plane; they cannot write a shadow-map texel. */
function reachableTriangles(frustum: THREE.Frustum, triangles: ReturnType<typeof issuedShadowTriangles>) {
  const result = new Map<string, number[][]>(), point = new THREE.Vector3();
  for (const [state, list] of triangles) {
    const kept = list.filter(triangle => !frustum.planes.some(plane =>
      [0, 3, 6].every(offset => plane.distanceToPoint(point.fromArray(triangle, offset)) < 0)));
    if (kept.length > 0) result.set(state, kept);
  }
  return result;
}

function trafficEvidence(line: string): void { process.stdout.write(`${line}\n`); }

describe("real motor traffic shadow instancing", () => {
  it("matches world triangles per depth state and reduces real shadow draws with unchanged main draws", () => {
    const { vehicles, harness, install } = trafficFixture();
    const receivers = vehicles.flatMap(vehicle => meshes(vehicle)).map(mesh => [mesh, mesh.receiveShadow] as const);
    const mainBefore = harness.main().length, beforeDraws = harness.run();
    const shadowBefore = beforeDraws.length, before = issuedShadowTriangles(beforeDraws);
    const casters = install();
    try {
      const afterDraws = harness.run(), shadowAfter = afterDraws.length;
      const equivalence = equivalentTrafficShadows(before, issuedShadowTriangles(afterDraws));
      expect(shadowAfter).toBeLessThan(shadowBefore);
      const mainAfter = harness.main().length; expect(mainAfter).toBe(mainBefore);
      for (const [mesh, receiveShadow] of receivers) expect(mesh.receiveShadow).toBe(receiveShadow);
      expect(casters.group.children.every(mesh => !mesh.visible)).toBe(true);
      for (const child of casters.group.children as THREE.InstancedMesh[]) {
        expect(Object.keys(child.geometry.attributes)).toEqual(["position"]);
        expect(child.frustumCulled).toBe(true);
        for (let i = 0; i < child.count; i++) {
          const sphere = child.geometry.boundingSphere!.clone().applyMatrix4(new THREE.Matrix4().fromArray(child.instanceMatrix.array, i * 16));
          expect(child.boundingSphere!.center.distanceTo(sphere.center) + sphere.radius).toBeLessThanOrEqual(child.boundingSphere!.radius + 1e-6);
        }
      }
      trafficEvidence(`T27 N=15 (3 each sedan/taxi/police/bus/truck): shadow ${shadowBefore} -> ${shadowAfter} real renderBufferDirect calls; main ${mainBefore} -> ${mainAfter} stub submissions; oriented triangles ${equivalence.count}; max error ${equivalence.maximumError}; tolerance ${equivalence.tolerance} m; uncovered ${JSON.stringify(casters.uncovered)}`);
    } finally { casters.dispose(); }
  }, 60000);

  it("culls each source against the shadow frustum exactly as the per-vehicle pass did", () => {
    const { vehicles, harness, install } = trafficFixture();
    // Covers the first few vehicles only; the rest lie outside the sun frustum.
    Object.assign(harness.light.shadow.camera, { left: -12, right: 12, top: 12, bottom: -12 });
    harness.light.shadow.camera.updateProjectionMatrix();
    const beforeDraws = harness.run(), shadowBefore = beforeDraws.length, before = issuedShadowTriangles(beforeDraws);
    const casters = install();
    try {
      const afterDraws = harness.run(), after = issuedShadowTriangles(afterDraws);
      // Separate surfaces cull per material group, so a straddling vehicle may skip groups
      // that lie wholly outside one frustum plane and cannot reach the shadow map.
      const frustum = harness.light.shadow.getFrustum();
      const issuedBefore = [...before.values()].reduce((sum, list) => sum + list.length, 0);
      const issuedAfter = [...after.values()].reduce((sum, list) => sum + list.length, 0);
      const reachable = reachableTriangles(frustum, before);
      const equivalence = equivalentTrafficShadows(reachable, reachableTriangles(frustum, after));
      const reachableCount = [...reachable.values()].reduce((sum, list) => sum + list.length, 0);
      expect(issuedAfter).toBeGreaterThanOrEqual(reachableCount);
      const instances = (casters.group.children as THREE.InstancedMesh[]).reduce((sum, mesh) => sum + mesh.count, 0);
      const outside = vehicles.filter(vehicle => meshes(vehicle).every(mesh => !harness.light.shadow.getFrustum().intersectsObject(mesh)));
      expect(outside.length).toBeGreaterThan(0);
      const surfaces = Reflect.get(casters, "surfaces") as { bindings: { source: THREE.Mesh }[] }[];
      const bound = surfaces.reduce((sum, surface) => sum + surface.bindings.length, 0);
      const shown = (node: THREE.Object3D | null): boolean => node === null || (node.visible && shown(node.parent));
      const expected = surfaces.reduce((sum, surface) => sum + surface.bindings.filter(binding =>
        shown(binding.source) && frustum.intersectsObject(binding.source)).length, 0);
      expect(instances).toBe(expected); expect(instances).toBeLessThan(bound);
      // A source that opts out of frustum culling is written to its surfaces, as it was drawn
      // before; the caster's own sphere test then applies as for any shadow object.
      const far = meshes(outside[0]!).find(mesh => casters.group.children.some(child =>
        (child as THREE.InstancedMesh).material === mesh.material))!;
      far.frustumCulled = false; harness.run();
      const optedOut = (casters.group.children as THREE.InstancedMesh[]).reduce((sum, mesh) => sum + mesh.count, 0);
      expect(optedOut).toBeGreaterThan(instances);
      far.frustumCulled = true; harness.run();
      expect((casters.group.children as THREE.InstancedMesh[]).reduce((sum, mesh) => sum + mesh.count, 0)).toBe(instances);
      trafficEvidence(`T27 shadow frustum culling: ${outside.length} of ${vehicles.length} vehicles outside; shadow ${shadowBefore} -> ${afterDraws.length} draws; ${instances} of ${bound} bound instances; issued triangles ${issuedBefore} -> ${issuedAfter}; frustum-reachable oriented triangles ${equivalence.count} matched; max error ${equivalence.maximumError} PASS`);
    } finally { casters.dispose(); }
  }, 60000);

  it("follows actual inactive/display-limit/traffic-layer visibility and restores visible vehicles", () => {
    const { root, vehicles, harness, install } = trafficFixture(5);
    const before = harness.run().length, casters = install();
    const shown = harness.run().length;
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    const types = Object.keys(assets) as MotorType[];
    const samples = vehicles.map((_, i) => [`vehicle.${i}`, i * 4, -i * 2, i * 20, types[i]!, 0.2]);
    const frame = (values: unknown[]) => ({ vehicles: values, persons: [], tls: {} });
    Object.assign(preview, { vehicles: new Map(vehicles.map((object, i) => [`vehicle.${i}`, { object, type: types[i]!, cyclist: null }])),
      people: new Map(), aircraft: new Map(), lamps: [], currentSecond: -1, displayIds: null,
      data: { duration_seconds: 3, step_seconds: 1, frames: [frame(samples), frame([]), frame(samples), frame(samples)] },
      flightData: { duration_seconds: 1, step_seconds: 1, frames: [[], []] } });
    try {
      preview.update(1, true, true, true, true, true); // No active vehicle samples.
      expect(harness.run()).toHaveLength(0);
      preview.update(0, true, true, true, true, true);
      expect(harness.run()).toHaveLength(shown);
      preview.setDisplayLimits({ vehicles: 0, bicycles: 0, pedestrians: 0 });
      preview.update(0, true, true, true, true, true);
      expect(harness.run()).toHaveLength(0); expect(preview.visibleCounts().vehicles).toBe(0);
      expect(preview.entityPosition("vehicle.0")).toBeNull();
      preview.setDisplayLimits({ vehicles: 5, bicycles: 0, pedestrians: 0 });
      preview.update(0, true, true, true, true, true);
      expect(harness.run()).toHaveLength(shown); expect(preview.visibleCounts().vehicles).toBe(5);
      expect(preview.entityPosition("vehicle.0")).toBe(vehicles[0]!.position);
      preview.update(0, false, true, true, true, true); // Actual traffic-layer argument.
      expect(harness.run()).toHaveLength(0);
      preview.update(0, true, true, true, true, true); expect(harness.run()).toHaveLength(shown);
      root.visible = false; expect(harness.run()).toHaveLength(0);
      root.visible = true; expect(harness.run()).toHaveLength(shown);
      harness.camera.layers.disableAll(); expect(harness.run()).toHaveLength(0);
      harness.camera.layers.enable(0); expect(harness.run()).toHaveLength(shown);
      vehicles[0]!.children[0]!.visible = false;
      expect(harness.run().length).toBeLessThan(shown);
      vehicles[0]!.children[0]!.visible = true; expect(harness.run()).toHaveLength(shown);
      trafficEvidence(`T27 visibility: initial shadow draws ${before}, instanced ${shown}; inactive/display-limited/hidden ancestor/layers off=0; show again PASS`);
    } finally { casters.dispose(); }
  });

  it("never returns a caster to picking, including when explicitly visible", () => {
    const { root, vehicles, harness, install } = trafficFixture(5);
    const ray = new THREE.Raycaster(new THREE.Vector3(3.25, 20, -4.125), new THREE.Vector3(0, -1, 0));
    harness.run();
    const originalHit = ray.intersectObjects(visiblePickObjects([root]), false)[0]!;
    const target = (mesh: THREE.Object3D) => { while (mesh.userData.target === undefined) mesh = mesh.parent!; return mesh.userData.target; };
    const beforeTarget = target(originalHit.object), casters = install(); harness.run();
    try {
      for (const child of casters.group.children) child.visible = true;
      const hits = ray.intersectObjects(visiblePickObjects([root]), false);
      expect(hits.some(hit => hit.object.userData.trafficShadowCaster)).toBe(false);
      expect(target(hits[0]!.object)).toEqual(beforeTarget); expect(hits[0]!.distance).toBe(originalHit.distance);
      expect(ray.intersectObjects(casters.group.children, true)).toEqual([]);
      expect(vehicles.every(vehicle => vehicle.children.at(-1)!.userData.collisionProxy === true)).toBe(true);
      trafficEvidence("T27 picking: same target/distance/proxies; explicitly visible and recursive hidden casters return no hits PASS");
    } finally { casters.dispose(); }
  });

  it("makes steady hooks without container/typed-buffer creation, array iterators or math clones", () => {
    const { scene, harness, install } = trafficFixture(15), casters = install(); harness.run();
    const casterMeshes = casters.group.children as THREE.InstancedMesh[];
    const identities = casterMeshes.map(mesh => [mesh.geometry, mesh.instanceMatrix, mesh.instanceMatrix.array, mesh.boundingSphere]);
    // Isolate the shared before/after wrapper from Three/GPU submission allocations.
    // The original callback is replaced before registration in a second renderer.
    const renderer = { shadowMap: { render() {} } } as unknown as THREE.WebGLRenderer;
    casters.prepareRenderer(renderer);
    const lights: THREE.Light[] = [harness.light], arrayMethods = ["map", "slice", "filter", "flatMap", "concat"] as const;
    const methods = arrayMethods.map(name => Array.prototype[name]);
    const iterator = Array.prototype[Symbol.iterator], matrixClone = THREE.Matrix4.prototype.clone;
    const vectorClone = THREE.Vector3.prototype.clone, sphereClone = THREE.Sphere.prototype.clone;
    const containers = { Array, Map, Set, Float32Array };
    let allocations = 0;
    const counted = function () { allocations++; throw new Error("steady hook allocation"); };
    scene.updateMatrixWorld(true);
    try {
      for (let i = 0; i < arrayMethods.length; i++) Reflect.set(Array.prototype, arrayMethods[i]!, counted);
      Reflect.set(Array.prototype, Symbol.iterator, counted);
      THREE.Matrix4.prototype.clone = counted; THREE.Vector3.prototype.clone = counted; THREE.Sphere.prototype.clone = counted;
      globalThis.Array = new Proxy(containers.Array, { construct() { return counted(); } });
      globalThis.Map = new Proxy(containers.Map, { construct() { return counted(); } });
      globalThis.Set = new Proxy(containers.Set, { construct() { return counted(); } });
      globalThis.Float32Array = new Proxy(containers.Float32Array, { construct() { return counted(); } });
      for (let i = 0; i < 100; i++) renderer.shadowMap.render(lights, scene, harness.camera);
    } finally {
      globalThis.Array = containers.Array; globalThis.Map = containers.Map; globalThis.Set = containers.Set; globalThis.Float32Array = containers.Float32Array;
      for (let i = 0; i < arrayMethods.length; i++) Reflect.set(Array.prototype, arrayMethods[i]!, methods[i]);
      Reflect.set(Array.prototype, Symbol.iterator, iterator);
      THREE.Matrix4.prototype.clone = matrixClone; THREE.Vector3.prototype.clone = vectorClone; THREE.Sphere.prototype.clone = sphereClone;
    }
    expect(allocations).toBe(0);
    for (let i = 0; i < casterMeshes.length; i++) {
      const mesh = casterMeshes[i]!;
      expect([mesh.geometry, mesh.instanceMatrix, mesh.instanceMatrix.array, mesh.boundingSphere]).toEqual(identities[i]);
    }
    casters.dispose();
    trafficEvidence("T27 steady hook: 100 frames, trapped Array/Map/Set/Float32Array constructors, array-producing methods/iterator, Matrix4/Vector3/Sphere clones: 0; geometry/instance buffer/bounds identities retained PASS");
  });

  it("warms every real instanced depth surface outside the sun frustum and restores culling", async () => {
    const { scene, harness, install } = trafficFixture(0), casters = install();
    harness.light.position.set(10000, 500, 10000); harness.light.target.position.set(10000, 0, 10000);
    expect(harness.run()).toHaveLength(0);
    const events: string[] = [];
    const renderer = Object.assign(harness.renderer, {
      initTexture() {}, compileAsync: vi.fn(async () => { events.push("compile"); }),
      getContext: () => signalledGl(() => events.push("gpu idle")),
      info: { programs: [{ getUniforms() { events.push("uniforms"); }, getAttributes() { events.push("attributes"); } }] },
      render(_scene: THREE.Scene, view: THREE.Camera) {
        events.push("depth render"); expect(view).not.toBe(harness.camera);
        expect(view.layers.mask).toBe(harness.camera.layers.mask); harness.run();
      },
    });
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    Object.assign(preview, { shadowCasters: casters, vehicleTemplates: Object.fromEntries(mergedTemplates), personTemplates: [] });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 0; });
    try {
      await preview.prepareRenderer(renderer as unknown as THREE.WebGLRenderer, harness.camera, scene);
      expect(events).toEqual(["compile", "compile", "gpu idle", "depth render", "uniforms", "attributes"]);
      expect(harness.draws.length).toBe(casters.group.children.length);
      expect(harness.draws.every(draw => draw.mesh instanceof THREE.InstancedMesh && Reflect.get(draw.depth, "isMeshDepthMaterial"))).toBe(true);
      trafficEvidence(`T27 warm-up: ${harness.draws.length} real instanced depth surfaces, no spawned vehicles and sun frustum miss; all variants submitted PASS`);
      expect(harness.run()).toHaveLength(0);
      expect(casters.group.children.every(mesh => !mesh.visible && mesh.frustumCulled)).toBe(true);
    } finally { casters.dispose(); vi.unstubAllGlobals(); }
  });
});

describe("traffic depth state and lifecycle", () => {
  it("retains unsupported casting surfaces and groups eligible surfaces by their full depth state", () => {
    const template = new THREE.Group(), root = new THREE.Group();
    const geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial();
    const add = (name: string, mesh: THREE.Mesh) => { mesh.name = name; mesh.castShadow = true; mesh.receiveShadow = true; template.add(mesh); return mesh; };
    add("skinned", new THREE.SkinnedMesh(geometry, material)).frustumCulled = false;
    const custom = add("custom", new THREE.Mesh(geometry, material)); custom.customDepthMaterial = new THREE.MeshDepthMaterial();
    const alpha = material.clone(); alpha.map = new THREE.Texture(); alpha.alphaTest = 0.4;
    add("alpha", new THREE.Mesh(geometry, alpha));
    const displacement = material.clone(); displacement.displacementMap = new THREE.Texture(); displacement.displacementScale = 1;
    add("displacement", new THREE.Mesh(geometry, displacement));
    const eligible = add("eligible", new THREE.Mesh(geometry, material)); eligible.layers.set(2);
    const other = material.clone(); other.shadowSide = THREE.DoubleSide; other.clipShadows = true;
    other.clippingPlanes = [new THREE.Plane(new THREE.Vector3(1, 0, 0), 5)];
    other.wireframeLinewidth = 2;
    add("other depth state", new THREE.Mesh(geometry, other)).layers.set(2);
    const scene = new THREE.Scene(); scene.add(root);
    const vehicle = template.clone(true); root.add(vehicle);
    const harness = trafficShadowHarness(scene); harness.camera.layers.enable(2);
    const before = harness.run().length;
    const casters = new TrafficShadowCasters(root, { sedan: template }); casters.add(template, vehicle); casters.prepareRenderer(harness.renderer);
    try {
      expect(casters.uncovered.map(item => item.reason)).toEqual(["skinned", "custom depth/distance material", "alpha-tested texture", "displacement"]);
      for (const name of ["skinned", "custom", "alpha", "displacement"]) expect(vehicle.getObjectByName(name)!.castShadow).toBe(true);
      expect(vehicle.getObjectByName("eligible")!.castShadow).toBe(false);
      expect(harness.run()).toHaveLength(before);
      const covered = harness.draws.filter(draw => draw.mesh.userData.trafficShadowCaster);
      expect(covered).toHaveLength(2);
      expect(new Set(covered.map(draw => draw.depth.side))).toEqual(new Set([THREE.BackSide, THREE.DoubleSide]));
      other.shadowSide = THREE.FrontSide; other.clippingPlanes[0]!.constant = 7; other.wireframeLinewidth = 3;
      harness.run();
      const changed = harness.draws.find(draw => draw.mesh.material === other)!;
      expect(changed.depth.side).toBe(THREE.FrontSide);
      expect(changed.depth.clippingPlanes![0]!.constant).toBe(7);
      expect((changed.depth as THREE.MeshDepthMaterial).wireframeLinewidth).toBe(3);
      harness.camera.layers.disable(0); // Source layer 2 still matches; caster must render it.
      expect(harness.run()).toHaveLength(2);
      other.map = new THREE.Texture(); other.alphaTest = 0.4;
      expect(() => harness.run()).toThrow("depth state changed to require vertex attributes");
      expect(() => harness.run()).toThrow("depth state changed to require vertex attributes");
      expect(casters.group.children.every(child => !child.visible)).toBe(true);
      expect(vehicle.getObjectByName("other depth state")!.receiveShadow).toBe(true);
      other.map = null; other.alphaTest = 0; expect(harness.run()).toHaveLength(2);
      process.stdout.write("T27 exclusions: skinned/custom depth/textured alpha test/displacement remain casting; source layers and side/clipping/linewidth state mutation PASS\n");
    } finally { casters.dispose(); }
  });

  it("restores receiveShadow on multi-material sources after VSM and exceptions, grows only past capacity and disposes resources", () => {
    const template = new THREE.Group(), root = new THREE.Group(), scene = new THREE.Scene(); scene.add(root);
    const material = new THREE.MeshStandardMaterial(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), [material, material]);
    for (const group of mesh.geometry.groups) group.materialIndex = 0;
    mesh.castShadow = true; mesh.receiveShadow = true; template.add(mesh);
    const casters = new TrafficShadowCasters(root, { sedan: template });
    for (let i = 0; i < 3; i++) { const vehicle = template.clone(true); root.add(vehicle); casters.add(template, vehicle); }
    const harness = trafficShadowHarness(scene), original = harness.renderer.shadowMap.render;
    casters.prepareRenderer(harness.renderer); harness.renderer.shadowMap.type = THREE.VSMShadowMap;
    harness.run();
    expect(root.children.filter(child => child !== casters.group).every(child => (child.children[0] as THREE.Mesh).receiveShadow)).toBe(true);
    expect(harness.draws.filter(draw => draw.mesh === root.children[1]!.children[0])).toHaveLength(0);
    const caster = casters.group.children[0] as THREE.InstancedMesh, buffer = caster.instanceMatrix;
    harness.run(); expect(caster.instanceMatrix).toBe(buffer);
    // Capacity is 6 after the initial growth. Add up to capacity, then exceed it.
    for (let i = 3; i < 6; i++) { const vehicle = template.clone(true); root.add(vehicle); casters.add(template, vehicle); }
    harness.run(); expect(caster.instanceMatrix).toBe(buffer);
    const extra = template.clone(true); root.add(extra); casters.add(template, extra); harness.run();
    expect(caster.instanceMatrix).not.toBe(buffer); expect(caster.count).toBe(7);
    const renderer = { shadowMap: { render() { throw new Error("shadow failure"); } } } as unknown as THREE.WebGLRenderer;
    casters.prepareRenderer(renderer);
    expect(() => renderer.shadowMap.render([], scene, harness.camera)).toThrow("shadow failure");
    expect(casters.group.children.every(child => !child.visible)).toBe(true);
    expect(root.children.filter(child => child !== casters.group).every(child => (child.children[0] as THREE.Mesh).receiveShadow)).toBe(true);
    const disposeGeometry = vi.spyOn(caster.geometry, "dispose"), disposeMesh = vi.spyOn(caster, "dispose");
    casters.dispose(); expect(disposeGeometry).toHaveBeenCalledOnce(); expect(disposeMesh).toHaveBeenCalledOnce();
    expect(harness.renderer.shadowMap.render).toBe(original); expect(casters.group.parent).toBeNull();
    process.stdout.write("T27 multi-material VSM/throw restoration; capacity growth at 1->6->14 only; caster geometry/GPU resource disposal PASS\n");
  });
});

describe("bicycle shadow exclusion", () => {
  it("leaves the real cloneSkeleton bicycle surfaces, transforms and shadow draws unchanged", () => {
    const root = new THREE.Group(), scene = new THREE.Scene(); scene.add(root);
    const bicycle = cloneSkeleton(bicycleTemplate) as THREE.Group; root.add(bicycle);
    const harness = trafficShadowHarness(scene), beforeDraws = harness.run();
    const beforeCount = beforeDraws.length, before = issuedShadowTriangles(beforeDraws);
    const casters = new TrafficShadowCasters(root, { bicycle: bicycleTemplate }); casters.prepareRenderer(harness.renderer);
    try {
      expect(casters.group.children).toHaveLength(0);
      const afterDraws = harness.run(); expect(afterDraws.length).toBe(beforeCount);
      // The bike-only source is static; CityCyclist adds the animated rider at load time.
      expect(meshes(bicycle).map(mesh => mesh.type)).toEqual(meshes(bicycleTemplate).map(mesh => mesh.type));
      expect(meshes(bicycle).every(mesh => mesh.castShadow)).toBe(true);
      const equality = equivalentTrafficShadows(before, issuedShadowTriangles(afterDraws));
      expect(equality.maximumError).toBe(0);
      process.stdout.write(`T27 real bicycle cloneSkeleton: ${beforeCount} -> ${afterDraws.length} shadow calls; ${equality.count} triangles identical; excluded from instancing PASS\n`);
    } finally { casters.dispose(); }
  });
});
