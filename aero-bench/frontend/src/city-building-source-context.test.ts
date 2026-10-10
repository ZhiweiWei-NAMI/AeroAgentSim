// @vitest-environment node
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { AssetResolver, AssetResolutionError } from "./asset-resolver";
import { parseBuildingRenderManifest, type BuildingRenderManifest } from "./city-building-renders";
import {
  fetchBuildingRenderSourceContext,
  parseBuildingRenderSourceContext,
  validateBuildingRenderContext,
  type BuildingRenderContext,
  type BuildingRenderSourceContext,
} from "./city-building-source-context";
import { parseMeshPack } from "./osm2world/pack";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const rawRender = JSON.parse(readFileSync(new URL(
  "../public/building-renders/shanghai-huangpu-east-v1/manifest.json", import.meta.url), "utf8"));
const render = parseBuildingRenderManifest(rawRender);
const packBytes = readFileSync(new URL("../public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json", import.meta.url));
const pack = { manifest: parseMeshPack(JSON.parse(packBytes.toString("utf8"))),
  manifestSha256: createHash("sha256").update(packBytes).digest("hex") };
const temp = mkdtempSync(join(tmpdir(), "building-source-context-test-"));
let sourceBytes: Buffer;
try {
  const output = join(temp, "source-context.json");
  execFileSync("python", [join(ROOT, "frontend/scripts/build-building-render-source-context.py"),
    "--scene-id", "shanghai-huangpu-east-v1",
    "--objects", join(ROOT, "validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json"),
    "--scaleout-manifest", join(ROOT, "validation/building-render-scaleout-mimo-20260929/manifest.json"),
    "--catalog-fragment", join(ROOT, "validation/building-render-scaleout-mimo-20260929/building-render-catalog-fragment.json"),
    "--mesh-pack", join(ROOT, "frontend/public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json"),
    "--mesh-source", join(ROOT, "validation/scene-compiler-shanghai-huangpu-east-v1/osm/effective.osm.json"),
    "--canonical-assets", join(ROOT, "validation/building-render-scaleout-mimo-20260929/assets"),
    "--output", output]);
  sourceBytes = readFileSync(output);
} finally {
  rmSync(temp, { recursive: true });
}
const rawSource: unknown = JSON.parse(sourceBytes.toString("utf8"));
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

async function verifiedSource(value: unknown = rawSource) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const resolver = new AssetResolver({ baseHref: "https://viewer.test/source/",
    digest: bytes => Promise.resolve(createHash("sha256").update(new Uint8Array(bytes)).digest("hex")),
    fetch: (async () => new Response(bytes, { status: 200 })) as typeof fetch });
  try { return await fetchBuildingRenderSourceContext(resolver, { sha256, size_bytes: bytes.byteLength }); }
  finally { resolver.dispose(); }
}

async function context(): Promise<BuildingRenderContext> {
  return { expectedSceneId: "shanghai-huangpu-east-v1", pack, source: await verifiedSource() };
}

