// @vitest-environment node
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { AssetResolver, AssetResolutionError, type DeclaredAsset } from "./asset-resolver";
import {
  BUILDING_RENDER_PLACEMENT_RULE,
  BuildingRenderStreamer,
  assembleBuildingVisual,
  buildingPlacement,
  buildingWorldPosition,
  fetchBuildingRenderManifest,
  parseBuildingRenderManifest,
  readGlbDocument,
  setBuildingRenderLighting,
  shareBuildingRenderMaterial,
  texturePlan,
  validateBuildingRenderGlb,
  type BuildingRenderEntry,
  type BuildingRenderManifest,
} from "./city-building-renders";
import { CITY_BUILDING_MATERIAL_ROLE_KEY, CITY_FACADE_PANE_PROFILE_KEY } from "./city-facade-pane-profile";
import { parseCitySceneConfig } from "./city-scene-config";

const MANIFEST_PATH = new URL(
  "../public/building-renders/shanghai-huangpu-east-v1/manifest.json", import.meta.url);
const SCENE_PATH = new URL(
  "../public/city-presentation/building-render-scene-v1.json", import.meta.url);
const manifestBytes = readFileSync(MANIFEST_PATH);
const manifestSha256 = createHash("sha256").update(manifestBytes).digest("hex");
const rawManifest: unknown = JSON.parse(manifestBytes.toString("utf8"));
const sceneConfig = parseCitySceneConfig(JSON.parse(readFileSync(SCENE_PATH, "utf8")));
const manifest: BuildingRenderManifest = parseBuildingRenderManifest(rawManifest);
// Dataset pins belong to this recorded fixture, not the generic runtime parser.
const BUILDING_RENDER_EXPECTED_BUILDINGS = 414;
const BUILDING_RENDER_BLOCK_SIZE_M = 120;
const BUILDING_RENDER_OBJECTS_SHA256 = "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc";
const BUILDING_RENDER_COMBINED_SHA256 = "ab6c82860157928e85f5a26b6baec1144b98915901c383873aaecf3acfbf256a";

function digestHex(bytes: ArrayBuffer): Promise<string> {
  return Promise.resolve(createHash("sha256").update(new Uint8Array(bytes)).digest("hex"));
}

interface StubFetchOptions {
  readonly manifest?: Uint8Array<ArrayBuffer>;
}

function stubResolver(options: StubFetchOptions = {}): AssetResolver {
  const fetchStub = (async (input: URL | string | Request) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    if (url.pathname.endsWith(`assets/${manifestSha256}`) && options.manifest !== undefined) {
      return new Response(options.manifest, { status: 200 });
    }
    return new Response(null, { status: 404 });
  }) as unknown as typeof fetch;
  return new AssetResolver({
    baseHref: "https://viewer.test/building-renders/shanghai-huangpu-east-v1/",
    digest: digestHex,
    fetch: fetchStub,
  });
}

/**
 * Streamer-level resolver: returns zero-filled bytes of the declared size.
 * Digest verification itself is covered by the fetchBuildingRenderManifest tests.
 */
function fakeStreamResolver(failSha?: string): AssetResolver {
  const resolver = new AssetResolver({
    baseHref: "https://viewer.test/building-renders/shanghai-huangpu-east-v1/",
    digest: digestHex,
    fetch: (async () => new Response(null, { status: 500 })) as unknown as typeof fetch,
  });
  const sizes = new Map<string, number>();
  for (const entry of manifest.buildings) sizes.set(entry.derived_glb.path, entry.derived_glb.bytes);
  const failTarget = failSha === undefined ? null : `assets/${failSha}`;
  resolver.fetchVerifiedBytes = async (replayPath: string, declared: DeclaredAsset) => {
    if (replayPath === failTarget) throw new Error("simulated fetch failure");
    const size = sizes.get(replayPath);
    if (size === undefined || size !== declared.sizeBytes) {
      throw new Error(`stub has no size for ${replayPath}`);
    }
    return new ArrayBuffer(size);
  };
  return resolver;
}

function fakeParse(bytes: ArrayBuffer, entry: BuildingRenderEntry): Promise<THREE.Object3D> {
  void bytes;
  return Promise.resolve(assembleBuildingVisual(entry, new THREE.Object3D(), []));
}

