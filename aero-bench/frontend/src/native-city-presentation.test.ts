// @vitest-environment node
import { createHash } from "node:crypto";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import type { PublicBuilding, PublicEntityDefinition, PublicScenario,
  PublicScenarioAsset } from "./generated/aero-bench-contracts";
import { inspectNativeCityPresentation, loadNativeCityPresentation, planNativeCityPresentation,
  validateNativeBuildingGlb } from "./native-city-presentation";
import { pose, scenario } from "./testing/trace-v3-fixture";

// Texture transport is covered by city-terrain-surfaces tests; retain the real geometry loader here.
import * as terrain from "./city-terrain-surfaces";
import { fakeTerrainTextureSets } from "./testing/terrain-surface-fixture";
vi.spyOn(terrain, "loadVerifiedTerrainTextureSets").mockImplementation(async options =>
  fakeTerrainTextureSets(options.setIds));

const origin = { latitude_deg: 31.2, longitude_deg: 121.5, ellipsoid_height_m: 4 };
const objectsSha256 = "b".repeat(64);
const displayedSurfaceSha256 = "d".repeat(64);
const buildingId = "building.unit.component.0";
const entityId = "fixture.building.entity";
const renderAssetId = "render.building.unit.component.0";

function encoded(value: unknown): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(JSON.stringify(value));
}

function digest(bytes: Uint8Array | ArrayBuffer): string {
  return createHash("sha256").update(bytes instanceof ArrayBuffer ? new Uint8Array(bytes) : bytes).digest("hex");
}

function digestBytes(bytes: ArrayBuffer): Promise<string> {
  return Promise.resolve(digest(bytes));
}

function makeGlb(): Uint8Array<ArrayBuffer> {
  const positions = [
    0, 0, 0, 2, 0, 0, 2, 0, -2, 0, 0, -2,
    0, 3, 0, 2, 3, 0, 2, 3, -2, 0, 3, -2,
  ];
  const indices = [
    0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
    0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5,
    2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7,
  ];
  const bin = new Uint8Array(positions.length * 4 + indices.length * 2);
  const data = new DataView(bin.buffer);
  positions.forEach((value, index) => data.setFloat32(index * 4, value, true));
  indices.forEach((value, index) => data.setUint16(positions.length * 4 + index * 2, value, true));
  const document = {
    asset: { version: "2.0", extras: {
      schema: "aero-bench.building-render-scaleout/v0", object_id: buildingId,
      source_frame: "asset_local", frame: {
        placement: "ENU_east = anchor_east_m + X; ENU_north = anchor_north_m - Z; ENU_up = base_enu_up_m + Y",
        anchor_east_m: 10, anchor_north_m: 20, base_enu_up_m: 2,
      }, building: { height_m: 3, footprint_vertex_count: 4 },
    } },
    buffers: [{ byteLength: bin.byteLength }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.length * 4, target: 34962 },
      { buffer: 0, byteOffset: positions.length * 4, byteLength: indices.length * 2, target: 34963 },
    ],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 8, type: "VEC3", min: [0, 0, -2], max: [2, 3, 0] },
      { bufferView: 1, componentType: 5123, count: indices.length, type: "SCALAR" },
    ],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    nodes: [{ mesh: 0 }], scenes: [{ nodes: [0] }], scene: 0,
  };
  const rawJson = new TextEncoder().encode(JSON.stringify(document));
  const jsonLength = Math.ceil(rawJson.byteLength / 4) * 4;
  const total = 12 + 8 + jsonLength + 8 + bin.byteLength;
  const glb = new Uint8Array(total);
  const header = new DataView(glb.buffer);
  header.setUint32(0, 0x46546c67, true);
  header.setUint32(4, 2, true);
  header.setUint32(8, total, true);
  header.setUint32(12, jsonLength, true);
  header.setUint32(16, 0x4e4f534a, true);
  glb.fill(0x20, 20, 20 + jsonLength);
  glb.set(rawJson, 20);
  header.setUint32(20 + jsonLength, bin.byteLength, true);
  header.setUint32(24 + jsonLength, 0x004e4942, true);
  glb.set(bin, 28 + jsonLength);
  return glb;
}

