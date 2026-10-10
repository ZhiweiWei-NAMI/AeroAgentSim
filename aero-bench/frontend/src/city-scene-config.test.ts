// @vitest-environment node
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { parseCitySceneConfig } from "./city-scene-config";

const current = JSON.parse(readFileSync(new URL(
  "../public/city-presentation/huangpu-ground-scene-v1.json", import.meta.url), "utf8"));
const renderScene = JSON.parse(readFileSync(new URL(
  "../public/city-presentation/building-render-scene-v1.json", import.meta.url), "utf8"));
const defaultScene = JSON.parse(readFileSync(new URL(
  "../public/city-presentation/default-scene-v1.json", import.meta.url), "utf8"));

describe("city scene manifest", () => {
  it("binds the map, road, traffic, building placement and flight to local assets", () => {
    const scene = parseCitySceneConfig(current);
    expect(scene.mesh_pack.base_url).toBe("/osm2world/packs/shanghai-huangpu-east-v1/");
    expect(scene.initial_mood).toBe("day");
    expect(scene.mesh_pack.road_surface_texture).toContain("/Asphalt010/");
    expect(scene.assets && Object.values(scene.assets)).toHaveLength(4);
    expect(Object.values(scene.assets ?? {}).every(path => path.startsWith("/"))).toBe(true);
  });

  it("rejects a path escaping the published asset tree", () => {
    expect(() => parseCitySceneConfig({ ...current, assets: {
      ...current.assets, road: "/city-presentation/../other-road.json",
    } })).toThrow(/absolute local asset URL/);
  });

  it("rejects a changed mesh pack digest", () => {
    expect(() => parseCitySceneConfig({ ...current, mesh_pack: {
      ...current.mesh_pack, manifest: { ...current.mesh_pack.manifest, sha256: "bad" },
    } })).toThrow(/manifest ref is invalid/);
  });

  it("rejects an asphalt material path outside the published asset tree", () => {
    expect(() => parseCitySceneConfig({ ...current, mesh_pack: {
      ...current.mesh_pack, road_surface_texture: "/osm2world/../invalid.png",
    } })).toThrow(/absolute local asset URL/);
  });
});