describe("verified building rendering costs", () => {
  const document = { materials: [{ name: "facade", extras: { role: "facade" } }],
    images: [{ uri: `assets/${"a".repeat(64)}` }], textures: [{ source: 0 }], samplers: [{ wrapS: 10497 }] };

  it("shares equivalent opaque source materials and disposes only the duplicate material", () => {
    const cache = new Map<string, THREE.MeshStandardMaterial>();
    const first = new THREE.MeshStandardMaterial(), second = new THREE.MeshStandardMaterial();
    const texture = new THREE.Texture(); first.map = second.map = texture;
    const duplicateDisposed = vi.fn(), textureDisposed = vi.fn();
    second.addEventListener("dispose", duplicateDisposed); texture.addEventListener("dispose", textureDisposed);
    expect(shareBuildingRenderMaterial(first, document, 0, cache)).toBe(first);
    // The cached material may already have received a mood or weather calibration.
    first.emissiveIntensity = 1.8;
    expect(shareBuildingRenderMaterial(second, structuredClone(document), 0, cache)).toBe(first);
    expect(first.emissiveIntensity).toBe(1.8);
    expect(duplicateDisposed).toHaveBeenCalledOnce(); expect(textureDisposed).not.toHaveBeenCalled();
    first.dispose(); texture.dispose();
  });

  it("keeps source profiles, textures, samplers and loader geometry variants distinct", () => {
    const cache = new Map<string, THREE.MeshStandardMaterial>();
    const first = new THREE.MeshStandardMaterial();
    shareBuildingRenderMaterial(first, document, 0, cache);
    for (const changed of [
      { ...document, materials: [{ name: "facade", extras: { role: "roof" } }] },
      { ...document, images: [{ uri: `assets/${"b".repeat(64)}` }] },
      { ...document, samplers: [{ wrapS: 33071 }] },
    ]) {
      const material = new THREE.MeshStandardMaterial();
      expect(shareBuildingRenderMaterial(material, changed, 0, cache)).toBe(material);
    }
    const variant = new THREE.MeshStandardMaterial(); variant.normalScale.y = -1;
    expect(shareBuildingRenderMaterial(variant, document, 0, cache)).toBe(variant);
    for (const material of cache.values()) material.dispose();
  });

  it("does not share transparent or non-depth-writing materials and rejects missing definitions", () => {
    const cache = new Map<string, THREE.MeshStandardMaterial>();
    for (const options of [{ transparent: true }, { opacity: 0.5 }, { depthWrite: false }]) {
      const material = new THREE.MeshStandardMaterial(options);
      expect(shareBuildingRenderMaterial(material, document, 0, cache)).toBe(material); material.dispose();
    }
    expect(cache.size).toBe(0);
    const missing = new THREE.MeshStandardMaterial();
    expect(() => shareBuildingRenderMaterial(missing, document, 2, cache)).toThrow(/source definition/);
    missing.dispose();
  });

  it("caches the placed visual statically while retaining parent transform inheritance", () => {
    const content = new THREE.Group(), child = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
    child.position.set(2, 3, 4); content.add(child);
    const update = vi.spyOn(child, "updateMatrix");
    const visual = assembleBuildingVisual(manifest.buildings[0]!, content, [child.material]);
    expect(visual.matrixAutoUpdate).toBe(false); expect(content.matrixAutoUpdate).toBe(false);
    expect(child.matrixAutoUpdate).toBe(false); expect(update).toHaveBeenCalledOnce();
    const parent = new THREE.Group(); parent.add(visual); parent.updateMatrixWorld(true);
    const before = child.getWorldPosition(new THREE.Vector3());
    expect(before.toArray()).toEqual(visual.position.clone().add(child.position).toArray());
    parent.position.z += 7; parent.updateMatrixWorld();
    expect(child.getWorldPosition(new THREE.Vector3()).sub(before).toArray()).toEqual([0, 0, 7]);
    // Without parent movement a frame leaves every placed node clean: nothing is recomputed.
    const recompute = vi.spyOn(child.matrixWorld, "multiplyMatrices");
    visual.updateMatrixWorld();
    expect(recompute).not.toHaveBeenCalled();
    visual.traverse(node => expect(node.matrixWorldNeedsUpdate).toBe(false));
    expect(update).toHaveBeenCalledOnce(); child.geometry.dispose(); child.material.dispose();
  });
});