function road(osmSha256: string): Record<string, unknown> {
  const ring = [[-8, -8], [8, -8], [8, -5], [-8, -5]];
  const polygon = { outline: ring, holes: [] };
  const sourceContext = {
    schema_version: "aero-bench.city-rendered-source-context/v1",
    mesh_pack_source_sha256: osmSha256, objects_json_sha256: objectsSha256,
    origin_wgs84: origin,
    building_geometry: { count: 1,
      actual_glb_footprint_audit: { count: 1, boundary_tolerance_m: 0.001 } },
  };
  return {
    schema_version: "aero-bench.city-road-preview/v3",
    source_kind: "sumo-network-visual-geometry", road_scope: "ground-only",
    source_network_sha256: "a".repeat(64), mesh_pack_source_sha256: osmSha256,
    displayed_surface_sha256: displayedSurfaceSha256,
    source_context: sourceContext, physical_clearance: { status: "PASS" },
    road_surface_materials: {
      asphalt: { texture: "/asphalt.png", source: "pinned asphalt triangles" },
      concrete: { texture: "/concrete.jpg", source: "tagged concrete road triangles",
        texture_asset: { url: `/osm2world/packs/unit/assets/${"e".repeat(64)}`,
          sha256: "e".repeat(64), size_bytes: 16 },
        uv_repeat_m: [1.2, 0.6], source_way_count: 1, source_way_triangles: 1,
        source_junction_count: 0, source_junction_triangles: 0,
        source_triangle_count: 1, rendered_area_m2: 48 },
    },
    concrete_roadbed_area_m2: 48, roadbed_area_m2: 48, walkbed_area_m2: 48,
    internal_lane_count: 0, roadbed: [polygon], concrete_roadbed: [polygon], walkbed: [polygon],
    curb_edges: [], signal_road_coverage: {},
    lanes: [{ id: "w1#0_0", width: 3, kind: "motor", shape: [[-8, -6.5], [8, -6.5]] }],
    marking_widths_m: { lane_edge: 0.14, lane_divider: 0.13, derived_direction_guide: 0.15 },
    direction_guides: [{ id: "direction-guide:w1:0", source_way_id: "w1",
      edge_ids: ["w1#0", "-w1#0"], marking: "derived_direction_guide",
      source_kind: "sumo-lane-topology-not-surveyed-marking", width_m: 0.15,
      shape: [[-8, -6.5], [8, -6.5]] }],
    junctions: [], crossings: [], street_lamps: [],
    street_layout: {
      source_kind: "sumo-topology-with-explicit-derived-urban-design",
      walking_area_source_count: 0, walking_area_count: 0, omitted_degenerate_walking_areas: [],
      sidewalk_provenance: {
        source_kind: "derived_osm_eligible_corridor_visual_geometry",
        sumo_lane_topology_modified: false, surface_precision_normalized: true,
        sidewalk_kind: "derived_paved_sidewalk_not_surveyed",
        median_kind: "derived_paved_narrow_enclosed_strip_not_surveyed",
        crossing_cuts_affect: "curb_lines_only",
      },
      sidewalk_stats: {
        derived_walkbed_area_m2: 0, median_beds_area_m2: 0, sidewalk_width_m: 2.2,
        building_clearance_m: 0.15, vehicle_clearance_m: 0.15, precision_grid_m: 0.001,
        effective_building_clearance_m: 0.153, effective_vehicle_clearance_m: 0.153,
        raw_roadbed_area_m2: 48, roadbed_normalization_symmetric_difference_m2: 0,
        precision_clearance_margin_m: 0.003, road_walk_separation_m: 0.003,
        normalized_roadbed_area_m2: 48, normalized_source_walkbed_area_m2: 48,
      },
      road_height_m: 0.075, sidewalk_height_m: 0.225,
      pavement_edges: [], derived_walkbed: [], median_beds: [], markings: [], arrows: [],
    },
  };
}

function effectiveFixtures(sourceContext: Record<string, unknown>): Record<string, unknown> {
  const location = { id: "unit-signal", x: 25, z: 25 };
  const footprint = { outline: [[24.8, 24.8], [25.2, 24.8], [25.2, 25.2], [24.8, 25.2]],
    holes: [[[24.9, 24.9], [24.900001, 24.9], [24.9, 24.900001]]] };
  return {
    schema_version: "aero-bench.city-effective-fixture-geometry/v1",
    displayed_surface_sha256: displayedSurfaceSha256, source_context: sourceContext,
    models: { signal: { sha256: "e".repeat(64) }, street_lamp: { sha256: "f".repeat(64) } },
    source_inventories: { signals: [location], street_lamps: [] },
    effective_fixtures: [{ id: "signal:unit-signal", kind: "signal", source_index: 0,
      source_location: location, model_sha256: "e".repeat(64), base_up_m: 0, top_up_m: 4,
      footprints: [footprint], motion_footprints: [footprint] }],
    omitted_signals: [], omitted_street_lamps: [],
    counts: { effective_signals: 1, effective_street_lamps: 0,
      source_signals: 1, source_street_lamps: 0, omitted_signals: 0, omitted_street_lamps: 0 },
  };
}

