import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { beforeAll, describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { WebGLShadowMap } from "three/src/renderers/webgl/WebGLShadowMap.js";
import { BuildingRenderBatches } from "./city-building-batch";
import { assembleBuildingVisual, BuildingRenderStreamer, parseBuildingRenderManifest,
  setBuildingRenderLighting, shareBuildingRenderMaterial, validateBuildingRenderGlb,
  type BuildingRenderEntry } from "./city-building-renders";
import { visiblePickObjects } from "./city-rendering";
import { CityLocalReflections } from "./city-local-reflections";
import { PublicTraceMap, type MapView } from "./map";
import { LayerState } from "./state/layers";
import { CITY_WEATHER_CLEAR } from "./city-weather";
const DEFAULT_LAYERS = new LayerState().visibility();
import { AssetResolver } from "./asset-resolver";
import { collectCityCalibratedFacadeMaterials, setCityBuildingCalibration } from "./city-presentation";
import { cityMaterialPaneProfile } from "./city-facade-pane-profile";
import { sampleCityLighting, cityVisualSolar } from "./city-lighting-calibration";
import type { TraceTarget } from "./state/target";

const base = pathToFileURL(resolve("public/building-renders/shanghai-huangpu-east-v1") + "/");
const manifest = parseBuildingRenderManifest(JSON.parse(readFileSync(new URL("manifest.json", base), "utf8")));
// Fixed manifest order by block/id, independent of fetch completion order.
const entries = [...manifest.buildings].sort((a, b) => a.block - b.block || a.object_id.localeCompare(b.object_id)).slice(0, 60);
const templates = new Map<string, THREE.Object3D>();
const shared = new Map<string, THREE.MeshStandardMaterial>();
const textures = new Map<string, THREE.Texture>();
function evidence(line: string): void { process.stdout.write(line + "\n"); }
const TOLERANCE = 2e-5; // Float32 block-local translation; metres for positions/bounds.

beforeAll(async () => {
  for (const entry of manifest.buildings) {
    const raw = readFileSync(new URL(entry.derived_glb.path, base));
    const bytes = Uint8Array.from(raw).buffer;
    const document = validateBuildingRenderGlb(bytes, entry, manifest);
    const loader = new GLTFLoader();
    loader.register(parser => {
      parser.loadTexture = async index => {
        const definition = (document.textures as { source: number }[])[index]!;
        const uri = (document.images as { uri: string }[])[definition.source]!.uri;
        let texture = textures.get(uri);
        if (texture === undefined) { texture = new THREE.Texture(); texture.name = uri; textures.set(uri, texture); }
        return texture;
      };
      return { name: "headless-image-decoding" };
    });
    const gltf = await loader.parseAsync(bytes, "");
    gltf.scene.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      node.castShadow = true; node.receiveShadow = true;
      const replace = (material: THREE.Material) => shareBuildingRenderMaterial(
        material as THREE.MeshStandardMaterial, document, gltf.parser.associations.get(material)!.materials!, shared);
      node.material = Array.isArray(node.material) ? node.material.map(replace) : replace(node.material);
    });
    templates.set(entry.object_id, gltf.scene);
  }
});

/** A WebGL2 fence that is already signalled; `onSignalled` records when the warm-up waited on it. */
function signalledGl(onSignalled: () => void = () => {}) {
  return { SYNC_GPU_COMMANDS_COMPLETE: 0x9117, SYNC_STATUS: 0x9114, SIGNALED: 0x9119,
    fenceSync: () => ({}), flush() {}, isContextLost: () => false, deleteSync() {},
    getSyncParameter() { onSignalled(); return 0x9119; } };
}