describe("building render manifest mapping and digests", () => {
  it("binds all 414 objects to content-addressed GLBs with pinned provenance", () => {
    expect(manifest.schema_version).toBe("aero-bench.building-render-runtime/v2");
    expect(manifest.counts.buildings).toBe(BUILDING_RENDER_EXPECTED_BUILDINGS);
    expect(manifest.buildings).toHaveLength(414);
    expect(manifest.counts.total_bytes).toBe(101_076_596);
    expect(manifest.counts.derived_total_bytes).toBe(
      manifest.buildings.reduce((total, entry) => total + entry.derived_glb.bytes, 0));
    expect(manifest.counts.derived_total_bytes + manifest.counts.texture_bytes).toBeLessThan(7_000_000);
    // Active middle-tile crops; the 2,725,934 B source originals are archived separately.
    expect(manifest.counts.texture_bytes).toBe(1_503_596);
    expect(manifest.counts.textures).toBe(45);
    expect(manifest.textures).toHaveLength(45);
    expect(manifest.counts.styles).toBe(15);
    expect(manifest.counts.blocks).toBe(78);
    // Art detail is explicitly labelled non-survey render art in the manifest.
    const rawRoot = rawManifest as Record<string, unknown>;
    const rawCounts = rawRoot.counts as Record<string, number>;
    expect(rawCounts.art_detail_buildings).toBe(414);
    expect(rawCounts.art_detail_relief_buildings).toBe(0);
    expect(rawCounts.art_detail_roofs).toBeGreaterThan(0);
    expect(rawCounts.art_detail_roofs).toBeLessThanOrEqual(manifest.counts.buildings);
    expect(rawCounts.art_detail_structures).toBeGreaterThan(0);
    expect(rawCounts.art_detail_structures).toBeLessThanOrEqual(rawCounts.art_detail_roofs!);
    expect(rawCounts.derived_vertices).toBeGreaterThan(rawCounts.source_vertices!);
    expect(rawCounts.derived_triangles).toBeGreaterThan(rawCounts.source_triangles!);
    const artBlock = rawRoot.art_detail as Record<string, unknown>;
    expect(artBlock.schema).toBe("aero-bench.building-render-art-detail/v1");
    expect(artBlock.class).toBe("derived_render_art_only_not_survey");
    expect(artBlock.layers).toEqual({ roof_detail: true, facade_relief: false });
    expect(String(artBlock.disclaimer_zh)).toMatch(/非测绘事实/);
    expect(String(artBlock.disclaimer_en)).toMatch(/not survey/);
    expect(manifest.block_size_m).toBe(BUILDING_RENDER_BLOCK_SIZE_M);
    expect(manifest.scene.objects_json_sha256).toBe(BUILDING_RENDER_OBJECTS_SHA256);
    expect(manifest.scene.placement_rule).toBe(BUILDING_RENDER_PLACEMENT_RULE);
    expect(manifest.sources.scaleout_glb_combined_sha256).toBe(BUILDING_RENDER_COMBINED_SHA256);
    const ids = manifest.buildings.map(entry => entry.object_id);
    expect(new Set(ids).size).toBe(414);
    const digests = manifest.buildings.map(entry => entry.glb.sha256);
    expect(new Set(digests).size).toBe(414);
    const derivedDigests = manifest.buildings.map(entry => entry.derived_glb.sha256);
    expect(new Set(derivedDigests).size).toBe(414);
    for (const entry of manifest.buildings) {
      expect(entry.glb.path).toBe(`assets/${entry.glb.sha256}`);
      expect(entry.glb.sha256).toMatch(/^[0-9a-f]{64}$/);
      expect(entry.derived_glb.path).toBe(`assets/${entry.derived_glb.sha256}`);
      expect(entry.derived_glb.sha256).toMatch(/^[0-9a-f]{64}$/);
      expect(entry.derived_glb.sha256).not.toBe(entry.glb.sha256);
    }
    const textureDigests = manifest.textures.map(texture => texture.sha256);
    expect(new Set(textureDigests).size).toBe(45);
    expect(textureDigests).toEqual([...textureDigests].sort());
    const textureSet = new Set(textureDigests);
    for (const entry of manifest.buildings) {
      expect(textureSet.has(entry.derived_glb.sha256)).toBe(false);
      expect(textureSet.has(entry.glb.sha256)).toBe(false);
    }
    // The 414 ↔ 410 pack discrepancy stays explicit: never a silent mix.
    expect(manifest.counts.pack_linked_objects).toBe(410);
    expect(manifest.counts.pack_missing_objects).toBe(4);
    expect(manifest.counts.pack_targets).toBe(411);
    expect(manifest.pack_missing_object_ids).toEqual([
      "building.way.378107085.component.0",
      "building.way.447021393.component.0",
      "building.way.447021395.component.0",
      "building.way.447021396.component.0",
    ]);
    const relation = manifest.buildings.find(entry =>
      entry.object_id === "building.relation.3898738.component.0");
    expect(relation?.pack_target_ids).toHaveLength(2);
  });

  it("binds the scene config manifest ref to the staged manifest bytes", () => {
    if (sceneConfig.building_render === undefined) throw new Error("render scene config expected");
    expect(sceneConfig.assets).toBeUndefined();
    expect(sceneConfig.building_render.base_url).toBe("/building-renders/shanghai-huangpu-east-v1/");
    expect(sceneConfig.building_render.manifest.sha256).toBe(manifestSha256);
    expect(sceneConfig.building_render.manifest.size_bytes).toBe(manifestBytes.byteLength);
  });

  it("places every building at its ENU anchor with an identity rotation envelope", () => {
    for (const entry of manifest.buildings) {
      const world = buildingWorldPosition(entry);
      expect(world.x).toBeCloseTo(entry.anchor_east_m, 9);
      expect(world.y).toBeCloseTo(entry.base_enu_up_m, 9);
      expect(world.z).toBeCloseTo(-entry.anchor_north_m, 9);
      const placement = buildingPlacement(entry);
      expect(placement.building_id).toBe(entry.object_id);
      expect(placement.rotation_deg).toBe(0);
      expect(placement.base_y).toBeCloseTo(entry.envelope.base_up, 9);
      expect(placement.width).toBeCloseTo(entry.envelope.max_e - entry.envelope.min_e, 9);
      expect(placement.depth).toBeCloseTo(entry.envelope.max_n - entry.envelope.min_n, 9);
      expect(placement.height).toBeCloseTo(entry.height_m, 9);
      // Envelope box centre in world space == box built from the placement source.
      expect(placement.x).toBeCloseTo((entry.envelope.min_e + entry.envelope.max_e) / 2, 9);
      expect(placement.z).toBeCloseTo(-(entry.envelope.min_n + entry.envelope.max_n) / 2, 9);
      expect(entry.envelope.top_up - entry.envelope.base_up).toBeCloseTo(entry.height_m, 6);
      expect(entry.anchor_east_m).toBeGreaterThanOrEqual(entry.envelope.min_e);
      expect(entry.anchor_east_m).toBeLessThanOrEqual(entry.envelope.max_e);
      expect(entry.anchor_north_m).toBeGreaterThanOrEqual(entry.envelope.min_n);
      expect(entry.anchor_north_m).toBeLessThanOrEqual(entry.envelope.max_n);
    }
  });
});