function environment(osmSha256: string): Record<string, unknown> {
  return {
    schemaVersion: "aero-bench.city-environment-source/v1",
    coordinateFrame: "x-east,y-up,z-south-meters",
    source: { osmSha256, objectsSha256, projection: "WGS84->ECEF->ENU", origin },
    buildings: [{ id: buildingId, geometrySha256: "c".repeat(64),
      outline: [[10, -20], [12, -20], [12, -22], [10, -22]], holes: [] }],
    greens: [{ id: "green.unit", outline: [[30, 30], [34, 30], [34, 34], [30, 34]], holes: [],
      provenance: { kind: "osm", sourceSha256: osmSha256, elementType: "way", elementId: "4",
        sourceElementType: "way", sourceElementId: "4", tags: { landuse: "grass" } } }],
    sourceTrees: [],
    groundCovers: [{ id: "osm:way:5:0", kind: "parking", surface: null,
      outline: [[35, 30], [37, 30], [37, 32], [35, 32]], holes: [],
      provenance: { kind: "osm", sourceSha256: osmSha256, elementType: "way", elementId: "5",
        sourceElementType: "way", sourceElementId: "5", tags: { amenity: "parking" } } }],
    inspection: { taggedAreaCount: 1, greenAreaCount: 1, sourceTreeCount: 0,
      excludedTags: {}, measurementStatus: "measured", groundCoverCount: 1,
      groundCoverCountsByKind: { parking: 1 }, groundCoverSourceAreaM2ByKind: { parking: 4 } },
  };
}

function asset(assetId: string, mediaType: string, bytes: Uint8Array<ArrayBuffer>): PublicScenarioAsset {
  const sha256 = digest(bytes);
  return { asset_id: assetId, selector: `${assetId}.bin`, sha256, size_bytes: bytes.byteLength,
    media_type: mediaType, replay_path: `assets/${sha256}`, license_id: null, license_selector: null,
    license_sha256: null, license_size_bytes: null, license_replay_path: null } as PublicScenarioAsset;
}

interface Fixture {
  readonly scenario: PublicScenario;
  readonly bytes: ReadonlyMap<string, Uint8Array<ArrayBuffer>>;
  readonly glb: Uint8Array<ArrayBuffer>;
}

function fixture(): Fixture {
  const osm = new TextEncoder().encode('{"version":0.6,"generator":"unit","elements":['
    + '{"type":"node","id":9007199254740992,"lat":31.2,"lon":121.5},'
    + '{"type":"node","id":9007199254740993,"lat":31.2,"lon":121.5001},'
    + '{"type":"way","id":3,"nodes":[9007199254740992,9007199254740993]}]}');
  const osmSha256 = digest(osm);
  const roadValue = road(osmSha256);
  const fixtureValue = effectiveFixtures(roadValue.source_context as Record<string, unknown>);
  const environmentValue = environment(osmSha256);
  const glb = makeGlb();
  const assets = [asset("scene.effective_osm", "application/json", osm),
    asset("asset.layer-road-v3", "application/json", encoded(roadValue)),
    asset("asset.layer-effective-fixtures", "application/json", encoded(fixtureValue)),
    asset("asset.layer-source-ground-cover", "application/json", encoded(environmentValue)),
    asset(renderAssetId, "model/gltf-binary", glb)];
  const bytes = new Map(assets.map((entry, index) => [entry.sha256,
    [osm, encoded(roadValue), encoded(fixtureValue), encoded(environmentValue), glb][index]!]));
  const vertex = (east_m: number, north_m: number, up_m: number) =>
    ({ enu: { east_m, north_m, up_m } });
  const building = { building_id: buildingId, entity_id: entityId, render_asset_id: renderAssetId,
    anchor_east_m: 11.25, anchor_north_m: 21.25,
    base_vertices: [vertex(10, 20, 2), vertex(12, 20, 2), vertex(12, 22, 2), vertex(10, 22, 2)],
    top_vertices: [vertex(10, 20, 5), vertex(12, 20, 5), vertex(12, 22, 5), vertex(10, 22, 5)],
  } as unknown as PublicBuilding;
  const entity = { entity_id: entityId, kind: "static_asset", owner_kind: "scenario",
    owner_id: "fixture.world", authority_kind: "scenario_static", state: "static",
    model_asset_id: renderAssetId,
    initial_pose: pose({ position: { enu: { east_m: 16, north_m: 27, up_m: 1 } } }),
    selected_launch_override: false } as unknown as PublicEntityDefinition;
  const layers = ["osm_scene", "roads", "entities", "regions"].map((kind, index) => ({
    layer_id: `layer.${kind}`, kind, asset_id: assets[index]!.asset_id,
    visibility: "public", default_visible: true,
  }));
  const base = scenario() as unknown as PublicScenario;
  return { glb, bytes, scenario: { ...base, assets: [...base.assets, ...assets], layers,
    buildings: [building], entities: [...base.entities, entity] } as PublicScenario };
}

