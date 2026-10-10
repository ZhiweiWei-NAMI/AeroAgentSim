import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { cityVegetationInput } from "./city-vegetation-binding";
import type { BuildingRenderManifest } from "./city-building-renders";
import type { CityVegetationRoadBinding } from "./city-vegetation-layer";

const read = (path: string): unknown => JSON.parse(readFileSync(resolve(process.cwd(), "public", path), "utf8"));
const renderScene = (read("building-renders/shanghai-huangpu-east-v1/manifest.json") as BuildingRenderManifest).scene;
const pack = read("osm2world/packs/shanghai-huangpu-east-v1/manifest.json") as {
  extent: { west: number; east: number; south: number; north: number } };
const scene = read("city-presentation/default-scene-v1.json") as {
  mesh_pack: { manifest: { sha256: string } };
  environment_source: { url: string; sha256: string; size_bytes: number } };

const roadBinding: CityVegetationRoadBinding = {
  roadbed: [], walkbed: [], crossings: [], junctions: [], lamps: [], signals: [] };

describe("city vegetation binding", () => {
  it("binds the default scene source to the render authority and the pack extent", () => {
    const input = cityVegetationInput(scene.environment_source, renderScene, scene.mesh_pack.manifest.sha256, pack.extent, roadBinding);
    expect(input.roadBinding).toBe(roadBinding);
    expect(input.source).toEqual({ url: "/city-presentation/shanghai-source-ground-cover-v1.json",
      sha256: scene.environment_source.sha256, sizeBytes: scene.environment_source.size_bytes });
    expect(input.authority.osmSha256).toBe(renderScene.mesh_pack_source_sha256);
    expect(input.authority.objectsSha256).toBe(renderScene.objects_json_sha256);
    expect(input.authority.origin).toEqual({ latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50 });
    // x = east, z = -north: the northern edge has the most negative z.
    const zs = input.extent.outline.map(point => point[1]);
    expect(Math.min(...zs)).toBe(-pack.extent.north);
    expect(Math.max(...zs)).toBe(-pack.extent.south);
  });
  it("rejects a render manifest bound to another pack and a degenerate extent", () => {
    expect(() => cityVegetationInput(scene.environment_source, renderScene, "0".repeat(64), pack.extent, roadBinding))
      .toThrow(/different mesh pack/);
    expect(() => cityVegetationInput(scene.environment_source, renderScene, scene.mesh_pack.manifest.sha256,
      { ...pack.extent, east: pack.extent.west }, roadBinding)).toThrow(/finite rectangle/);
  });
});