function visual(entry: BuildingRenderEntry): THREE.Group {
  const content = templates.get(entry.object_id)!.clone(true);
  const materials = new Set<THREE.MeshStandardMaterial>();
  content.traverse(node => {
    if (node instanceof THREE.Mesh) for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      materials.add(material as THREE.MeshStandardMaterial);
    }
  });
  return assembleBuildingVisual(entry, content, [...materials]);
}
function fixture(selected: readonly BuildingRenderEntry[] = entries) {
  const before = new THREE.Group(), after = new THREE.Group();
  const batches = new BuildingRenderBatches(after, manifest.blocks);
  for (const entry of selected) {
    before.add(visual(entry));
    const owner = visual(entry); after.add(owner); batches.add(owner, entry.block);
  }
  batches.flush(); before.updateMatrixWorld(true); after.updateMatrixWorld(true);
  return { before, after, batches };
}
function meshes(root: THREE.Object3D): THREE.Mesh[] {
  const result: THREE.Mesh[] = [];
  root.traverseVisible(node => { if (node instanceof THREE.Mesh && node.isMesh) result.push(node); });
  return result;
}
function surfaces(root: THREE.Object3D) {
  root.updateWorldMatrix(true, true);
  const result = new Map<THREE.Material, Map<string, number[][]>>();
  for (const mesh of meshes(root)) {
    const geometry = mesh.geometry, p = geometry.getAttribute("position"), n = geometry.getAttribute("normal");
    const uv = geometry.getAttribute("uv"), normalMatrix = new THREE.Matrix3().getNormalMatrix(mesh.matrixWorld);
    const groups = Array.isArray(mesh.material) ? geometry.groups
      : [{ start: 0, count: geometry.index?.count ?? p.count, materialIndex: 0 }];
    for (const group of groups) {
      const material = Array.isArray(mesh.material) ? mesh.material[group.materialIndex!]! : mesh.material;
      const state = JSON.stringify([mesh.receiveShadow, mesh.layers.mask, mesh.renderOrder, Object.keys(geometry.attributes).filter(name => name !== "cityBuildingOrigin").sort()]);
      let states = result.get(material); if (states === undefined) { states = new Map(); result.set(material, states); }
      const corners = states.get(state) ?? []; states.set(state, corners);
      for (let offset = group.start; offset < group.start + group.count; offset++) {
        const index = geometry.index?.getX(offset) ?? offset;
        corners.push([...new THREE.Vector3().fromBufferAttribute(p, index).applyMatrix4(mesh.matrixWorld).toArray(),
          ...new THREE.Vector3().fromBufferAttribute(n, index).applyNormalMatrix(normalMatrix).toArray(), ...(uv === undefined ? [] : [uv.getX(index), uv.getY(index)])]);
      }
    }
  }
  for (const states of result.values()) for (const corners of states.values()) corners.sort((a, b) => {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i]! - b[i]!;
    return 0;
  });
  return result;
}
function equivalent(before: THREE.Object3D, after: THREE.Object3D): number {
  const a = surfaces(before), b = surfaces(after);
  expect(new Set(b.keys())).toEqual(new Set(a.keys()));
  let maxError = 0;
  for (const [material, states] of a) {
    expect(new Set(b.get(material)!.keys())).toEqual(new Set(states.keys()));
    for (const [state, corners] of states) {
      const actual = b.get(material)!.get(state)!;
      expect(actual.length).toBe(corners.length);
      const consumed = new Uint8Array(actual.length);
      for (const corner of corners) {
        let low = 0, high = actual.length;
        while (low < high) {
          const mid = (low + high) >>> 1;
          if (actual[mid]![0]! < corner[0]! - TOLERANCE) low = mid + 1; else high = mid;
        }
        let match = -1;
        for (let i = low; i < actual.length && actual[i]![0]! <= corner[0]! + TOLERANCE; i++) {
          if (consumed[i]) continue;
          const error = Math.max(...corner.map((value, axis) => Math.abs(value - actual[i]![axis]!)));
          if (error <= TOLERANCE) { match = i; maxError = Math.max(maxError, error); break; }
        }
        expect(match, `${material.name}: unmatched corner ${corner}`).toBeGreaterThanOrEqual(0);
        consumed[match] = 1;
      }
      expect(consumed.every(value => value === 1)).toBe(true);
    }
  }
  return maxError;
}
function renderedBounds(root: THREE.Object3D): THREE.Box3 {
  const box = new THREE.Box3();
  for (const states of surfaces(root).values()) for (const corners of states.values()) {
    for (const corner of corners) box.expandByPoint(new THREE.Vector3(...corner.slice(0, 3) as [number, number, number]));
  }
  return box;
}
function pick(root: THREE.Object3D, ray: THREE.Raycaster): { target: TraceTarget; distance: number } | null {
  root.updateWorldMatrix(true, true);
  const hit = ray.intersectObjects(visiblePickObjects([root]), false)[0];
  if (hit === undefined) return null;
  if (hit.faceIndex !== null && hit.faceIndex !== undefined && hit.object.userData.ranges !== undefined) {
    const target = hit.object.userData.ranges.find((range: { end: number }) => hit.faceIndex! < range.end).target;
    return { target, distance: hit.distance };
  }
  let owner = hit.object; while (owner.userData.target === undefined) owner = owner.parent!;
  return { target: owner.userData.target, distance: hit.distance };
}
function applyView(root: THREE.Group, batches: BuildingRenderBatches | null, view: MapView, mood: "day" | "dusk") {
  const map = Object.create(PublicTraceMap.prototype);
  Object.assign(map, { staticView: null, nativePresentation: null, sourceKey: "default-pack", world: new THREE.Group(),
    buildingPresentation: root, renderStreamer: batches === null ? null : { batches }, cityMood: mood,
    selectionOutline: new THREE.Box3Helper(new THREE.Box3()), vegetationPresentation: null,
    authoredLandscapeLayer: null, cityVegetationLayer: null, roadPresentation: null, staticSignalPresentation: null });
  Reflect.get(map, "applyStaticView").call(map, view);
}
const defaultView: MapView = { layers: DEFAULT_LAYERS, hiddenEntities: new Set(), hiddenTrajectories: new Set(),
  isolate: null, selected: null, hovered: null };