function fetchFrom(values: ReadonlyMap<string, Uint8Array<ArrayBuffer>>): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const bytes = values.get(url.pathname.split("/").at(-1)!);
    return bytes === undefined ? new Response(null, { status: 404 }) : new Response(bytes.slice());
  }) as typeof fetch;
}

function parsedBuilding(): { readonly root: THREE.Mesh; readonly geometry: THREE.BufferGeometry;
  readonly material: THREE.MeshStandardMaterial } {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute([
    0, 0, 0, 2, 0, 0, 2, 0, -2, 0, 0, -2,
    0, 3, 0, 2, 3, 0, 2, 3, -2, 0, 3, -2,
  ], 3));
  const material = new THREE.MeshStandardMaterial();
  return { root: new THREE.Mesh(geometry, material), geometry, material };
}

describe("native city presentation plan", () => {
  it("preserves the osm_mesh route and binds the complete native city inventory", () => {
    const value = fixture();
    const plan = planNativeCityPresentation(value.scenario)!;
    expect(plan.kind).toBe("native-city-assets");
    expect(plan.buildings).toHaveLength(1);
    expect(plan.originWgs84).toEqual(origin);
    expect(plan.totalBytes).toBe([...value.bytes.values()].reduce((sum, bytes) => sum + bytes.byteLength, 0));
    expect(planNativeCityPresentation({ ...value.scenario, layers: [{
      layer_id: "layer.mesh", kind: "osm_mesh", asset_id: "unresolved",
      visibility: "public", default_visible: true,
    }] } as PublicScenario)).toBeNull();
    expect(inspectNativeCityPresentation({ ...value.scenario, layers: [{
      layer_id: "layer.mesh", kind: "osm_mesh", asset_id: "unresolved",
      visibility: "public", default_visible: true,
    }] } as PublicScenario)).toEqual({ kind: "mesh-pack" });
  });

  it("fails closed on an incomplete layer set or a foreign GLB", () => {
    const value = fixture();
    expect(() => planNativeCityPresentation({ ...value.scenario,
      layers: value.scenario.layers.slice(1) } as PublicScenario)).toThrow(/exactly/);
    expect(inspectNativeCityPresentation({ ...value.scenario,
      layers: value.scenario.layers.slice(1) } as PublicScenario)).toEqual({
      kind: "unavailable",
      reason: "Native city assets route supports exactly osm_scene, roads, entities, and regions layers",
    });
    const extra = { ...value.scenario.assets.find(item => item.asset_id === renderAssetId)!,
      asset_id: "render.building.foreign" };
    expect(() => planNativeCityPresentation({ ...value.scenario,
      assets: [...value.scenario.assets, extra] } as PublicScenario)).toThrow(/foreign or unbound/);
  });
});

describe("native city building GLB validation", () => {
  it("accepts the declared embedded frame and rejects a changed anchor", () => {
    const value = fixture();
    const binding = planNativeCityPresentation(value.scenario)!.buildings[0]!;
    expect(validateNativeBuildingGlb(value.glb.buffer, binding).measuredBounds.min.toArray())
      .toEqual([10, 2, -22]);
    const changed = value.glb.slice();
    const jsonLength = new DataView(changed.buffer).getUint32(12, true);
    const document = JSON.parse(new TextDecoder().decode(changed.subarray(20, 20 + jsonLength)));
    document.asset.extras.frame.anchor_east_m = 10.5;
    const replacement = new TextEncoder().encode(JSON.stringify(document));
    expect(replacement.byteLength).toBeLessThanOrEqual(jsonLength);
    changed.fill(0x20, 20, 20 + jsonLength);
    changed.set(replacement, 20);
    expect(() => validateNativeBuildingGlb(changed.buffer, binding)).toThrow(/envelope/);
  });
});