describe("building source context joins", () => {
  it("joins the recorded objects, all canonical GLBs and the actually loaded pack", async () => {
    const source = parseBuildingRenderSourceContext(rawSource);
    expect(source.buildings).toHaveLength(414);
    expect(source.scene.objects_json_sha256).toBe(render.scene.objects_json_sha256);
    expect(source.sources).toEqual(render.sources);
    validateBuildingRenderContext(render, await context());
  });

  it("rejects a missing selected identity, missing pack or an unverified source object", async () => {
    const actual = await context();
    expect(() => validateBuildingRenderContext(render, undefined as unknown as BuildingRenderContext)).toThrow(/expected identity is missing/);
    expect(() => validateBuildingRenderContext(render, { ...actual, expectedSceneId: "" })).toThrow(/expected identity is missing/);
    expect(() => validateBuildingRenderContext(render, { ...actual, expectedSceneId: "another-scene" })).toThrow(/scene id/);
    expect(() => validateBuildingRenderContext(render, { ...actual, pack: undefined as unknown as typeof pack })).toThrow(/not ready/);
    expect(() => validateBuildingRenderContext(render, { ...actual,
      source: { ...actual.source } })).toThrow(/has not been digest verified/);
    expect(Object.isFrozen(actual.source.context.buildings[0]!.glb)).toBe(true);
  });

  it("rejects each source digest and all five origin components when they drift", async () => {
    const actual = await context();
    for (const key of ["objects_json_sha256", "mesh_pack_manifest_sha256", "mesh_pack_source_sha256"] as const) {
      const changed = clone(render);
      (changed.scene as unknown as Record<string, unknown>)[key] = "1".repeat(64);
      expect(() => validateBuildingRenderContext(changed, actual)).toThrow(new RegExp(key));
    }
    for (const key of Object.keys(render.sources)) {
      const changed = clone(render);
      (changed.sources as unknown as Record<string, unknown>)[key] = "2".repeat(64);
      expect(() => validateBuildingRenderContext(changed, actual)).toThrow(new RegExp(key));
    }
    for (const key of ["latitude_deg", "longitude_deg", "amsl_m", "geoid_undulation_m"] as const) {
      const changed = clone(render);
      (changed.scene.origin_wgs84 as unknown as Record<string, number>)[key]! += 0.01;
      if (key === "amsl_m" || key === "geoid_undulation_m") {
        (changed.scene.origin_wgs84 as unknown as Record<string, number>).ellipsoid_height_m! += 0.01;
      }
      expect(() => validateBuildingRenderContext(changed, actual)).toThrow(/origin/);
    }
    const changed = clone(render);
    (changed.scene.origin_wgs84 as unknown as Record<string, number>).ellipsoid_height_m! += 0.01;
    expect(() => validateBuildingRenderContext(changed, actual)).toThrow(/vertical relation/);
  });

  it("rejects actual pack manifest/source/origin drift and wrong pack object linkage", async () => {
    const actual = await context();
    expect(() => validateBuildingRenderContext(render, { ...actual, pack: { ...pack, manifestSha256: "3".repeat(64) } })).toThrow(/mesh pack manifest or source/);
    const sourceDrift = clone(pack);
    (sourceDrift.manifest.source as unknown as Record<string, unknown>).sha256 = "4".repeat(64);
    expect(() => validateBuildingRenderContext(render, { ...actual, pack: sourceDrift })).toThrow(/mesh pack manifest or source/);
    const originDrift = clone(pack);
    (originDrift.manifest.projection.origin as unknown as Record<string, number>).latitude_deg! += 0.01;
    expect(() => validateBuildingRenderContext(render, { ...actual, pack: originDrift })).toThrow(/mesh pack origin/);
    const mappingDrift = clone(pack);
    const item = mappingDrift.manifest.objects.find(item => item.tags["aero_bench:source_id"] === "3898738"
      && item.id.startsWith("w"))!;
    (item.tags as Record<string, string>)["aero_bench:source_id"] = "42";
    expect(() => validateBuildingRenderContext(render, { ...actual, pack: mappingDrift })).toThrow(/pack object mapping/);
  });

  it("rejects internally consistent replacement of canonical hashes, sizes, styles and envelopes", async () => {
    const actual = await context();
    const digestDrift = clone(render);
    const glb = digestDrift.buildings[0]!.glb as unknown as Record<string, unknown>;
    glb.sha256 = "5".repeat(64); glb.path = `assets/${glb.sha256}`;
    expect(() => validateBuildingRenderContext(digestDrift, actual)).toThrow(/glb.sha256/);
    const bytesDrift = clone(render);
    (bytesDrift.buildings[0]!.glb as unknown as Record<string, number>).bytes! += 4;
    (bytesDrift.counts as unknown as Record<string, number>).total_bytes! += 4;
    expect(() => validateBuildingRenderContext(bytesDrift, actual)).toThrow(/glb.bytes/);
    const styleDrift = clone(render);
    (styleDrift.buildings[0]! as unknown as Record<string, unknown>).style_id = styleDrift.buildings[1]!.style_id;
    expect(() => validateBuildingRenderContext(styleDrift, actual)).toThrow(/style_id/);
    const heightDrift = clone(render);
    (heightDrift.buildings[0]! as unknown as Record<string, number>).height_m! += 1;
    (heightDrift.buildings[0]!.envelope as unknown as Record<string, number>).top_up! += 1;
    expect(() => validateBuildingRenderContext(heightDrift, actual)).toThrow(/height_m/);
  });

  it("rejects mismatched source object inventory even if the render count is unchanged", async () => {
    const actual = await context();
    const idDrift = clone(render);
    const entry = idDrift.buildings[0]! as unknown as Record<string, unknown>;
    const oldId = entry.object_id;
    entry.object_id = "building.relation.42.component.0"; entry.osm = { type: "relation", id: 42 };
    const block = idDrift.blocks[idDrift.buildings[0]!.block]!;
    (block as unknown as Record<string, string[]>).object_ids = block.object_ids.map(id => id === oldId ? entry.object_id as string : id).sort();
    expect(() => validateBuildingRenderContext(idDrift, actual)).toThrow(/missing from the render manifest/);
    const shortenedSource = clone(rawSource) as BuildingRenderSourceContext;
    (shortenedSource as unknown as Record<string, unknown>).buildings = shortenedSource.buildings.slice(1);
    const shortened = await verifiedSource(shortenedSource);
    expect(() => validateBuildingRenderContext(render, { ...actual, source: shortened })).toThrow(/source building count/);
  });

  it("validates a different declared profile without relying on count, block size or Shanghai pins", async () => {
    // Contract fixture: retain the recorded GLB bytes, but declare a separately
    // hashed source profile and pack. This checks parser portability, not a
    // claim that another surveyed city or simulation run was produced.
    const variant = clone(rawRender) as Record<string, unknown>;
    const variantSource = clone(rawSource) as BuildingRenderSourceContext;
    const variantPack = clone(pack);
    const scene = variant.scene as Record<string, unknown>;
    const sourceScene = variantSource.scene as unknown as Record<string, unknown>;
    scene.id = sourceScene.id = "recorded-source-profile-unit-fixture";
    scene.mesh_pack_manifest_sha256 = sourceScene.mesh_pack_manifest_sha256 = "6".repeat(64);
    scene.mesh_pack_source_sha256 = sourceScene.mesh_pack_source_sha256 = "7".repeat(64);
    scene.objects_json_sha256 = sourceScene.objects_json_sha256 = "8".repeat(64);
    (variantPack as unknown as Record<string, unknown>).manifestSha256 = scene.mesh_pack_manifest_sha256;
    (variantPack.manifest.source as unknown as Record<string, unknown>).sha256 = scene.mesh_pack_source_sha256;
    const buildings = variant.buildings as BuildingRenderManifest["buildings"];
    const keep = buildings.find(entry => entry.pack_target_ids.length === 0)!;
    variant.buildings = [{ ...keep, block: 0 }];
    variant.block_size_m = 200;
    variant.blocks = [{ index: 0, min_e: keep.anchor_east_m - 100, max_e: keep.anchor_east_m + 100,
      min_n: keep.anchor_north_m - 100, max_n: keep.anchor_north_m + 100, object_ids: [keep.object_id] }];
    variant.pack_missing_object_ids = [keep.object_id];
    variant.counts = { buildings: 1, total_bytes: keep.glb.bytes, derived_total_bytes: keep.derived_glb.bytes,
      texture_bytes: render.counts.texture_bytes, textures: render.counts.textures, styles: 1,
      pack_linked_objects: 0, pack_missing_objects: 1, pack_targets: 0, blocks: 1,
      source_vertices: variantSource.buildings.find(entry => entry.object_id === keep.object_id)!.geometry.vertices,
      source_triangles: variantSource.buildings.find(entry => entry.object_id === keep.object_id)!.geometry.triangles,
      derived_vertices: keep.art_detail!.vertices, derived_triangles: keep.art_detail!.triangles,
      art_detail_buildings: 1, art_detail_relief_buildings: 0, art_detail_roofs: keep.art_detail!.parapet ? 1 : 0,
      art_detail_structures: keep.art_detail!.roof_structures > 0 ? 1 : 0 };
    (variantSource as unknown as Record<string, unknown>).buildings = variantSource.buildings.filter(entry => entry.object_id === keep.object_id);
    (variantPack.manifest as unknown as Record<string, unknown>).batches = variantPack.manifest.batches.map(batch => ({ ...batch,
      ranges: batch.ranges.map(range => range.target?.kind === "building" ? { ...range, target: null } : range) }));
    const parsed = parseBuildingRenderManifest(variant);
    const source = await verifiedSource(variantSource);
    expect(parsed.buildings).toHaveLength(1);
    expect(parsed.block_size_m).toBe(200);
    validateBuildingRenderContext(parsed, { expectedSceneId: scene.id as string, pack: variantPack, source });
  });
});