// Execute Three's real shadow traversal/getDepthMaterial with a GPU-free renderer.
function shadowHarness(scene: THREE.Scene) {
  const draws: { mesh: THREE.Mesh; material: THREE.Material; depth: THREE.Material; group: THREE.Group | null }[] = [];
  const renderer = {
    getRenderTarget: () => null, getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    setRenderTarget() {}, clear() {},
    state: { setBlending() {}, setScissorTest() {}, viewport() {},
      buffers: { depth: { getReversed: () => false, setTest() {} }, color: { setClear() {} } } },
    properties: { get: () => ({}) }, localClippingEnabled: true,
    renderBufferDirect(_camera: THREE.Camera, _scene: unknown, _geometry: THREE.BufferGeometry,
      depth: THREE.Material, mesh: THREE.Mesh, group: THREE.Group | null) {
      const material = Array.isArray(mesh.material) ? mesh.material[(group as unknown as { materialIndex: number }).materialIndex]! : mesh.material;
      draws.push({ mesh, material, depth: depth.clone(), group });
    },
  };
  const shadowMap = new WebGLShadowMap(renderer as unknown as THREE.WebGLRenderer,
    { update: (mesh: THREE.Mesh) => mesh.geometry } as unknown as ConstructorParameters<typeof WebGLShadowMap>[1],
    { maxTextureSize: 4096 } as ConstructorParameters<typeof WebGLShadowMap>[2]);
  shadowMap.enabled = true;
  const full = Object.assign(renderer, { shadowMap });
  const light = new THREE.DirectionalLight(); light.castShadow = true;
  light.position.set(0, 3000, 0); scene.add(light, light.target);
  Object.assign(light.shadow.camera, { left: -5000, right: 5000, top: 5000, bottom: -5000, near: 1, far: 10000 });
  light.shadow.camera.updateProjectionMatrix(); scene.updateMatrixWorld(true);
  const camera = new THREE.PerspectiveCamera();
  return { renderer: full as unknown as THREE.WebGLRenderer, draws, light, camera,
    run(mainCamera = camera) { draws.length = 0; scene.updateMatrixWorld(true); shadowMap.render([light], scene, mainCamera); return draws; } };
}
function depthState(material: THREE.Material): string {
  const m = material as THREE.MeshStandardMaterial;
  return JSON.stringify([m.visible, m.wireframe, m.side, m.shadowSide, m.alphaTest, m.alphaToCoverage,
    m.alphaMap?.uuid ?? null, m.alphaTest > 0 || m.alphaToCoverage ? m.map?.uuid ?? null : null,
    m.displacementMap?.uuid ?? null, m.displacementScale, m.displacementBias,
    m.clippingPlanes?.map(plane => [...plane.normal.toArray(), plane.constant]) ?? null,
    m.clipShadows, m.clipIntersection, m.wireframeLinewidth, Reflect.get(m, "linewidth")]);
}
function shadowTriangles(root: THREE.Object3D) {
  const result = new Map<string, number[][]>();
  root.updateWorldMatrix(true, true);
  for (const mesh of meshes(root)) {
    if (!mesh.castShadow) continue;
    const geometry = mesh.geometry, position = geometry.getAttribute("position");
    const groups = Array.isArray(mesh.material) ? geometry.groups
      : [{ start: 0, count: geometry.index?.count ?? position.count, materialIndex: 0 }];
    let owner: THREE.Object3D | null = mesh;
    while (owner !== null && owner.userData.target === undefined) owner = owner.parent;
    const block = owner === null ? Number(mesh.name.match(/(?:batch|caster) (\d+)/)![1])
      : manifest.buildings.find(entry => entry.object_id === owner!.userData.target.id)!.block;
    for (const group of groups) {
      const material = Array.isArray(mesh.material) ? mesh.material[group.materialIndex!]! : mesh.material;
      if (!material.visible) continue;
      const key = JSON.stringify([block, depthState(material)]);
      const triangles = result.get(key) ?? []; result.set(key, triangles);
      for (let offset = group.start; offset < group.start + group.count; offset += 3) {
        const triangle: number[] = [];
        for (let corner = 0; corner < 3; corner++) {
          const index = geometry.index?.getX(offset + corner) ?? offset + corner;
          triangle.push(...new THREE.Vector3().fromBufferAttribute(position, index).applyMatrix4(mesh.matrixWorld).toArray());
        }
        triangles.push(triangle);
      }
    }
  }
  for (const triangles of result.values()) triangles.sort((a, b) => a[0]! - b[0]!);
  return result;
}
function equivalentShadows(before: THREE.Object3D, after: THREE.Object3D): number {
  const a = shadowTriangles(before), b = shadowTriangles(after);
  expect(new Set(b.keys())).toEqual(new Set(a.keys()));
  let maxError = 0;
  for (const [state, triangles] of a) {
    const actual = b.get(state)!;
    expect(actual.length).toBe(triangles.length);
    const consumed = new Uint8Array(actual.length);
    for (const triangle of triangles) {
      let match = -1;
      for (let i = 0; i < actual.length; i++) {
        if (consumed[i] || Math.abs(actual[i]![0]! - triangle[0]!) > TOLERANCE) continue;
        let error = 0;
        for (let j = 0; j < 9; j++) error = Math.max(error, Math.abs(actual[i]![j]! - triangle[j]!));
        if (error <= TOLERANCE) { match = i; maxError = Math.max(maxError, error); break; }
      }
      expect(match, `unmatched oriented triangle in ${state}`).toBeGreaterThanOrEqual(0);
      consumed[match] = 1;
    }
  }
  return maxError;
}