describe("real staged GLB container parsing", () => {
  interface GlbParts {
    readonly document: Record<string, unknown>;
    readonly bin: Buffer;
  }

  const parseGlb = (buffer: Buffer): GlbParts => {
    expect(buffer.readUInt32LE(0)).toBe(0x46546c67);
    expect(buffer.readUInt32LE(4)).toBe(2);
    expect(buffer.readUInt32LE(8)).toBe(buffer.byteLength);
    const jsonLength = buffer.readUInt32LE(12);
    expect(buffer.readUInt32LE(16)).toBe(0x4e4f534a);
    const document = JSON.parse(buffer.subarray(20, 20 + jsonLength).toString("utf8")) as Record<string, unknown>;
    const binTypeStart = 20 + jsonLength;
    const binLength = buffer.readUInt32LE(binTypeStart);
    expect(buffer.readUInt32LE(binTypeStart + 4)).toBe(0x004e4942);
    return { document, bin: buffer.subarray(binTypeStart + 8, binTypeStart + 8 + binLength) };
  };

  const sha256Of = (buffer: Buffer): string => createHash("sha256").update(buffer).digest("hex");

  it("validates every recorded GLB container, source identity, geometry range and shared texture declaration", () => {
    for (const entry of manifest.buildings) {
      const raw = readFileSync(new URL(`../public/building-renders/shanghai-huangpu-east-v1/${entry.derived_glb.path}`, import.meta.url));
      expect(raw.byteLength).toBe(entry.derived_glb.bytes);
      expect(sha256Of(raw)).toBe(entry.derived_glb.sha256);
      const bytes = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer;
      expect(validateBuildingRenderGlb(bytes, entry, manifest).asset).toBeDefined();
    }
  });

  it("rejects mismapped GLBs, malformed containers, out-of-range geometry and undeclared resource URIs", () => {
    const entry = manifest.buildings[0]!;
    const original = readFileSync(new URL(`../public/building-renders/shanghai-huangpu-east-v1/${entry.derived_glb.path}`, import.meta.url));
    const mutate = (change: (document: Record<string, unknown>) => void): ArrayBuffer => {
      const parts = parseGlb(original);
      change(parts.document);
      const json = Buffer.from(JSON.stringify(parts.document));
      const padded = Buffer.concat([json, Buffer.alloc((-json.byteLength) & 3, 0x20)]);
      const output = Buffer.alloc(28 + padded.byteLength + parts.bin.byteLength);
      output.writeUInt32LE(0x46546c67, 0); output.writeUInt32LE(2, 4); output.writeUInt32LE(output.byteLength, 8);
      output.writeUInt32LE(padded.byteLength, 12); output.writeUInt32LE(0x4e4f534a, 16); padded.copy(output, 20);
      output.writeUInt32LE(parts.bin.byteLength, 20 + padded.byteLength); output.writeUInt32LE(0x004e4942, 24 + padded.byteLength);
      parts.bin.copy(output, 28 + padded.byteLength);
      return output.buffer.slice(output.byteOffset, output.byteOffset + output.byteLength) as ArrayBuffer;
    };
    const probes: ((document: Record<string, unknown>) => void)[] = [
      document => { (document.asset as Record<string, unknown>).version = "1.0"; },
      document => { ((document.asset as Record<string, unknown>).extras as Record<string, unknown>).object_id = manifest.buildings[1]!.object_id; },
      document => { (((document.asset as Record<string, unknown>).extras as Record<string, unknown>).scene as Record<string, unknown>).objects_json_sha256 = "1".repeat(64); },
      document => { (((document.asset as Record<string, unknown>).extras as Record<string, unknown>).frame as Record<string, unknown>).anchor_east_m = 0; },
      document => { (document.buffers as Record<string, unknown>[])[0]!.uri = "https://unverified.test/geometry.bin"; },
      document => { (document.bufferViews as Record<string, unknown>[])[0]!.byteLength = original.byteLength * 2; },
      document => { (document.accessors as Record<string, unknown>[])[0]!.count = original.byteLength; },
      document => { (document.images as Record<string, unknown>[])[0]!.uri = "https://unverified.test/image.png"; },
      document => { (document.images as Record<string, unknown>[])[0]!.uri = `assets/${"2".repeat(64)}`; },
      document => { (document.textures as Record<string, unknown>[])[0]!.source = 0.5; },
      document => { (document.nodes as Record<string, unknown>[])[0]!.translation = [0, 1, 0]; },
      document => { (document.scenes as Record<string, unknown>[])[0]!.nodes = [0, 0]; },
      document => { (document.accessors as Record<string, unknown>[])[1]!.count = 1; },
    ];
    for (const probe of probes) expect(() => validateBuildingRenderGlb(mutate(probe), entry, manifest)).toThrow();
    const wrongLength = Buffer.from(original); wrongLength.writeUInt32LE(wrongLength.byteLength + 4, 8);
    expect(() => readGlbDocument(wrongLength.buffer.slice(wrongLength.byteOffset, wrongLength.byteOffset + wrongLength.byteLength) as ArrayBuffer))
      .toThrow(/declared length mismatch/);
  });

  it("derives each public GLB inside the canonical envelope with labelled art detail", () => {
    // Regression: readGlbDocument once read json chunk fields through a 12-byte
    // DataView, which threw "Offset is outside the bounds of the DataView" in
    // the browser for every building (fake parse in other tests bypassed it).
    const rawRoot = rawManifest as Record<string, unknown>;
    const rawBuildings = rawRoot.buildings as Record<string, unknown>[];
    let derivedVertices = 0;
    let derivedTriangles = 0;
    let sourceVertices = 0;
    let sourceTriangles = 0;
    for (const entry of manifest.buildings) {
      const canonical = readFileSync(new URL(
        `../../validation/building-render-scaleout-mimo-20260929/assets/${entry.object_id}.glb`, import.meta.url));
      expect(canonical.byteLength).toBe(entry.glb.bytes);
      expect(sha256Of(canonical)).toBe(entry.glb.sha256);

      const derived = readFileSync(new URL(
        `../public/building-renders/shanghai-huangpu-east-v1/${entry.derived_glb.path}`, import.meta.url));
      expect(derived.byteLength).toBe(entry.derived_glb.bytes);
      expect(sha256Of(derived)).toBe(entry.derived_glb.sha256);

      const source = parseGlb(canonical);
      const output = parseGlb(derived);
      // Materials, meshes, nodes are untouched except for the authored facade
      // pane profile and material role; provenance assets gain only an
      // explicit art-detail label.
      const outputMaterials = (output.document.materials as Record<string, unknown>[] | undefined)?.map(material => {
        const { extras, ...rest } = material;
        const remaining = { ...(extras as Record<string, unknown> | undefined) };
        delete remaining[CITY_FACADE_PANE_PROFILE_KEY];
        delete remaining[CITY_BUILDING_MATERIAL_ROLE_KEY];
        return Object.keys(remaining).length > 0 ? { ...rest, extras: remaining } : rest;
      });
      expect(outputMaterials).toEqual(source.document.materials);
      expect(output.document.meshes).toEqual(source.document.meshes);
      expect(output.document.nodes).toEqual(source.document.nodes);
      const sourceAsset = source.document.asset as Record<string, unknown>;
      const outputAsset = output.document.asset as Record<string, unknown>;
      const outputExtras = { ...(outputAsset.extras as Record<string, unknown>) };
      const art = outputExtras.art_detail as Record<string, unknown> | undefined;
      expect(art).toBeDefined();
      delete outputExtras.art_detail;
      expect({ ...outputAsset, extras: outputExtras }).toEqual(sourceAsset);
      expect(art!.schema).toBe("aero-bench.building-render-art-detail/v1");
      expect(art!.class).toBe("derived_render_art_only_not_survey");
      expect(art!.layers).toEqual({ roof_detail: true, facade_relief: false });
      expect(String(art!.disclaimer_zh)).toMatch(/非测绘事实/);
      expect(art!.max_up_m as number).toBeLessThanOrEqual(entry.envelope.top_up);
      const detail = art!.detail as Record<string, unknown>;
      expect(detail.facade_relief).toBe(false);
      expect(typeof detail.parapet).toBe("boolean");
      expect(detail.roof_structures as number).toBeLessThanOrEqual(2);

      const sourceAccessors = source.document.accessors as { count: number; bufferView: number }[];
      const outputAccessors = output.document.accessors as { count: number; bufferView: number }[];
      const payload = (parts: GlbParts, accessor: { bufferView: number }): Buffer => {
        const views = parts.document.bufferViews as { byteOffset?: number; byteLength: number }[];
        const view = views[accessor.bufferView]!;
        return parts.bin.subarray(view.byteOffset ?? 0, (view.byteOffset ?? 0) + view.byteLength);
      };
      // Roof-only means walls, outward normals and triangle indices remain
      // byte-identical to the canonical source. Wall texture coordinates are
      // re-authored at metre scale (facade UV contract) with one UV per vertex.
      for (const index of [0, 1, 3]) {
        expect(payload(output, outputAccessors[index]!)).toEqual(payload(source, sourceAccessors[index]!));
      }
      expect(outputAccessors[2]!.count).toBe(sourceAccessors[2]!.count);
      expect(payload(output, outputAccessors[2]!).byteLength).toBe(payload(source, sourceAccessors[2]!).byteLength);
      const roofRingVertices = sourceAccessors[4]!.count;
      const roofExtraVertices = outputAccessors[4]!.count - roofRingVertices;
      const roofExtraTriangles = (outputAccessors[6]!.count - sourceAccessors[6]!.count) / 3;
      // Each roof ring edge adds two quads; each of the at most two roof
      // structures adds five. This budget scales with footprint complexity.
      expect(roofExtraVertices).toBeGreaterThanOrEqual(0);
      expect(roofExtraVertices).toBeLessThanOrEqual(8 * roofRingVertices + 40);
      expect(roofExtraTriangles).toBeGreaterThanOrEqual(0);
      expect(roofExtraTriangles).toBeLessThanOrEqual(4 * roofRingVertices + 20);
      derivedVertices += outputAccessors[0]!.count + outputAccessors[4]!.count;
      derivedTriangles += (outputAccessors[3]!.count + outputAccessors[6]!.count) / 3;
      sourceVertices += sourceAccessors[0]!.count + sourceAccessors[4]!.count;
      sourceTriangles += (sourceAccessors[3]!.count + sourceAccessors[6]!.count) / 3;

      // Art-detail geometry stays inside the canonical envelope (footprint and
      // source top height): POSITION min/max may not leave the glTF-frame box
      // derived from the manifest envelope, and must still touch every face.
      const accessors = output.document.accessors as { min?: number[]; max?: number[] }[];
      const bounds = { lo: [Infinity, Infinity, Infinity], hi: [-Infinity, -Infinity, -Infinity] };
      for (const accessor of accessors) {
        if (accessor.min === undefined || accessor.max === undefined) continue;
        for (let axis = 0; axis < 3; axis++) {
          bounds.lo[axis] = Math.min(bounds.lo[axis]!, accessor.min[axis]!);
          bounds.hi[axis] = Math.max(bounds.hi[axis]!, accessor.max[axis]!);
        }
      }
      const envelopeLo = [
        entry.envelope.min_e - entry.anchor_east_m,
        0,
        entry.anchor_north_m - entry.envelope.max_n,
      ];
      const envelopeHi = [
        entry.envelope.max_e - entry.anchor_east_m,
        entry.height_m,
        entry.anchor_north_m - entry.envelope.min_n,
      ];
      for (let axis = 0; axis < 3; axis++) {
        expect(bounds.lo[axis]!).toBeGreaterThanOrEqual(envelopeLo[axis]! - 1e-3);
        expect(bounds.hi[axis]!).toBeLessThanOrEqual(envelopeHi[axis]! + 1e-3);
        expect(bounds.lo[axis]!).toBeCloseTo(envelopeLo[axis]!, 3);
        expect(bounds.hi[axis]!).toBeCloseTo(envelopeHi[axis]!, 3);
      }

      // Per-building manifest entry carries the same non-survey art label.
      const rawEntry = rawBuildings.find(item => item.object_id === entry.object_id)!;
      const entryArt = rawEntry.art_detail as Record<string, unknown>;
      expect(entryArt.relief).toBe(detail.facade_relief);
      expect(entryArt.parapet).toBe(detail.parapet);
      expect(entryArt.roof_structures).toBe(detail.roof_structures);

      // Images moved to the shared texture store and resolve to their digests.
      expect((output.document.images as { bufferView?: number }[]).every(image => image.bufferView === undefined)).toBe(true);
      for (const image of output.document.images as { uri?: string }[]) {
        expect(image.uri).toMatch(/^assets\/[0-9a-f]{64}$/);
        const texture = readFileSync(new URL(
          `../public/building-renders/shanghai-huangpu-east-v1/${image.uri}`, import.meta.url));
        expect(sha256Of(texture)).toBe(image.uri!.slice("assets/".length));
      }
      // The runtime reader and texture plan accept the derived container.
      const bytes = derived.buffer.slice(
        derived.byteOffset, derived.byteOffset + derived.byteLength) as ArrayBuffer;
      const document = validateBuildingRenderGlb(bytes, entry, manifest);
      expect(Array.isArray(document.meshes) && document.meshes.length).toBeGreaterThan(0);
      const plan = texturePlan(document);
      expect(plan.size).toBe((document.textures as unknown[]).length);
      expect(plan.size).toBeGreaterThan(0);
      const textureSet = new Set(manifest.textures.map(texture => texture.sha256));
      for (const slot of plan.values()) {
        expect(slot.sha256).toMatch(/^[0-9a-f]{64}$/);
        expect(textureSet.has(slot.sha256)).toBe(true);
      }
    }
    const counts = (rawManifest as Record<string, unknown>).counts as Record<string, number>;
    expect(counts.derived_vertices).toBe(derivedVertices);
    expect(counts.derived_triangles).toBe(derivedTriangles);
    expect(counts.source_vertices).toBe(sourceVertices);
    expect(counts.source_triangles).toBe(sourceTriangles);
    expect(() => readGlbDocument(new ArrayBuffer(8))).toThrow(/not a glTF 2.0 GLB/);
  });

  it("keeps the default roof-only public payload under 8MB", () => {
    const root = fileURLToPath(new URL(
      "../public/building-renders/shanghai-huangpu-east-v1/", import.meta.url));
    let total = 0;
    const walk = (path: string): void => {
      for (const name of readdirSync(path)) {
        const child = `${path}/${name}`;
        if (statSync(child).isDirectory()) walk(child);
        else total += statSync(child).size;
      }
    };
    walk(root);
    // Archived originals are exactly the images embedded in the canonical source GLBs.
    const archived = new Map<string, number>();
    for (const entry of manifest.buildings) {
      const { document, bin } = parseGlb(readFileSync(new URL(
        `../../validation/building-render-scaleout-mimo-20260929/assets/${entry.object_id}.glb`, import.meta.url)));
      const views = document.bufferViews as { byteOffset?: number; byteLength: number }[];
      for (const image of (document.images as { bufferView: number }[] | undefined) ?? []) {
        const view = views[image.bufferView]!;
        const offset = view.byteOffset ?? 0;
        const bytes = bin.subarray(offset, offset + view.byteLength);
        archived.set(sha256Of(bytes), bytes.byteLength);
      }
    }
    expect(archived.size).toBe(45);
    for (const digest of archived.keys()) {
      expect(statSync(`${root}/assets/${digest}`).size).toBe(archived.get(digest));
    }
    const archivedBytes = [...archived.values()].reduce((sum, bytes) => sum + bytes, 0);
    const runtimePayload = manifest.counts.derived_total_bytes + manifest.counts.texture_bytes
      + manifestBytes.byteLength * 2;
    expect(total).toBe(runtimePayload + archivedBytes
      + sceneConfig.building_render!.source_context.size_bytes);
    expect(runtimePayload).toBeLessThan(8_000_000);
  });
});