describe("building render scene manifest", () => {
  it("parses the render scene with separate digest-bound ground assets", () => {
    const scene = parseCitySceneConfig(renderScene);
    expect(scene.assets).toBeUndefined();
    if (scene.building_render === undefined) throw new Error("building_render expected");
    expect(scene.building_render.base_url).toBe("/building-renders/shanghai-huangpu-east-v1/");
    expect(scene.building_render.manifest.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(scene.building_render.manifest.size_bytes).toBeGreaterThan(1000);
    expect(scene.building_render.source_scene_id).toBe("shanghai-huangpu-east-v1");
    expect(scene.building_render.source_context.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(scene.building_render.source_context.size_bytes).toBeGreaterThan(1000);
    expect(scene.mesh_pack.base_url).toBe("/osm2world/packs/shanghai-huangpu-east-v1/");
    expect(scene.initial_mood).toBe("day");
    expect(scene.road_assets?.source_scene_id).toBe("shanghai-huangpu-east-v1");
    expect(scene.road_assets?.road.url).toContain("huangpu-canonical-road-v3");
    expect(scene.road_assets?.traffic.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(scene.road_assets?.effective_fixtures.url).toContain("huangpu-canonical-effective-fixtures");
    expect(scene.road_assets?.schema_version).toBe("aero-bench.city-road-assets/v2");
  });

  it("keeps the default scene a labelled engineering preview of the gated canonical ground roads", () => {
    const { name: _name, ...renderWithoutName } = renderScene;
    // The default preview is exactly the render scene, including its verified canonical road
    // assets, plus its verified environment source.
    const { name, environment_source: _source, ...preview } = defaultScene;
    expect(preview).toEqual(renderWithoutName);
    expect(name).toMatch(/^ENGINEERING PREVIEW/);
    expect(parseCitySceneConfig(defaultScene).road_assets?.road.url).toContain("huangpu-canonical-road-v3");
  });

  it("binds the default scene to a verified city-presentation environment source", () => {
    const scene = parseCitySceneConfig(defaultScene);
    if (scene.building_render === undefined) throw new Error("building_render expected");
    expect(scene.environment_source).toEqual({
      url: "/city-presentation/shanghai-source-ground-cover-v1.json",
      sha256: "075df19e940381f4aa226d3a1c06ab002c7fb1d9e808426628d3c829a00a9f31",
      size_bytes: 188867,
    });
    const bytes = readFileSync(new URL(`../public${scene.environment_source!.url}`, import.meta.url));
    expect(bytes.byteLength).toBe(scene.environment_source!.size_bytes);
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(scene.environment_source!.sha256);
    expect(JSON.parse(bytes.toString("utf8")).groundCovers).toHaveLength(19);
  });

  it("rejects tampered or misplaced environment sources on render scenes", () => {
    const source = defaultScene.environment_source;
    const stripped = parseCitySceneConfig({ ...defaultScene, environment_source: undefined });
    if (stripped.building_render === undefined) throw new Error("building_render expected");
    expect(stripped.environment_source).toBeUndefined();
    expect(() => parseCitySceneConfig({ ...defaultScene, road_assets: undefined }))
      .toThrow(/environment source requires verified road_assets/);
    expect(() => parseCitySceneConfig({ ...defaultScene,
      environment_source: { ...source, sha256: "f".repeat(63) } }))
      .toThrow(/environment source ref is invalid/);
    expect(() => parseCitySceneConfig({ ...defaultScene,
      environment_source: { ...source, size_bytes: 0 } }))
      .toThrow(/environment source ref is invalid/);
    expect(() => parseCitySceneConfig({ ...defaultScene,
      environment_source: { ...source, url: "/building-renders/env.json" } }))
      .toThrow(/city-presentation JSON document/);
    expect(() => parseCitySceneConfig({ ...defaultScene,
      environment_source: { ...source, url: "/city-presentation/../secret.json" } }))
      .toThrow(/city-presentation JSON document|absolute local asset URL/);
    expect(() => parseCitySceneConfig({ ...defaultScene,
      environment_source: { ...source, extra: 1 } })).toThrow(/undeclared fields/);
    // An environment source is a render-scene extension: the pack scene rejects it.
    expect(() => parseCitySceneConfig({ ...current, environment_source: source }))
      .toThrow(/environment source requires building_render/);
  });

  it("allows an explicitly declared render scene without ground replay", () => {
    const { road_assets: _roads, ...withoutRoads } = renderScene;
    const scene = parseCitySceneConfig(withoutRoads);
    expect(scene.assets).toBeUndefined();
    expect(scene.road_assets).toBeUndefined();
    expect(scene.building_render).toBeDefined();
  });

  it("rejects a scene that mixes building_render with workspace assets", () => {
    expect(() => parseCitySceneConfig({ ...renderScene, assets: current.assets }))
      .toThrow(/must not mix building_render with workspace assets/);
  });

  it("requires the declared source scene and digest-bound context in every render variant", () => {
    const { source_scene_id: _id, ...withoutId } = renderScene.building_render;
    const { source_context: _context, ...withoutContext } = renderScene.building_render;
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: withoutId }))
      .toThrow(/source scene identity is invalid/);
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: withoutContext }))
      .toThrow(/source context ref must be an object/);
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: {
      ...renderScene.building_render, source_context: { sha256: "bad", size_bytes: 262049 },
    } })).toThrow(/source context ref is invalid/);
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: {
      ...renderScene.building_render, source_scene_id: "../other-scene",
    } })).toThrow(/source scene identity is invalid/);
    expect(parseCitySceneConfig({ ...renderScene, building_render: {
      ...renderScene.building_render, source_scene_id: "another-scene",
    } }).building_render?.source_scene_id).toBe("another-scene");
  });

  it("rejects workspace assets without a building render and vice versa", () => {
    const { building_render: _render, road_assets: _roads, ...packOnly } = renderScene;
    expect(() => parseCitySceneConfig(packOnly)).toThrow(/city scene assets must be an object/);
    const { assets: _assets, ...defaultOnly } = current;
    expect(() => parseCitySceneConfig(defaultOnly)).toThrow(/city scene assets must be an object/);
  });

  it("rejects ground replay added to a workspace building variant", () => {
    expect(() => parseCitySceneConfig({ ...current, road_assets: renderScene.road_assets }))
      .toThrow(/road assets require building_render/);
  });

  it("rejects incomplete, escaping, or undeclared ground asset references", () => {
    expect(() => parseCitySceneConfig({ ...renderScene, road_assets: {
      ...renderScene.road_assets, source_network_sha256: "bad",
    } })).toThrow(/road assets identity is invalid/);
    expect(() => parseCitySceneConfig({ ...renderScene, road_assets: {
      ...renderScene.road_assets, road: "/city-presentation/road.json",
    } })).toThrow(/road assets road must be an object/);
    expect(() => parseCitySceneConfig({ ...renderScene, road_assets: {
      ...renderScene.road_assets, road: { ...renderScene.road_assets.road, url: "/city-presentation/../road.json" },
    } })).toThrow(/absolute local asset URL/);
    expect(() => parseCitySceneConfig({ ...renderScene, road_assets: {
      ...renderScene.road_assets, building_placement: current.assets.building_placement,
    } })).toThrow(/road assets identity is invalid/);
    expect(() => parseCitySceneConfig({ ...renderScene, road_assets: {
      ...renderScene.road_assets, traffic: { ...renderScene.road_assets.traffic, size_bytes: 0 },
    } })).toThrow(/traffic ref is invalid/);
  });

  it("rejects a broken render manifest ref and an escaping base URL", () => {
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: {
      ...renderScene.building_render,
      manifest: { ...renderScene.building_render.manifest, sha256: "bad" },
    } })).toThrow(/building render manifest ref is invalid/);
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: {
      ...renderScene.building_render, base_url: "/building-renders/../escape/",
    } })).toThrow(/absolute local asset URL/);
    expect(() => parseCitySceneConfig({ ...renderScene, building_render: {
      ...renderScene.building_render, base_url: "/building-renders/shanghai-huangpu-east-v1",
    } })).toThrow(/must end in \//);
  });
});