describe("real streamed building batching", () => {
  it("casts the same oriented world triangles per block/depth state with fewer real shadow draws", () => {
    for (const selected of [entries, manifest.buildings]) {
      const { before, after, batches } = fixture(selected);
      const scene = new THREE.Scene(); scene.add(after);
      const harness = shadowHarness(scene);
      const original = harness.renderer.shadowMap.render;
      let maxError = 0;
      const inspect = function (this: THREE.WebGLShadowMap, ...args: Parameters<THREE.WebGLShadowMap["render"]>) {
        maxError = equivalentShadows(before, after);
        original.apply(this, args);
      };
      harness.renderer.shadowMap.render = inspect;
      batches.prepareRenderer(harness.renderer); batches.prepareRenderer(harness.renderer);
      const mainBefore = meshes(before).reduce((count, mesh) => count + (Array.isArray(mesh.material) ? mesh.geometry.groups.length : 1), 0);
      const mainAfter = meshes(after).length;
      // Inspect visibility at the exact point where projectObject collects the list.
      expect(meshes(after).some(mesh => mesh.userData.buildingShadowCaster)).toBe(false);
      expect(batches.shadowGroup.children.every(mesh => !mesh.visible)).toBe(true);
      const shadowBefore = meshes(before).filter(mesh => mesh.castShadow)
        .reduce((count, mesh) => count + (Array.isArray(mesh.material) ? mesh.geometry.groups.length : 1), 0);
      const draws = harness.run();
      expect(draws.length).toBeLessThan(meshes(batches.group).length);
      expect(draws.every(draw => draw.mesh.userData.buildingShadowCaster)).toBe(true);
      for (const draw of draws) {
        expect(Object.keys(draw.mesh.geometry.attributes)).toEqual(["position"]);
        const m = draw.material as THREE.MeshStandardMaterial, depth = draw.depth as THREE.MeshDepthMaterial;
        expect(depth.side).toBe(m.shadowSide ?? ({ [THREE.FrontSide]: THREE.BackSide,
          [THREE.BackSide]: THREE.FrontSide, [THREE.DoubleSide]: THREE.DoubleSide })[m.side]);
        expect(depth.alphaTest).toBe(m.alphaTest);
        const bounds = draw.mesh.geometry.boundingBox!;
        const p = draw.mesh.geometry.getAttribute("position"), actual = new THREE.Box3();
        for (let i = 0; i < draw.mesh.geometry.index!.count; i++) actual.expandByPoint(
          new THREE.Vector3().fromBufferAttribute(p, draw.mesh.geometry.index!.getX(i)));
        expect(bounds.min.distanceTo(actual.min)).toBeLessThan(TOLERANCE);
        expect(bounds.max.distanceTo(actual.max)).toBeLessThan(TOLERANCE);
      }
      const count = draws.length;
      // A second camera models a reflection capture main/shadow traversal.
      expect(meshes(after).some(mesh => mesh.userData.buildingShadowCaster)).toBe(false);
      expect(harness.run(new THREE.PerspectiveCamera()).length).toBe(count);
      expect(batches.shadowGroup.children.every(mesh => !mesh.visible)).toBe(true);
      evidence(`T26 N=${selected.length}: main ${mainBefore} -> ${mainAfter}; shadow ${shadowBefore} -> ${count} renderBufferDirect calls; T25 shadow buckets=${meshes(batches.group).length}; oriented triangles max error=${maxError}; tolerance=${TOLERANCE} m; excluded real parts=0`);
      batches.dispose(); expect(harness.renderer.shadowMap.render).toBe(inspect);
    }
  });

  it("keeps batch and caster world matrices clean on frames without parent movement", () => {
    const { after, batches } = fixture();
    after.updateMatrix(); after.matrixAutoUpdate = false; after.updateMatrixWorld(true);
    const recomputed: THREE.Object3D[] = [];
    for (const group of [batches.group, batches.shadowGroup]) {
      expect(group.parent).toBe(after); expect(group.matrixAutoUpdate).toBe(false);
      for (const mesh of group.children) {
        const multiply = mesh.matrixWorld.multiplyMatrices.bind(mesh.matrixWorld);
        mesh.matrixWorld.multiplyMatrices = (a, b) => { recomputed.push(mesh); return multiply(a, b); };
      }
    }
    expect(batches.group.children.length + batches.shadowGroup.children.length).toBeGreaterThan(0);
    after.updateMatrixWorld();
    expect(recomputed).toHaveLength(0);
    after.position.x += 3; after.updateMatrix(); after.updateMatrixWorld();
    expect(new Set(recomputed)).toEqual(new Set([...batches.group.children, ...batches.shadowGroup.children]));
    batches.dispose();
  });

  it("makes a steady flush without array creation or geometry/index/range replacement", () => {
    const { after, batches } = fixture();
    const all = [...batches.group.children, ...batches.shadowGroup.children] as THREE.Mesh[];
    const identities = all.map(mesh => [mesh.geometry, mesh.geometry.index, mesh.userData.ranges]);
    const snapshots = ["slice", "map", "flatMap", "filter", "concat", "splice"] as const;
    const originals = snapshots.map(name => Array.prototype[name]);
    const calls = snapshots.map(() => 0);
    const originalIterator = Array.prototype[Symbol.iterator];
    let iteratorCalls = 0;
    try {
      // Raw counters avoid Vitest's spy recorder, which itself uses array iteration.
      for (let i = 0; i < snapshots.length; i++) Reflect.set(Array.prototype, snapshots[i]!, function (this: unknown[]) {
        calls[i]!++;
        return Reflect.apply(originals[i]!, this, arguments);
      });
      Array.prototype[Symbol.iterator] = function () { iteratorCalls++; return originalIterator.call(this); };
      batches.flush();
    } finally {
      for (let i = 0; i < snapshots.length; i++) Reflect.set(Array.prototype, snapshots[i]!, originals[i]);
      Array.prototype[Symbol.iterator] = originalIterator;
    }
    expect(calls).toEqual(snapshots.map(() => 0));
    // The only array iteration is the existing parts array: no array spread/copy.
    expect(iteratorCalls).toBe(1);
    for (const [i, mesh] of all.entries()) {
      expect(mesh.geometry).toBe(identities[i]![0]);
      expect(mesh.geometry.index).toBe(identities[i]![1]);
      expect(mesh.userData.ranges).toBe(identities[i]![2]);
    }
    expect(meshes(after).some(mesh => mesh.userData.buildingShadowCaster)).toBe(false);
    // A material shared by many buckets has its depth inputs read once per flush.
    const shared = (batches.group.children as THREE.Mesh[]).map(mesh => mesh.material as THREE.Material);
    const material = shared.find(candidate => shared.filter(other => other === candidate).length > 1)!;
    const bucketsSharing = shared.filter(other => other === material).length;
    let alphaTest = material.alphaTest, reads = 0;
    Object.defineProperty(material, "alphaTest", { configurable: true,
      get: () => { reads++; return alphaTest; }, set: value => { alphaTest = value; } });
    try { batches.flush(); } finally {
      Reflect.deleteProperty(material, "alphaTest"); material.alphaTest = alphaTest;
    }
    expect(bucketsSharing).toBeGreaterThan(1);
    expect(reads).toBe(1);
    evidence(`steady flush: slice/map/flatMap/filter/concat/splice calls=0; existing-array iterator=1; geometry/index/range identities unchanged; depth reads for a material shared by ${bucketsSharing} buckets=${reads} PASS`);
    batches.dispose();
  });

  it("separates depth state, retains unsupported textured/custom buckets, and follows in-place changes", () => {
    const root = new THREE.Group(), batches = new BuildingRenderBatches(root, manifest.blocks);
    const materials: THREE.MeshStandardMaterial[] = [], sources: THREE.Mesh[] = [];
    const alphaTexture = new THREE.Texture(), displacement = new THREE.Texture();
    const planes = [new THREE.Plane(new THREE.Vector3(1, 0, 0), 2)];
    const add = (change: (material: THREE.MeshStandardMaterial, mesh: THREE.Mesh) => void = () => {}) => {
      const material = new THREE.MeshStandardMaterial(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), material);
      materials.push(material); sources.push(mesh); mesh.castShadow = mesh.receiveShadow = true;
      const owner = new THREE.Group(), content = new THREE.Group();
      owner.userData.target = { kind: "building", id: String(sources.length) }; content.add(mesh); owner.add(content); root.add(owner);
      change(material, mesh); batches.add(owner, 0);
    };
    add(); add(m => { m.map = new THREE.Texture(); }); // No alpha test: same depth state.
    add(m => { m.side = THREE.DoubleSide; });
    add(m => { m.shadowSide = THREE.FrontSide; });
    add(m => { m.alphaTest = 0.2; }); // Constant opacity test needs positions only.
    add(m => { m.clippingPlanes = planes; m.clipShadows = true; });
    add(m => { m.clippingPlanes = planes; m.clipShadows = true; m.clipIntersection = true; });
    add(m => { m.visible = false; });
    add(m => { m.wireframe = true; m.wireframeLinewidth = 2; });
    add(m => { m.alphaToCoverage = true; });
    add(m => { m.map = alphaTexture; m.alphaTest = 0.2; });
    add(m => { m.alphaMap = alphaTexture; m.alphaTest = 0.2; });
    add(m => { m.map = alphaTexture; m.alphaToCoverage = true; });
    add(m => { m.displacementMap = displacement; m.displacementScale = 2; m.displacementBias = 0.5; });
    add((_m, mesh) => { mesh.customDepthMaterial = new THREE.MeshDepthMaterial(); });
    add((_m, mesh) => { mesh.customDistanceMaterial = new THREE.MeshDistanceMaterial(); });
    add((_m, mesh) => { mesh.onBeforeShadow = () => {}; });
    add((_m, mesh) => { mesh.onAfterShadow = () => {}; });
    batches.flush();
    expect(batches.shadowGroup.children).toHaveLength(9);
    const colour = batches.group.children as THREE.Mesh[];
    for (let i = 0; i < sources.length; i++) {
      const bucket = colour.find(mesh => mesh.material === materials[i])!;
      expect(bucket.castShadow).toBe(i >= 10);
      expect(bucket.receiveShadow).toBe(true);
      expect(bucket.customDepthMaterial).toBe(sources[i]!.customDepthMaterial);
      expect(bucket.customDistanceMaterial).toBe(sources[i]!.customDistanceMaterial);
    }
    const scene = new THREE.Scene(); scene.add(root);
    const harness = shadowHarness(scene); batches.prepareRenderer(harness.renderer);
    const draws = harness.run();
    expect(draws.filter(draw => draw.mesh.userData.buildingShadowCaster)).toHaveLength(8); // invisible depth state is skipped
    expect(draws.filter(draw => !draw.mesh.userData.buildingShadowCaster)).toHaveLength(8);
    for (const draw of draws) {
      const m = draw.material as THREE.MeshStandardMaterial, depth = draw.depth as THREE.MeshDepthMaterial;
      expect(depth.alphaTest).toBe(m.alphaToCoverage ? 0.5 : m.alphaTest);
      expect(depth.alphaMap).toBe(m.alphaMap); expect(depth.map).toBe(m.map);
      expect(depth.clippingPlanes).toEqual(m.clippingPlanes); expect(depth.clipShadows).toBe(m.clipShadows);
      expect(depth.clipIntersection).toBe(m.clipIntersection);
      expect(depth.displacementMap).toBe(m.displacementMap);
      expect(depth.displacementScale).toBe(m.displacementScale); expect(depth.displacementBias).toBe(m.displacementBias);
    }
    materials[1]!.alphaTest = 0.3; batches.flush();
    expect(colour.find(mesh => mesh.material === materials[1])!.castShadow).toBe(true);
    materials[1]!.alphaTest = 0; batches.flush();
    expect(colour.find(mesh => mesh.material === materials[1])!.castShadow).toBe(false);
    materials[0]!.side = THREE.BackSide; batches.flush();
    expect(batches.shadowGroup.children).toHaveLength(10);
    const original = harness.renderer.shadowMap.render;
    // A thrown shadow draw must restore both main-pass visibility and receiveShadow.
    harness.renderer.shadowMap.render = original;
    const other = shadowHarness(scene);
    const failure = () => { throw new Error("shadow failure"); };
    other.renderer.shadowMap.render = failure; batches.prepareRenderer(other.renderer);
    expect(() => other.run()).toThrow("shadow failure");
    expect(batches.shadowGroup.children.every(mesh => !mesh.visible)).toBe(true);
    expect(colour.every(mesh => mesh.receiveShadow)).toBe(true);
    batches.dispose(); expect(other.renderer.shadowMap.render).toBe(failure);
    for (const m of materials) m.dispose(); alphaTexture.dispose(); displacement.dispose();
    evidence("depth state/fallback/in-place mutation/exception restoration PASS; synthetic exclusions: textured alpha test/coverage, active displacement, custom depth/distance material, shadow callbacks");
  });

  it("avoids duplicate VSM receiver draws and preserves noncasting receivers", () => {
    const { before, after, batches } = fixture();
    // A genuine noncasting receiver remains eligible for VSM's receiveShadow branch.
    const extra = visual(entries[0]!);
    extra.traverse(node => { if (node instanceof THREE.Mesh) node.castShadow = false; });
    after.add(extra); batches.add(extra, entries[0]!.block); batches.flush();
    const scene = new THREE.Scene(); scene.add(after);
    const harness = shadowHarness(scene); batches.prepareRenderer(harness.renderer);
    harness.renderer.shadowMap.type = THREE.VSMShadowMap;
    const draws = harness.run().filter(draw => draw.mesh.userData.buildingShadowCaster);
    expect(draws.some(draw => !draw.mesh.castShadow && draw.mesh.receiveShadow)).toBe(true);
    expect(harness.draws.filter(draw => draw.mesh.name.startsWith("Building batch"))).toHaveLength(0);
    expect((batches.group.children as THREE.Mesh[]).every(mesh => mesh.receiveShadow)).toBe(true);
    expect(meshes(after).some(mesh => mesh.userData.buildingShadowCaster)).toBe(false);
    batches.dispose(); void before;
    evidence("VSM covered colour receivers suppressed only inside shadow pass; noncasting receivers retained PASS");
  });

  it("preserves sorted corner multisets, material identities, shadow flags and block/owner bounds", () => {
    const { before, after, batches } = fixture();
    expect(new Set(entries.map(entry => entry.block)).size).toBeGreaterThanOrEqual(3);
    const maxError = equivalent(before, after);
    const a = renderedBounds(before), b = renderedBounds(after);
    for (const endpoint of ["min", "max"] as const) expect(a[endpoint].distanceTo(b[endpoint])).toBeLessThan(TOLERANCE);
    for (const entry of entries) {
      const original = before.children.find(child => child.name === entry.object_id)!;
      const owner = after.children.find(child => child.name === entry.object_id)!;
      expect(owner.userData).toEqual(original.userData);
      expect(new THREE.Box3().setFromObject(owner).equals(new THREE.Box3().setFromObject(original))).toBe(true);
      expect(owner.children.at(-1)!.userData.collisionProxy).toBe(true);
    }
    for (const batch of meshes(batches.group)) {
      const expected = renderedBounds(batch).translate(batch.position.clone().negate());
      expect(batch.geometry.boundingBox!.min.distanceTo(expected.min)).toBeLessThan(TOLERANCE);
      expect(batch.geometry.boundingBox!.max.distanceTo(expected.max)).toBeLessThan(TOLERANCE);
      expect(batch.frustumCulled).toBe(true);
    }
    evidence(`real manifest GLBs N=${entries.length}, blocks=${new Set(entries.map(entry => entry.block)).size}; `
      + `positions/normals/UV sorted multisets, material identity, shadows, owner/block bounds PASS; max error=${maxError}; tolerance=${TOLERANCE}`);
    batches.dispose();
  });

  it.each([
    ["layer off", { ...defaultView, layers: { ...DEFAULT_LAYERS, buildings: false } }, "day"],
    ["one hidden", { ...defaultView, hiddenEntities: new Set([`building:${entries[17]!.object_id}`]) }, "day"],
    ["isolate one", { ...defaultView, isolate: { kind: "building", id: entries[17]!.object_id } }, "day"],
    ["storefront day", defaultView, "day"],
    ["storefront dusk", defaultView, "dusk"],
  ] as const)("matches the actual map visibility loop: %s", (name, view, mood) => {
    const { before, after, batches } = fixture();
    if (name.startsWith("storefront")) {
      before.children[12]!.userData.cityStorefrontLight = true;
      after.children.find(child => child.name === entries[12]!.object_id)!.userData.cityStorefrontLight = true;
    }
    applyView(before, null, view as MapView, mood); applyView(after, batches, view as MapView, mood);
    equivalent(before, after);
    const scene = new THREE.Scene(); scene.add(after);
    const harness = shadowHarness(scene), original = harness.renderer.shadowMap.render;
    harness.renderer.shadowMap.render = function (lights, scene, camera) {
      equivalentShadows(before, after);
      original.call(this, lights, scene, camera);
    };
    batches.prepareRenderer(harness.renderer); harness.run();
    const casterTargets = new Set((batches.shadowGroup.children as THREE.Mesh[]).flatMap(mesh => mesh.userData.ranges.map((range: { target: TraceTarget }) => range.target.id)));
    const expected = before.children.filter(child => child.visible).map(child => child.userData.target.id).sort();
    const actual = [...new Set(meshes(batches.group).flatMap(mesh => mesh.userData.ranges.map((range: { target: TraceTarget }) => range.target.id)))].sort();
    expect(actual).toEqual(expected); expect([...casterTargets].sort()).toEqual(expected);
    evidence(`visibility ${name}: rendered buildings=${actual.length}; per-building rule PASS`);
    applyView(before, null, defaultView, "dusk"); applyView(after, batches, defaultView, "dusk");
    equivalent(before, after); // Re-showing hidden indices must restore every surface.
    harness.run(); // The same rule restores every shadow triangle too.
    batches.dispose();
  });

  it("culls batches outside both a main camera and a shadow camera frustum", () => {
    const { before, batches } = fixture();
    const entry = entries[17]!, owner = before.children.find(child => child.name === entry.object_id)!;
    const box = new THREE.Box3().setFromObject(owner), center = box.getCenter(new THREE.Vector3());
    for (const camera of [new THREE.PerspectiveCamera(30, 1, 0.1, 400),
      new THREE.OrthographicCamera(-30, 30, 30, -30, 0.1, 400)]) {
      camera.position.copy(center).add(new THREE.Vector3(0, 200, 0)); camera.up.set(0, 0, -1);
      camera.lookAt(center); camera.updateProjectionMatrix(); camera.updateMatrixWorld(true);
      const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4()
        .multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
      const visible = meshes(batches.group).filter(mesh => frustum.intersectsObject(mesh));
      expect(visible.length).toBeGreaterThan(0); expect(visible.length).toBeLessThan(meshes(batches.group).length);
      expect(visible.some(mesh => mesh.userData.ranges.some((range: { target: TraceTarget }) => range.target.id === entry.object_id))).toBe(true);
    }
    batches.dispose(); evidence("main/shadow camera frusta: tight batch bounds include chosen building and cull distant blocks PASS");
  });

  it("returns the same target and hit distance before/after compaction and isolation", () => {
    const { before, after, batches } = fixture();
    const entry = entries[17]!;
    const owner = before.children.find(child => child.name === entry.object_id)!;
    const box = new THREE.Box3().setFromObject(owner), center = box.getCenter(new THREE.Vector3());
    const ray = new THREE.Raycaster(new THREE.Vector3(center.x, box.max.y + 100, center.z), new THREE.Vector3(0, -1, 0));
    const a = pick(before, ray), b = pick(after, ray);
    for (const caster of batches.shadowGroup.children) {
      caster.visible = true;
      expect(ray.intersectObject(caster, true)).toEqual([]);
      caster.visible = false;
    }
    expect(ray.intersectObjects(batches.shadowGroup.children, true)).toEqual([]);
    expect(a?.target).toEqual({ kind: "building", id: entry.object_id }); expect(b?.target).toEqual(a?.target);
    expect(Math.abs(a!.distance - b!.distance)).toBeLessThan(TOLERANCE);
    const view = { ...defaultView, isolate: a!.target };
    applyView(before, null, view, "day"); applyView(after, batches, view, "day");
    expect(pick(after, ray)?.target).toEqual(a!.target);
    applyView(after, batches, { ...view, hiddenEntities: new Set([`building:${entry.object_id}`]) }, "day");
    expect(pick(after, ray)).toBeNull();
    evidence(`picking ${entry.object_id}: original/batched/isolated target PASS; hidden target absent`);
    batches.dispose();
  });

  it("retains the T25 main-pass draw reduction and material identities", () => {
    const counts = (root: THREE.Object3D, shadow: boolean) => {
      const result = new Map<string, number>();
      for (const mesh of meshes(root)) {
        if (shadow && !mesh.castShadow) continue;
        const materials = Array.isArray(mesh.material) ? mesh.geometry.groups.map(group => (mesh.material as THREE.Material[])[group.materialIndex!]!) : [mesh.material];
        for (const material of materials) result.set(material.name, (result.get(material.name) ?? 0) + 1);
      }
      return result;
    };
    for (const selected of [entries, manifest.buildings]) {
      const { before, batches } = fixture(selected);
    for (const shadow of [false]) {
      const a = counts(before, shadow), b = counts(batches.group, shadow);
      expect([...b.values()].reduce((x, y) => x + y, 0)).toBeLessThan([...a.values()].reduce((x, y) => x + y, 0));
      for (const [name, count] of a) evidence(`N=${selected.length} ${shadow ? "shadow" : "main"} ${name}: ${count} -> ${b.get(name) ?? 0} draw items`);
    }
      const identityCounts = (root: THREE.Object3D) => {
        const result = new Map<THREE.Material, number>();
        for (const mesh of meshes(root)) {
          const materials = Array.isArray(mesh.material) ? mesh.geometry.groups.map(group => (mesh.material as THREE.Material[])[group.materialIndex!]!) : [mesh.material];
          for (const material of materials) result.set(material, (result.get(material) ?? 0) + 1);
        }
        return result;
      };
      const original = identityCounts(before), merged = identityCounts(batches.group);
      expect(new Set(merged.keys())).toEqual(new Set(original.keys()));
      const variants = new Map<string, number>();
      for (const [material, count] of original) {
        const variant = (variants.get(material.name) ?? 0) + 1; variants.set(material.name, variant);
        evidence(`N=${selected.length} material identity ${material.name} variant ${variant}: main ${count} -> ${merged.get(material)} each`);
      }
      batches.dispose();
    }
  });

  it("keeps per-surface shadow flags separate", () => {
    const { before, after, batches } = fixture(); batches.dispose();
    const next = new BuildingRenderBatches(after, manifest.blocks);
    // Fresh sources are required after the first fixture replaced their renderer flags.
    after.clear();
    for (const [i, entry] of entries.entries()) {
      const owner = visual(entry); after.add(owner);
      for (const root of [before.children[i]!, owner]) root.traverse(node => {
        if (node instanceof THREE.Mesh) { node.castShadow = i % 2 === 0; node.receiveShadow = i % 3 === 0; }
      });
      next.add(owner, entry.block);
    }
    next.flush(); equivalent(before, after);
    const scene = new THREE.Scene(); scene.add(after);
    const harness = shadowHarness(scene), original = harness.renderer.shadowMap.render;
    harness.renderer.shadowMap.render = function (lights, scene, camera) {
      equivalentShadows(before, after); original.call(this, lights, scene, camera);
    };
    next.prepareRenderer(harness.renderer); harness.run(); next.dispose();
  });

  it("preserves mood/calibration/window registries and per-building local reflection materials", () => {
    const { before, after, batches } = fixture();
    const environment = new THREE.Texture();
    setBuildingRenderLighting(after, "night", environment);
    for (const mesh of meshes(batches.group)) expect((mesh.material as THREE.MeshStandardMaterial).envMap).toBe(environment);
    const sample = sampleCityLighting({ solar: cityVisualSolar("night"), weather: { kind: "visual-settings", settings: CITY_WEATHER_CLEAR } });
    // Geometry/material contracts use real GLBs. Image decoding and canvas pixels
    // are headless fixtures; the authored pane profile drives the mask itself.
    for (const material of shared.values()) {
      const profile = cityMaterialPaneProfile(material);
      if (profile === null) continue;
      for (const texture of [material.map, material.normalMap, material.emissiveMap]) {
        texture!.image = { width: profile.tile_size_px[0], height: profile.tile_size_px[1] };
        texture!.wrapS = texture!.wrapT = THREE.RepeatWrapping;
      }
    }
    const canvas = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ({
      drawImage() {}, clearRect() {}, getImageData(_x: number, _y: number, width: number, height: number) {
        return { data: new Uint8ClampedArray(width * height * 4) };
      },
    }) as unknown as CanvasRenderingContext2D);
    try { setCityBuildingCalibration(before, sample, environment); setCityBuildingCalibration(after, sample, environment); }
    finally { canvas.mockRestore(); }
    batches.flush();
    for (const mesh of meshes(batches.group)) {
      const material = mesh.material as THREE.Material;
      if (material.userData.cityWindowLights !== true) continue;
      const shader = { uniforms: {}, vertexShader: "#include <common>\n#include <project_vertex>", fragmentShader: "#include <common>\n#include <emissivemap_fragment>" };
      material.onBeforeCompile(shader as THREE.WebGLProgramParametersWithUniforms, {} as THREE.WebGLRenderer);
      expect(shader.vertexShader).toContain("attribute vec3 cityBuildingOrigin;");
      expect(shader.vertexShader).toContain("(modelMatrix * vec4(cityBuildingOrigin, 1.0)).xz");
      const origins = mesh.geometry.getAttribute("cityBuildingOrigin");
      let start = 0;
      for (const range of mesh.userData.ranges as { end: number; target: TraceTarget }[]) {
        const source = before.children.find(owner => owner.userData.target.id === range.target.id)!;
        const expected = source.getWorldPosition(new THREE.Vector3());
        for (let i = start * 3; i < range.end * 3; i++) {
          const vertex = mesh.geometry.index!.getX(i);
          const origin = new THREE.Vector3().fromBufferAttribute(origins, vertex).applyMatrix4(mesh.matrixWorld);
          expect(origin.distanceTo(expected)).toBeLessThan(TOLERANCE);
          expect([Math.floor(origin.x) % 251, Math.floor(origin.z) % 251]).toEqual([Math.floor(expected.x) % 251, Math.floor(expected.z) % 251]);
        }
        start = range.end;
      }
    }
    expect(new Set(collectCityCalibratedFacadeMaterials(after))).toEqual(new Set(collectCityCalibratedFacadeMaterials(before)));
    const renderer = {} as THREE.WebGLRenderer;
    const scene = new THREE.Scene(); scene.add(after);
    const focus = new THREE.Box3().setFromObject(after.children[0]!).getCenter(new THREE.Vector3());
    const probe = new CityLocalReflections(renderer, scene); probe.configure(after, focus, true);
    expect(probe.selectedBuildings().length).toBeGreaterThan(0);
    // The renderer's matrix update must follow probe-assigned material identities.
    scene.updateMatrixWorld(true);
    const clones = meshes(batches.group).filter(mesh => (mesh.material as THREE.Material).name.includes("local street reflection"));
    expect(clones.length).toBeGreaterThan(0);
    const sourceMaterials: THREE.Material[] = [];
    for (const owner of probe.selectedBuildings()) owner.traverse(node => {
      if (node instanceof THREE.Mesh) sourceMaterials.push(...(Array.isArray(node.material) ? node.material : [node.material]));
    });
    for (const mesh of clones) expect(sourceMaterials).toContain(mesh.material);
    probe.dispose(); scene.updateMatrixWorld(true);
    expect(meshes(batches.group).some(mesh => (mesh.material as THREE.Material).name.includes("local street reflection"))).toBe(false);
    equivalent(before, after); batches.dispose(); environment.dispose();
    evidence("mood/calibration/window registry/local reflection material assignment/restoration PASS");
  });

  it("streams before loadAll, follows a hidden arriving owner, compiles real batch variants and cancels pending frames", async () => {
    let callback: FrameRequestCallback | undefined;
    vi.stubGlobal("requestAnimationFrame", vi.fn((cb: FrameRequestCallback) => { callback = cb; return 123; }));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const resolver = new AssetResolver({ baseHref: "https://test.invalid/" });
    vi.spyOn(resolver, "fetchVerifiedBytes").mockResolvedValue(new ArrayBuffer(1));
    const group = new THREE.Group(), placed: string[] = [], progress: number[] = [], requestRender = vi.fn();
    const streamer = new BuildingRenderStreamer({ manifest: { ...manifest, buildings: entries }, resolver, group, concurrency: 1,
      parseBuilding: async (_bytes, entry) => visual(entry), onPlaced: owner => {
        placed.push(owner.name); if (owner.name === entries[17]!.object_id) owner.visible = false;
      }, onProgress: state => progress.push(state.loaded), requestRender });
    try {
      const loading = streamer.loadAll();
      // Let the first verified building settle without waiting for the whole city.
      for (let i = 0; i < 5; i++) await Promise.resolve();
      expect(streamer.progress.loaded).toBeGreaterThan(0); expect(streamer.progress.loaded).toBeLessThan(60);
      callback!(0); expect(meshes(streamer.batches.group).length).toBeGreaterThan(0);
      await loading;
      expect(placed).toEqual(entries.map(entry => entry.object_id));
      expect(progress).toEqual(Array.from({ length: 60 }, (_, i) => i + 1));
      expect(streamer.progress).toMatchObject({ total: 60, loaded: 60, active: 0, failed: 0 });
      expect(meshes(streamer.batches.group).flatMap(mesh => mesh.userData.ranges).some(range => range.target.id === entries[17]!.object_id)).toBe(false);
      const uploaded = new Set<THREE.Texture>(), compiled = new Set<THREE.Material>();
      let gpuIdle = 0; // The queued uploads and links must drain before the warm render's synchronous queries.
      const scene = new THREE.Scene(); scene.add(group);
      const harness = shadowHarness(scene), originalShadow = harness.renderer.shadowMap.render;
      // The actual map focuses its sun after prepareRenderer. Warm depth programs
      // even when the initial shadow frustum misses every loaded building.
      harness.light.position.set(10000, 3000, 10000); harness.light.target.position.set(10000, 0, 10000);
      Object.assign(harness.light.shadow.camera, { left: -1, right: 1, top: 1, bottom: -1 });
      harness.light.shadow.camera.updateProjectionMatrix();
      expect(harness.run()).toHaveLength(0);
      const renderer = Object.assign(harness.renderer, { initTexture: (texture: THREE.Texture) => { uploaded.add(texture); },
        compileAsync: vi.fn(async (root: THREE.Object3D) => { for (const mesh of meshes(root)) compiled.add(mesh.material as THREE.Material); }),
        getContext: () => signalledGl(() => { gpuIdle++; }),
        render: vi.fn((_scene: THREE.Scene, _view: THREE.Camera) => { expect(gpuIdle).toBe(1); harness.run(); }) });
      // Texture uploads are sliced per animation frame; let those frames run.
      const pending = callback;
      vi.mocked(requestAnimationFrame).mockImplementation(cb => { queueMicrotask(() => cb(0)); return 124; });
      const mapCamera = new THREE.PerspectiveCamera(); mapCamera.layers.enable(4);
      await streamer.prepareRenderer(renderer, mapCamera, scene);
      vi.mocked(requestAnimationFrame).mockImplementation(cb => { callback = cb; return 123; });
      expect(callback).toBe(pending);
      expect(harness.draws.length).toBeGreaterThan(0);
      expect(harness.draws.every(draw => Reflect.get(draw.depth, "isMeshDepthMaterial") === true)).toBe(true);
      expect(renderer.render).toHaveBeenCalledOnce();
      // The warm render's main view excludes the city, so it uploads no further textures.
      const view = renderer.render.mock.calls[0]![1];
      expect(view).not.toBe(mapCamera); expect(view.layers.mask).toBe(mapCamera.layers.mask);
      expect(harness.run()).toHaveLength(0); // Warm-up restored ordinary shadow culling.
      expect(streamer.batches.shadowGroup.children.every(mesh => !mesh.visible && mesh instanceof THREE.Mesh && mesh.frustumCulled)).toBe(true);
      expect(compiled).toEqual(new Set(meshes(streamer.batches.group).map(mesh => mesh.material)));
      for (const material of compiled) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) expect(uploaded.has(value)).toBe(true);
        if (material.userData.cityWindowLights === true) {
          const shader = { uniforms: {}, vertexShader: "#include <common>\n#include <project_vertex>", fragmentShader: "#include <common>\n#include <emissivemap_fragment>" };
          material.onBeforeCompile(shader as THREE.WebGLProgramParametersWithUniforms, renderer as unknown as THREE.WebGLRenderer);
          expect(shader.vertexShader.match(/attribute vec3 cityBuildingOrigin;/g)).toHaveLength(1);
        }
      }
      streamer.dispose(); expect(renderer.shadowMap.render).toBe(originalShadow); expect(cancelAnimationFrame).toHaveBeenCalledWith(123);
      expect(streamer.batches.group.parent).toBeNull();
      const count = requestRender.mock.calls.length; callback!(0); expect(requestRender).toHaveBeenCalledTimes(count);
      evidence("streaming order/progress/early appearance/late hidden owner/loadAll/compile/dispose PASS");
    } finally { streamer.dispose(); resolver.dispose(); vi.unstubAllGlobals(); }
  });
});