describe("building render manifest fail-closed parsing", () => {
  const mutate = (fn: (value: Record<string, unknown>) => void): unknown => {
    const clone = JSON.parse(JSON.stringify(rawManifest)) as Record<string, unknown>;
    fn(clone);
    return clone;
  };

  it("rejects unsupported schema, malformed provenance and inconsistent block units", () => {
    expect(() => parseBuildingRenderManifest(mutate(value => {
      value.schema_version = "aero-bench.building-render-runtime/v3";
    }))).toThrow(/schema_version/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      (value.scene as Record<string, unknown>).objects_json_sha256 = "0".repeat(64);
    }))).toThrow(/nonzero lowercase sha256/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      (value.scene as Record<string, unknown>).placement_rule = "guessed";
    }))).toThrow(/placement rule/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      (value.sources as Record<string, unknown>).scaleout_glb_combined_sha256 = "invalid";
    }))).toThrow(/lowercase sha256/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      value.block_size_m = 100;
    }))).toThrow(/block_size_m/);
  });

  it("rejects unknown fields and missing current-schema metadata", () => {
    const probes: ((value: Record<string, unknown>) => void)[] = [
      value => { value.compatibility = true; },
      value => { delete (value.scene as Record<string, unknown>).origin_wgs84; },
      value => { (value.scene as Record<string, unknown>).origin = {}; },
      value => { delete (value.buildings as Record<string, unknown>[])[0]!.osm; },
      value => { ((value.buildings as Record<string, unknown>[])[0]!.glb as Record<string, unknown>).url = "guessed"; },
      value => { (value.counts as Record<string, unknown>).count = 414; },
      value => { (value.textures as Record<string, unknown>[])[0]!.url = "guessed"; },
      value => { (value.blocks as Record<string, unknown>[])[0]!.legacy = true; },
      value => { ((value.scene as Record<string, unknown>).origin_wgs84 as Record<string, unknown>).unit = "cm"; },
    ];
    for (const probe of probes) expect(() => parseBuildingRenderManifest(mutate(probe))).toThrow(/missing|unknown/);
  });

  it("keeps metre and identity checks independent of the dataset", () => {
    expect(() => parseBuildingRenderManifest(mutate(value => { value.block_size_m = 0; }))).toThrow(/positive metres/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      ((value.scene as Record<string, unknown>).origin_wgs84 as Record<string, unknown>).ellipsoid_height_m = 51;
    }))).toThrow(/vertical relation/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const entry = (value.buildings as Record<string, unknown>[])[0]!;
      (entry.osm as Record<string, unknown>).id = 42;
    }))).toThrow(/osm identity/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const ids = (value.blocks as Record<string, unknown>[])[0]!.object_ids as string[];
      ids.splice(1, 0, ids[0]!);
    }))).toThrow(/sorted and unique/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      (value.pack_missing_object_ids as string[]).push("building.way.1.component.0");
      (value.pack_missing_object_ids as string[]).sort();
      (value.counts as Record<string, unknown>).pack_missing_objects = 5;
    }))).toThrow(/unknown objects/);
  });

  it("accepts the declared roof art extension and rejects unlabelled or inconsistent art", () => {
    expect(manifest.art_detail?.layers).toEqual({ roof_detail: true, facade_relief: false });
    expect(manifest.buildings.every(entry => entry.art_detail?.relief === false)).toBe(true);
    expect(() => parseBuildingRenderManifest(mutate(value => { delete value.art_detail; }))).toThrow(/art_detail/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      (value.art_detail as Record<string, unknown>).class = "survey";
    }))).toThrow(/non-survey/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      ((value.buildings as Record<string, unknown>[])[0]!.art_detail as Record<string, unknown>).relief = true;
    }))).toThrow(/declared art layers/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      (value.art_detail as Record<string, unknown>).disclaimer_en = "measurement data";
    }))).toThrow(/non-survey disclaimers/);
  });

  it("rejects mapping and envelope drift", () => {
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      const glb = buildings[0]!.glb as Record<string, unknown>;
      glb.path = `glb/${glb.sha256}.glb`;
    }))).toThrow(/content-addressed/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      buildings[1]!.object_id = buildings[0]!.object_id;
      buildings[1]!.osm = buildings[0]!.osm;
    }))).toThrow(/not unique/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      buildings[0]!.height_m = (buildings[0]!.height_m as number) + 1;
    }))).toThrow(/does not span/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      buildings[0]!.block = 999;
    }))).toThrow(/unknown block/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const counts = value.counts as Record<string, unknown>;
      counts.total_bytes = 1;
    }))).toThrow(/total_bytes/);
  });

  it("rejects derived binding and texture store drift", () => {
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      const derived = buildings[0]!.derived_glb as Record<string, unknown>;
      derived.path = `glb/${derived.sha256}.glb`;
    }))).toThrow(/derived_glb.path is not content-addressed/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      const source = buildings[0]!.derived_glb as Record<string, unknown>;
      const derived = buildings[1]!.derived_glb as Record<string, unknown>;
      derived.sha256 = source.sha256;
      derived.path = source.path;
    }))).toThrow(/derived glb digests are not unique/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const counts = value.counts as Record<string, unknown>;
      counts.derived_total_bytes = 1;
    }))).toThrow(/derived_total_bytes/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const counts = value.counts as Record<string, unknown>;
      counts.textures = 44;
    }))).toThrow(/counts.textures/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const textures = value.textures as Record<string, unknown>[];
      textures.reverse();
    }))).toThrow(/sorted by unique sha256/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      delete value.textures;
    }))).toThrow(/root.textures is missing/);
    expect(() => parseBuildingRenderManifest(mutate(value => {
      const buildings = value.buildings as Record<string, unknown>[];
      const derived = buildings[0]!.derived_glb as Record<string, unknown>;
      const textureSha = (value.textures as Record<string, unknown>[])[0]!.sha256 as string;
      derived.sha256 = textureSha;
      derived.path = `assets/${textureSha}`;
    }))).toThrow(/collides with the texture store/);
  });
});