describe("source context validation and frozen byte fetch", () => {
  it("rejects missing/unknown fields, unsupported versions and duplicate canonical identities", () => {
    for (const mutate of [
      (value: Record<string, unknown>) => { value.schema_version = "unsupported"; },
      (value: Record<string, unknown>) => { value.fallback = true; },
      (value: Record<string, unknown>) => { delete value.sources; },
      (value: Record<string, unknown>) => { (value.buildings as unknown[]).push((value.buildings as unknown[])[0]); },
    ]) {
      const value = clone(rawSource) as Record<string, unknown>;
      mutate(value);
      expect(() => parseBuildingRenderSourceContext(value)).toThrow();
    }
  });

  it("rejects wrong declared digest/size and one changed source byte", async () => {
    const sha256 = createHash("sha256").update(sourceBytes).digest("hex");
    for (const mode of ["digest", "size", "byte"] as const) {
      const bytes = Uint8Array.from(sourceBytes);
      if (mode === "byte") bytes[100] = bytes[100]! ^ 1;
      const resolver = new AssetResolver({ baseHref: "https://viewer.test/source/",
        digest: bytes => Promise.resolve(createHash("sha256").update(new Uint8Array(bytes)).digest("hex")),
        fetch: (async () => new Response(bytes, { status: 200 })) as typeof fetch });
      await expect(fetchBuildingRenderSourceContext(resolver, { sha256: mode === "digest" ? "9".repeat(64) : sha256,
        size_bytes: sourceBytes.byteLength + (mode === "size" ? 1 : 0) })).rejects.toBeInstanceOf(AssetResolutionError);
      resolver.dispose();
    }
  });
});
