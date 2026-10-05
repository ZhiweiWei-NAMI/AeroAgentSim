// @vitest-environment node
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseMeshPack, unpackMeshBatch } from "./pack";
import { loadMeshPack } from "./pack-loader";
import { AssetResolver } from "../asset-resolver";
import { parseCitySceneConfig } from "../city-scene-config";

const scene = parseCitySceneConfig(JSON.parse(readFileSync(new URL(
  "../../public/city-presentation/default-scene-v1.json", import.meta.url), "utf8")));
const base = new URL(`../../public${scene.mesh_pack.base_url}`, import.meta.url);
const manifestBytes = readFileSync(new URL("manifest.json", base));
const manifest = parseMeshPack(JSON.parse(manifestBytes.toString("utf8")));

describe("official OSM2World published mesh pack", () => {
  it("pins the manifest and retains the exact official mesh inventory", () => {
    expect(scene.mesh_pack.base_url).toBe("/osm2world/packs/shanghai-huangpu-east-v1/");
    expect(createHash("sha256").update(manifestBytes).digest("hex")).toBe(scene.mesh_pack.manifest.sha256);
    expect(manifestBytes.length).toBe(scene.mesh_pack.manifest.size_bytes);
    expect(manifest.original_mesh_count).toBe(2052);
    expect(manifest.batches).toHaveLength(17);
    expect(manifest.projection.axes).toBe("east-up-south");
  });

  it("verifies and decodes every generated geometry buffer", () => {
    for (const batch of manifest.batches) {
      const bytes = readFileSync(new URL(`assets/${batch.file.sha256}`, base));
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(batch.file.sha256);
      const arrays = unpackMeshBatch(batch, Uint8Array.from(bytes).buffer);
      expect(arrays.positions.length).toBe(batch.vertices * 3);
      expect(arrays.indices.length).toBe(batch.indices);
      expect(batch.ranges.at(-1)?.end).toBe(batch.indices / 3);
    }
  });

  it("downloads only selected display textures while verifying every geometry buffer", async () => {
    const requested: string[] = [];
    const progress: [number, number][] = [];
    const resolver = new AssetResolver({
      baseHref: "http://localhost/",
      fetch: async input => {
        const digest = new URL(String(input)).pathname.split("/").at(-1)!;
        requested.push(digest);
        return new Response(new Uint8Array(readFileSync(new URL(`assets/${digest}`, base))));
      },
      createObjectUrl: () => { throw new Error("Geometry bytes must not be published as a blob URL"); },
    });
    try {
      const loaded = await loadMeshPack(resolver, scene.mesh_pack.manifest,
        undefined, undefined, (completed, total) => {
          progress.push([completed, total]);
        }, () => new Set());
      expect(loaded.batches).toHaveLength(manifest.batches.length);
      expect(loaded.textureUrls.size).toBe(0);
      expect(requested).toHaveLength(manifest.batches.length + 1);
      expect(progress.at(-1)).toEqual([manifest.batches.length, manifest.batches.length]);
      loaded.dispose();
    } finally {
      resolver.dispose();
    }
  });

  it("keeps full texture verification for declared replay packs", async () => {
    const requested: string[] = [];
    const resolver = new AssetResolver({ baseHref: "http://localhost/", fetch: async input => {
      const digest = new URL(String(input)).pathname.split("/").at(-1)!;
      requested.push(digest);
      return new Response(new Uint8Array(readFileSync(new URL(`assets/${digest}`, base))));
    } });
    try {
      const loaded = await loadMeshPack(resolver, scene.mesh_pack.manifest);
      expect(loaded.textureUrls.size).toBe(Object.keys(manifest.textures).length);
      expect(requested).toHaveLength(1 + manifest.batches.length + Object.keys(manifest.textures).length);
      loaded.dispose();
    } finally {
      resolver.dispose();
    }
  });

  it("rejects unknown fields, missing ranges and undeclared textures", () => {
    expect(() => parseMeshPack({ ...manifest, fallback: true })).toThrow(/fields/);
    const ranges = structuredClone(manifest);
    (ranges.batches[0]!.ranges as unknown[]).pop();
    expect(() => parseMeshPack(ranges)).toThrow(/range/);
    const textures = { ...manifest, textures: {} };
    expect(() => parseMeshPack(textures)).toThrow(/undeclared texture/);
  });

  it("requires the producer-bound source coordinate declaration", () => {
    const missing = structuredClone(manifest) as unknown as Record<string, unknown>;
    delete missing.coordinate_contract;
    expect(() => parseMeshPack(missing)).toThrow(/fields/);
    for (const change of [{ source_json_sha256: "a".repeat(64) }, { native_point_quantization_m: 1 },
      { converter_origin: { latitude_deg: 90, longitude_deg: 121 } },
      { stored_translation_xz_m: [0, Number.NaN] }]) {
      expect(() => parseMeshPack({ ...manifest,
        coordinate_contract: { ...manifest.coordinate_contract, ...change } })).toThrow();
    }
    expect(manifest.coordinate_contract.source_json_sha256).toBe(manifest.source.sha256);
    expect(manifest.coordinate_contract.native_point_quantization_m).toBe(0.001);
  });

  it("rejects invalid index and nonfinite buffer contents", () => {
    const batch = manifest.batches[0]!;
    const bytes = Uint8Array.from(readFileSync(new URL(`assets/${batch.file.sha256}`, base))).buffer;
    new Float32Array(bytes)[0] = Number.NaN;
    expect(() => unpackMeshBatch(batch, bytes)).toThrow(/nonfinite/);
    new Float32Array(bytes)[0] = 0;
    new Uint32Array(bytes, batch.vertices * 32)[0] = batch.vertices;
    expect(() => unpackMeshBatch(batch, bytes)).toThrow(/missing vertex/);
  });

  it("does not import the raw OSM dataset into production source", () => {
    const source = readFileSync(fileURLToPath(new URL("./source.ts", import.meta.url)), "utf8");
    expect(source).not.toMatch(/import .*\.osm\.json/);
  });
});