describe("verified manifest fetch", () => {
  it("fetches the staged manifest through the digest-verified resolver", async () => {
    const resolver = stubResolver({ manifest: new Uint8Array(manifestBytes) });
    const ref = sceneConfig.building_render?.manifest;
    if (ref === undefined) throw new Error("render scene config expected");
    const loaded = await fetchBuildingRenderManifest(resolver, ref);
    expect(loaded.counts.buildings).toBe(414);
    resolver.dispose();
  });

  it("fails closed on a single flipped manifest byte", async () => {
    const tampered = Uint8Array.from(manifestBytes);
    tampered[100] = tampered[100]! ^ 0xff;
    const resolver = stubResolver({ manifest: tampered });
    const ref = sceneConfig.building_render?.manifest;
    if (ref === undefined) throw new Error("render scene config expected");
    await expect(fetchBuildingRenderManifest(resolver, ref))
      .rejects.toBeInstanceOf(AssetResolutionError);
    resolver.dispose();
  });
});

describe("BuildingRenderStreamer loading behaviour", () => {
  it("primes only the camera frustum and streams the rest in the background", async () => {
    const group = new THREE.Group();
    const streamer = new BuildingRenderStreamer({
      manifest, resolver: fakeStreamResolver(), group, parseBuilding: fakeParse, concurrency: 4,
    });
    const dense = manifest.blocks.reduce((best, candidate) =>
      candidate.object_ids.length > best.object_ids.length ? candidate : best, manifest.blocks[0]!);
    const centerX = (dense.min_e + dense.max_e) / 2;
    const centerZ = -(dense.min_n + dense.max_n) / 2;
    const camera = new THREE.PerspectiveCamera(60, 1, 1, 100000);
    camera.position.set(centerX, 120, centerZ + 40);
    camera.lookAt(centerX, 0, centerZ - 400);
    camera.updateMatrixWorld(true);
    await streamer.prime(camera);
    expect(streamer.progress.loaded).toBeGreaterThan(0);
    expect(streamer.progress.loaded).toBeLessThan(414);
    expect(group.children.length).toBe(streamer.progress.loaded);
    await streamer.loadAll();
    expect(streamer.progress.loaded).toBe(414);
    expect(streamer.progress.failed).toBe(0);
    expect(group.children.length).toBe(414);
    streamer.dispose();
  });

  it("loads every building with strict placement and picking bindings", async () => {
    const group = new THREE.Group();
    const streamer = new BuildingRenderStreamer({
      manifest, resolver: fakeStreamResolver(), group, parseBuilding: fakeParse, concurrency: 4,
    });
    await streamer.loadAll();
    expect(new Set(group.children.map(child => child.name)).size).toBe(414);
    for (const entry of manifest.buildings) {
      const visual = group.children.find(child => child.name === entry.object_id);
      expect(visual).toBeDefined();
      const world = buildingWorldPosition(entry);
      expect(visual!.position.distanceTo(world)).toBeLessThan(1e-9);
      expect(visual!.userData.target).toEqual({ kind: "building", id: entry.object_id });
      expect(visual!.userData.collisionBox).toEqual(buildingPlacement(entry));
      expect(visual!.userData.glbSha256).toBe(entry.glb.sha256);
      expect(visual!.userData.derivedGlbSha256).toBe(entry.derived_glb.sha256);
    }
    streamer.dispose();
  });

  it("loads strictly in deterministic priority order at concurrency 1", async () => {
    const group = new THREE.Group();
    const streamer = new BuildingRenderStreamer({
      manifest, resolver: fakeStreamResolver(), group, parseBuilding: fakeParse, concurrency: 1,
    });
    const camera = new THREE.PerspectiveCamera(60, 1, 1, 100000);
    const anchor = manifest.buildings[0]!;
    camera.position.set(anchor.anchor_east_m + 200, 300, -anchor.anchor_north_m + 200);
    camera.lookAt(anchor.anchor_east_m, 0, -anchor.anchor_north_m);
    camera.updateMatrixWorld(true);
    camera.updateProjectionMatrix();
    streamer.update(camera);
    await streamer.loadAll();

    camera.updateMatrixWorld(true);
    const matrix = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const frustum = new THREE.Frustum().setFromProjectionMatrix(matrix);
    const keyed = manifest.buildings.map(entry => {
      const block = manifest.blocks[entry.block]!;
      const center = new THREE.Vector3(
        (block.min_e + block.max_e) / 2, 50, -(block.min_n + block.max_n) / 2);
      const radius = Math.hypot((block.max_e - block.min_e) / 2, (block.max_n - block.min_n) / 2, 100);
      const visible = frustum.intersectsSphere(new THREE.Sphere(center, radius));
      return {
        id: entry.object_id,
        key: [visible ? 0 : 1, camera.position.distanceTo(center), entry.block, entry.object_id] as const,
      };
    }).sort((left, right) => {
      for (let axis = 0; axis < 4; axis++) {
        const l = left.key[axis] as number | string;
        const r = right.key[axis] as number | string;
        if (l < r) return -1;
        if (l > r) return 1;
      }
      return 0;
    });
    expect(group.children.map(child => child.name)).toEqual(keyed.map(item => item.id));
    streamer.dispose();
  });

  it("exposes per-building load errors without dropping the rest of the city", async () => {
    const group = new THREE.Group();
    const broken = manifest.buildings[3]!;
    const streamer = new BuildingRenderStreamer({
      manifest, resolver: fakeStreamResolver(broken.derived_glb.sha256), group,
      parseBuilding: fakeParse, concurrency: 4,
    });
    await streamer.loadAll();
    expect(streamer.progress.loaded).toBe(413);
    expect(streamer.progress.failed).toBe(1);
    expect(streamer.progress.errors).toEqual([
      { object_id: broken.object_id, message: "simulated fetch failure" },
    ]);
    expect(group.children.some(child => child.name === broken.object_id)).toBe(false);
    streamer.dispose();
  });

  it("leaves batch flushing to the per-render matrix hook on camera updates", () => {
    const streamer = new BuildingRenderStreamer({
      manifest, resolver: fakeStreamResolver(), group: new THREE.Group(), parseBuilding: fakeParse, concurrency: 1,
    });
    const flush = vi.spyOn(streamer.batches, "flush");
    streamer.update(new THREE.PerspectiveCamera());
    expect(flush).not.toHaveBeenCalled();
    streamer.batches.group.updateMatrixWorld(true);
    expect(flush).toHaveBeenCalledOnce();
    streamer.dispose();
  });

  it("stops cleanly when disposed before and during loading", async () => {
    const group = new THREE.Group();
    const streamer = new BuildingRenderStreamer({
      manifest, resolver: fakeStreamResolver(), group, parseBuilding: fakeParse, concurrency: 1,
    });
    streamer.dispose();
    await streamer.loadAll();
    await streamer.prime(new THREE.PerspectiveCamera());
    streamer.update(new THREE.PerspectiveCamera());
    expect(group.children).toHaveLength(0);
    expect(streamer.progress.loaded).toBe(0);
  });
});