describe("native city presentation loading", () => {
  it("loads verified declared geometry, applies the embedded render anchor, and owns disposal", async () => {
    const value = fixture();
    const plan = planNativeCityPresentation(value.scenario)!;
    const parsed = parsedBuilding();
    const disposeGeometry = vi.spyOn(parsed.geometry, "dispose");
    const disposeMaterial = vi.spyOn(parsed.material, "dispose");
    const progress: string[] = [];
    const loaded = await loadNativeCityPresentation(plan, {
      baseHref: "https://viewer.test/native/", fetch: fetchFrom(value.bytes), digest: digestBytes,
      parseGlb: async () => parsed.root,
      onProgress: update => progress.push(`${update.phase}:${update.assetId}`),
    });
    expect(loaded.stats).toMatchObject({ buildingCount: 1, roadbedPolygonCount: 1,
      walkbedPolygonCount: 1, fixtureCount: 1, omittedFixtureInteriorRingCount: 2,
      greenCount: 1, groundCoverCount: 1,
      osmElementCount: 3, verifiedBytes: plan.totalBytes });
    expect(loaded.roadClearance.provenance).toEqual({ source: "native-road-v3",
      roadSha256: plan.layers.roads.sha256, fixtureIdentity: plan.layers.entities.sha256,
      displayedSurfaceSha256 });
    expect(loaded.roadClearance.roadbed).toHaveLength(1);
    expect(loaded.roadClearance.walkbed).toHaveLength(1);
    expect(loaded.roadClearance.fixtures[0]).toMatchObject({ id: "signal:unit-signal:0", x: 25, z: 25 });
    expect(progress).toHaveLength(5);
    expect(loaded.bounds.min.toArray()).toEqual(expect.arrayContaining([expect.any(Number)]));
    expect(loaded.layers.buildings.children[0]!.position.toArray()).toEqual([16, 1, -27]);
    expect(loaded.layers.buildings.children[0]!.children[0]!.position.toArray()).toEqual([-6, 1, 7]);
    const buildingBounds = new THREE.Box3().setFromObject(loaded.layers.buildings);
    expect(buildingBounds.min.toArray()).toEqual([10, 2, -22]);
    expect(buildingBounds.max.toArray()).toEqual([12, 5, -20]);
    loaded.setLayerVisibility({ buildings: false, roads: true, regions: false, static_assets: false });
    expect([loaded.layers.buildings.visible, loaded.layers.roads.visible,
      loaded.layers.regions.visible, loaded.layers.fixtures.visible]).toEqual([false, true, false, false]);
    loaded.dispose();
    loaded.dispose();
    expect(disposeGeometry).toHaveBeenCalledTimes(1);
    expect(disposeMaterial).toHaveBeenCalledTimes(1);
  });

  it("disposes parser-owned content when assembly fails", async () => {
    const value = fixture();
    const parsed = parsedBuilding();
    const disposeGeometry = vi.spyOn(parsed.geometry, "dispose");
    const foreign = new THREE.Group();
    foreign.add(parsed.root, new THREE.PointLight());
    await expect(loadNativeCityPresentation(planNativeCityPresentation(value.scenario)!, {
      baseHref: "https://viewer.test/native/", fetch: fetchFrom(value.bytes), digest: digestBytes,
      parseGlb: async () => foreign,
    })).rejects.toThrow(/foreign scene content/);
    expect(disposeGeometry).toHaveBeenCalledTimes(1);
  });

  it("rejects valid-shaped but tampered fetched bytes before parsing", async () => {
    const value = fixture();
    const plan = planNativeCityPresentation(value.scenario)!;
    const tampered = new Map(value.bytes);
    const changed = value.glb.slice(); changed[changed.length - 1] = changed[changed.length - 1]! ^ 1;
    tampered.set(plan.buildings[0]!.asset.sha256, changed);
    const parseGlb = vi.fn();
    await expect(loadNativeCityPresentation(plan, {
      baseHref: "https://viewer.test/native/", fetch: fetchFrom(tampered), digest: digestBytes, parseGlb,
    })).rejects.toThrow(/digest/);
    expect(parseGlb).not.toHaveBeenCalled();
  });
});