describe("building render lighting policy", () => {
  it("retains day and twilight output and applies a distinct night policy", () => {
    const material = new THREE.MeshStandardMaterial();
    const entry = manifest.buildings[0]!;
    const visual = assembleBuildingVisual(entry, new THREE.Object3D(), [material]);
    const group = new THREE.Group();
    group.add(visual);
    const environment = new THREE.Texture();
    setBuildingRenderLighting(group, "day", environment);
    expect(material.emissiveIntensity).toBe(0);
    expect(material.envMapIntensity).toBeCloseTo(0.6, 6);
    expect(material.envMap).toBe(environment);
    setBuildingRenderLighting(group, "twilight", environment);
    expect(material.emissiveIntensity).toBeCloseTo(1.2, 6);
    expect(material.envMapIntensity).toBeCloseTo(0.4, 6);
    setBuildingRenderLighting(group, "night", environment);
    expect(material.emissiveIntensity).toBeCloseTo(1.8, 6);
    expect(material.envMapIntensity).toBeCloseTo(0.28, 6);
    // Direct call on a lazily placed visual (post mood-switch path).
    const lateMaterial = new THREE.MeshStandardMaterial();
    const late = assembleBuildingVisual(entry, new THREE.Object3D(), [lateMaterial]);
    setBuildingRenderLighting(late, "night", environment);
    expect(lateMaterial.emissiveIntensity).toBeCloseTo(1.8, 6);
    expect(lateMaterial.envMap).toBe(environment);
    material.dispose();
    lateMaterial.dispose();
    environment.dispose();
  });
});
