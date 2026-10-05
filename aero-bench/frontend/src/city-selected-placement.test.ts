// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { StaticPresentationManifest, StaticSignal, StaticSignalInventory } from "./city-authoring-api";
import { CITY_SELECTED_DRAFT_SCHEMA, selectionDigest, type SelectedSceneDraft } from "./city-selected-draft";
import { CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario,
  type CitySelectedScenario, type SelectedScenarioFacility, type SelectedScenarioNoFlyZone } from "./city-selected-scenario";
import type { SceneSelection } from "./city-region-selector";
import { validateCitySelectedPlacement, type SelectedPlacementInput } from "./city-selected-placement";
import type { PlacementBox } from "./city-workspace-geometry";
import type { SourceBuildingTriangleRange } from "./city-building-shape";

/** Build the scene-bound source triangle slice for one building, exactly as
 * `loadPackedScene` exposes it for the static-presentation scene. */
function roofMeshTriangles(triangles: readonly (readonly [number, number, number, number,
  number, number, number, number, number])[]): SourceBuildingTriangleRange[] {
  const positions = new Float32Array(triangles.length * 9);
  const normals = new Float32Array(triangles.length * 9);
  const indices = new Uint32Array(triangles.length * 3);
  triangles.forEach((tri, triangleIndex) => {
    for (let corner = 0; corner < 3; corner++) {
      positions[triangleIndex * 9 + corner * 3] = tri[corner * 3]!;
      positions[triangleIndex * 9 + corner * 3 + 1] = tri[corner * 3 + 1]!;
      positions[triangleIndex * 9 + corner * 3 + 2] = tri[corner * 3 + 2]!;
    }
    normals.fill(1, triangleIndex * 9 + 1, triangleIndex * 9 + 3);
    indices[triangleIndex * 3] = triangleIndex * 3;
    indices[triangleIndex * 3 + 1] = triangleIndex * 3 + 1;
    indices[triangleIndex * 3 + 2] = triangleIndex * 3 + 2;
  });
  return [{ positions, normals, indices, firstTriangle: 0, endTriangle: triangles.length,
    roofMaterial: true }];
}

/** Deterministic 64-hex digest-shaped string per seed; fixtures pin identity by value. */
function hex64(seed: string): string {
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index++) {
    hash = Math.imul(hash ^ seed.charCodeAt(index), 16777619);
  }
  const block = (hash >>> 0).toString(16).padStart(8, "0");
  return block.repeat(8);
}

const sourceId = "test-city-osm-v1";
const jobId = hex64("job");
const rawSourceSha = hex64("raw-source");
const effectiveSha = hex64("effective-osm");
const sumoSourceSha = hex64("sumo-source");
const compilerSha = hex64("compiler");
const packManifestSha = hex64("pack-manifest");
const presentationManifestSha = hex64("presentation-manifest");
const networkSha = hex64("network");
const roadSha = hex64("road");
const buildingPlacementSha = hex64("building-placement");
const signalInventorySha = hex64("signal-inventory");
const visualAssetsSha = hex64("visual-assets");
const styleTreeSha = hex64("style-tree");
const bigcityTreeSha = hex64("bigcity-tree");
const surfaceSha = hex64("displayed-surface");
const textureSha = hex64("concrete-texture");

const origin = {
  latitude_deg: 31.23, longitude_deg: 121.47,
  ellipsoid_height_m: 50, geoid_undulation_m: 30, amsl_m: 20,
};

const defaultBounds: SceneSelection["bounds_enu_m"] = {
  min_east_m: -500, max_east_m: 500, min_north_m: -400, max_north_m: 400,
};

/** Motor-roadbed kept away from the centre so clear placements test cleanly. */
const defaultRoadOutline: readonly (readonly [number, number])[] = [[200, 200], [320, 200], [320, 320], [200, 320]];

function makeBuildingDocument(rows: readonly Record<string, unknown>[],
                              completeEnvelopes: readonly Record<string, unknown>[] = []): unknown {
  return {
    schema_version: "aero-bench.city-building-placement/v1",
    mesh_pack_source_sha256: effectiveSha,
    mesh_pack_manifest_sha256: packManifestSha,
    displayed_surface_sha256: surfaceSha,
    source_kind: "verified-source-surfaces-and-inscribed-rectangles",
    complete_footprint_buildings: completeEnvelopes.map(row => row.building_id),
    complete_footprint_envelopes: completeEnvelopes,
    occluded_buildings: [],
    placements: rows,
  };
}

function buildingRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { building_id: "b-1", part: 0, x: 50, z: -20, width: 20, depth: 20,
    height: 10, rotation_deg: 0, base_y: 0, ...overrides };
}

function makeRoad(roadbedOutline: readonly (readonly [number, number])[] = defaultRoadOutline,
                  streetLamps: readonly { x: number; z: number; rotation_deg: number }[] = []): unknown {
  const concreteAssetUrl = `/authoring/v1/scenes/${jobId}/pack/assets/${textureSha}`;
  return {
    schema_version: "aero-bench.city-static-road/v1",
    source_network_sha256: networkSha,
    mesh_pack_source_sha256: effectiveSha,
    building_placement_sha256: buildingPlacementSha,
    displayed_surface_sha256: surfaceSha,
    source_osm_sha256: sumoSourceSha,
    mesh_pack_manifest_sha256: packManifestSha,
    signal_inventory_sha256: signalInventorySha,
    road_surface_materials: {
      asphalt: { texture: "bigcity-asphalt", source: "urban-traffic" },
      concrete: {
        texture: "job-concrete", source: "job-pack",
        texture_asset: { url: concreteAssetUrl, sha256: textureSha, size_bytes: 1000 },
        uv_repeat_m: [4, 4], source_way_count: 0, source_way_triangles: 0,
        source_junction_count: 0, source_junction_triangles: 0, source_triangle_count: 0,
        rendered_area_m2: 0,
      },
    },
    concrete_roadbed_area_m2: 0,
    roadbed_area_m2: 100,
    walkbed_area_m2: 50,
    internal_lane_count: 2,
    roadbed: [{ outline: [...roadbedOutline], holes: [] }],
    concrete_roadbed: [],
    walkbed: [{ outline: [...defaultRoadOutline], holes: [] }],
    curb_edges: [],
    signal_road_coverage: {},
    lanes: [{ id: "lane0", width: 3.5, kind: "motor", shape: [[-100, 0], [100, 0]] }],
    marking_widths_m: { lane_edge: 0.12, lane_divider: 0.12, derived_direction_guide: 0.12 },
    direction_guides: [],
    junctions: [],
    crossings: [],
    street_lamps: [...streetLamps],
    street_layout: {
      source_kind: "sumo-topology-with-explicit-derived-urban-design",
      walking_area_source_count: 1,
      walking_area_count: 1,
      omitted_degenerate_walking_areas: [],
      sidewalk_provenance: {
        source_kind: "derived_osm_eligible_corridor_visual_geometry",
        sumo_lane_topology_modified: false,
        surface_precision_normalized: true,
        sidewalk_kind: "derived_paved_sidewalk_not_surveyed",
        median_kind: "derived_paved_narrow_enclosed_strip_not_surveyed",
        crossing_cuts_affect: "curb_lines_only",
      },
      sidewalk_stats: {
        derived_walkbed_area_m2: 50, median_beds_area_m2: 0, sidewalk_width_m: 3,
        building_clearance_m: 0.5, vehicle_clearance_m: 0.5, precision_grid_m: 0.001,
        effective_building_clearance_m: 0.503, effective_vehicle_clearance_m: 0.503,
        raw_roadbed_area_m2: 100, roadbed_normalization_symmetric_difference_m2: 0,
        precision_clearance_margin_m: 0.003, road_walk_separation_m: 0.003,
        normalized_roadbed_area_m2: 100, normalized_source_walkbed_area_m2: 50,
      },
      road_height_m: 0.075,
      sidewalk_height_m: 0.225,
      pavement_edges: [],
      derived_walkbed: [{ outline: [...defaultRoadOutline], holes: [] }],
      median_beds: [],
      markings: [],
      arrows: [],
    },
  };
}

function makeSignals(signals: readonly StaticSignal[]): StaticSignalInventory {
  return {
    schema_version: "aero-bench.city-static-signal-inventory/v1",
    source_network_sha256: networkSha,
    mesh_pack_source_sha256: effectiveSha,
    signals: [...signals],
  };
}

function vertiport(overrides: Partial<SelectedScenarioFacility> = {}): SelectedScenarioFacility {
  return {
    id: "vp.1", name: "vertiport", kind: "vertiport",
    placement: "ground", buildingId: null, supportHeightM: null,
    position: { x: 0, z: 0 }, rotationDeg: 0, widthM: 20, depthM: 20, heightM: 5,
    landing: { parkingSlots: 2, movementsPerHour: 30 }, cargo: null, charging: null,
    ...overrides,
  };
}

interface CityOptions {
  readonly facilities?: readonly SelectedScenarioFacility[];
  readonly noFlyZones?: readonly SelectedScenarioNoFlyZone[];
  readonly buildingPlacements?: readonly Record<string, unknown>[];
  readonly completeEnvelopes?: readonly Record<string, unknown>[];
  readonly roadbedOutline?: readonly (readonly [number, number])[];
  readonly streetLamps?: readonly { x: number; z: number; rotation_deg: number }[];
  readonly signals?: readonly StaticSignal[];
  readonly staticObstacles?: readonly PlacementBox[];
  readonly rooftopMesh?: ReadonlyMap<string, SourceBuildingTriangleRange[]>;
  readonly manifestPatch?: Partial<StaticPresentationManifest>;
  readonly bounds?: SceneSelection["bounds_enu_m"];
}

interface CityFixture {
  readonly scenario: CitySelectedScenario;
  readonly presentation: SelectedPlacementInput;
  readonly selection: SceneSelection;
  readonly bounds: SceneSelection["bounds_enu_m"];
}

async function makeCity(options: CityOptions = {}): Promise<CityFixture> {
  const bounds = options.bounds ?? defaultBounds;
  const selection: SceneSelection = {
    schema_version: "aero-bench.scene-selection/v1",
    source_id: sourceId,
    source_sha256: rawSourceSha,
    origin,
    bounds_enu_m: bounds,
  };
  const draft: SelectedSceneDraft = {
    purpose: "selected-scene-authoring",
    schema_version: CITY_SELECTED_DRAFT_SCHEMA,
    selection,
    selection_sha256: await selectionDigest(selection),
    job_id: jobId,
    source_sha256: rawSourceSha,
    pack_manifest_sha256: packManifestSha,
    presentation_manifest_sha256: presentationManifestSha,
  };
  const manifest: StaticPresentationManifest = {
    schema_version: "aero-bench.city-static-presentation/v1",
    job_id: jobId,
    selection_sha256: draft.selection_sha256,
    source_id: sourceId,
    raw_source_sha256: rawSourceSha,
    effective_osm_sha256: effectiveSha,
    sumo_source_osm_sha256: sumoSourceSha,
    compiler_manifest_sha256: compilerSha,
    origin,
    pack_manifest: { sha256: packManifestSha, size_bytes: 1024 },
    network: { sha256: networkSha, size_bytes: 2048, projection: "EPSG:32651",
      sumo_image_id: `sha256:${networkSha}` },
    road: { sha256: roadSha, size_bytes: 4096 },
    building_placement: { sha256: buildingPlacementSha, size_bytes: 512 },
    signal_inventory: { sha256: signalInventorySha, size_bytes: 256 },
    visual_assets: { sha256: visualAssetsSha, size_bytes: 1024 * 1024 },
    osm2world_style_tree_sha256: styleTreeSha,
    bigcity_library_tree_sha256: bigcityTreeSha,
    ...options.manifestPatch,
  };
  const scenario = parseCitySelectedScenario({
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene: draft,
    facilities: options.facilities ?? [],
    noFlyZones: options.noFlyZones ?? [],
    fleet: [],
    demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
  });
  const presentation: SelectedPlacementInput = {
    manifest,
    presentationManifestSha256: presentationManifestSha,
    buildingPlacement: makeBuildingDocument(options.buildingPlacements ?? [], options.completeEnvelopes ?? []),
    road: makeRoad(options.roadbedOutline, options.streetLamps ?? []),
    signals: makeSignals(options.signals ?? []),
    staticObstacles: options.staticObstacles,
    rooftopMesh: options.rooftopMesh,
  };
  return { scenario, presentation, selection, bounds };
}

describe("selected city placement adapter", () => {
  it("accepts a clean placement with no issues", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport()],
    });
    expect(await validateCitySelectedPlacement(scenario, presentation)).toEqual([]);
  });

  it("rejects a presentation that does not belong to the selected scene identity", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport()],
      buildingPlacements: [buildingRow({ x: 0, z: 0 })],
    });
    const otherJob = { ...presentation, manifest: { ...presentation.manifest, job_id: hex64("other-job") } };
    const issues = await validateCitySelectedPlacement(scenario, otherJob);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toMatchObject({ path: "selectedScene.job_id", code: "identity_mismatch" });

    const otherSource = { ...presentation, manifest: { ...presentation.manifest, source_id: "other-city-osm-v1" } };
    expect((await validateCitySelectedPlacement(scenario, otherSource)).some(issue =>
      issue.code === "identity_mismatch" && issue.path === "selectedScene.selection.source_id")).toBe(true);

    const otherOrigin = { ...presentation, manifest: { ...presentation.manifest, origin: { ...origin, latitude_deg: 32 } } };
    expect((await validateCitySelectedPlacement(scenario, otherOrigin)).some(issue =>
      issue.code === "identity_mismatch" && issue.path === "selectedScene.selection.origin")).toBe(true);

    const otherPack = { ...presentation,
      manifest: { ...presentation.manifest, pack_manifest: { sha256: hex64("other-pack"), size_bytes: 1024 } } };
    expect((await validateCitySelectedPlacement(scenario, otherPack)).some(issue =>
      issue.code === "identity_mismatch" && issue.path === "selectedScene.pack_manifest_sha256")).toBe(true);

    // No placement measurement is ever reported against the wrong city.
    expect(issues.some(issue => issue.code === "building_overlap")).toBe(false);
  });

  it("requires the verified presentation manifest hash", async () => {
    const { scenario, presentation } = await makeCity();
    const issues = await validateCitySelectedPlacement(scenario, {
      ...presentation, presentationManifestSha256: hex64("other-presentation"),
    });
    expect(issues).toMatchObject([{
      path: "selectedScene.presentation_manifest_sha256", code: "identity_mismatch",
    }]);
  });

  it("rejects a selection document that no longer hashes to its bound digest", async () => {
    const { scenario, presentation } = await makeCity();
    const tampered = {
      ...scenario,
      selectedScene: {
        ...scenario.selectedScene,
        selection: {
          ...scenario.selectedScene.selection,
          bounds_enu_m: { ...scenario.selectedScene.selection.bounds_enu_m, max_east_m: 499 },
        },
      },
    } as CitySelectedScenario;
    const issues = await validateCitySelectedPlacement(tampered, presentation);
    expect(issues.some(issue => issue.code === "selection_digest_mismatch"
      && issue.path === "selectedScene.selection")).toBe(true);
  });

  it("flags a facility whose rotated corner leaves the selected ENU bounds", async () => {
    // Unrotated the 20 m box at east 488 / north 390 fits; the 45° footprint does not.
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.corner", position: { x: 488, z: -390 }, rotationDeg: 45 })],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "facility_out_of_bounds"
      && issue.path === "facilities[0].position")).toBe(true);
    expect(issues.some(issue => issue.code === "facility_out_of_bounds"
      && issue.message.includes("vp.corner"))).toBe(true);
  });

  it("flags a facility overlapping a verified building", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ position: { x: 50, z: -20 } })],
      buildingPlacements: [buildingRow()],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "building_overlap"
      && issue.path === "facilities[0]" && issue.obstacleId === "b-1:0")).toBe(true);
  });

  it("uses the full rendered source envelope in place of an inscribed placement", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ position: { x: 28, z: -20 }, widthM: 12, depthM: 8 })],
      buildingPlacements: [buildingRow({ x: 50, width: 10 })],
      completeEnvelopes: [buildingRow({ x: 50, width: 60 })],
    });
    expect((await validateCitySelectedPlacement(scenario, presentation)).some(issue =>
      issue.code === "building_overlap" && issue.obstacleId === "b-1:0")).toBe(true);
  });

  it("flags a facility sitting on the verified motor roadbed", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.road" })],
      roadbedOutline: [[-100, -100], [100, -100], [100, 100], [-100, 100]],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "road_overlap"
      && issue.path === "facilities[0]" && issue.obstacleId === "roadbed:0")).toBe(true);
  });

  it("flags two facilities that overlap each other", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [
        vertiport({ id: "vp.a", position: { x: 0, z: 0 }, widthM: 30, depthM: 30 }),
        vertiport({ id: "vp.b", position: { x: 10, z: 0 }, widthM: 30, depthM: 30 }),
      ],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "facility_overlap"
      && issue.path === "facilities[1]" && issue.obstacleId === "vp.a")).toBe(true);
  });

  it("flags a vertiport overlapping a no-fly region", async () => {
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.nfz" })],
      noFlyZones: [{
        id: "nfz.hospital", name: "hospital",
        polygon: [{ x: -50, z: -50 }, { x: 50, z: -50 }, { x: 50, z: 50 }, { x: -50, z: 50 }],
        floorM: 0, ceilingM: 120, startsAtS: 0, endsAtS: null,
        source: { kind: "manual", label: "test", uri: null },
      }],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "airspace_overlap"
      && issue.path === "facilities[0]" && issue.obstacleId === "nfz.hospital")).toBe(true);
  });

  it("flags a no-fly polygon extending past the selected ENU bounds", async () => {
    const { scenario, presentation } = await makeCity({
      noFlyZones: [{
        id: "nfz.spill", name: "spill",
        polygon: [{ x: 400, z: -400 }, { x: 700, z: -400 }, { x: 700, z: -200 }, { x: 400, z: -200 }],
        floorM: 0, ceilingM: 60, startsAtS: 0, endsAtS: null,
        source: { kind: "manual", label: "test", uri: null },
      }],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "no_fly_out_of_bounds"
      && issue.path === "noFlyZones[0].polygon")).toBe(true);
    expect(issues.some(issue => issue.code === "facility_out_of_bounds")).toBe(false);
  });

  it("reports unverified static-asset clearance instead of inventing footprints", async () => {
    const { scenario, presentation } = await makeCity({
      signals: [{ id: "s.1", tls: "tls.1", link: 1, x: 0, z: 0, heading: 90 }],
      streetLamps: [{ x: 5, z: 5, rotation_deg: 0 }],
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    const unverified = issues.filter(issue => issue.code === "unverified_static_assets");
    expect(unverified.map(issue => issue.path).sort()).toEqual(["road.street_lamps", "signals"]);
    expect(issues.some(issue => issue.code === "static_overlap")).toBe(false);
  });

  it("checks measured lamp and signal meshes when the selected renderer supplies their bounds", async () => {
    const staticObstacles: PlacementBox[] = [
      { id: "lamp:0:street_light_8", kind: "street_asset", x: 0, z: 0,
        widthM: 1, depthM: 1, heightM: 7, rotationDeg: 0, baseY: 0 },
      { id: "signal:s.1:body", kind: "street_asset", x: 25, z: 0,
        widthM: 1, depthM: 1, heightM: 5, rotationDeg: 0, baseY: 0 },
    ];
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport()],
      signals: [{ id: "s.1", tls: "tls.1", link: 1, x: 25, z: 0, heading: 90 }],
      streetLamps: [{ x: 0, z: 0, rotation_deg: 0 }], staticObstacles,
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "static_overlap"
      && issue.obstacleId === "lamp:0:street_light_8")).toBe(true);
    expect(issues.some(issue => issue.code === "unverified_static_assets")).toBe(false);
  });

  it("surfaces a stale static road digest and does not trust its polygons", async () => {
    const staleRoad = makeRoad([[-100, -100], [100, -100], [100, 100], [-100, 100]]) as Record<string, unknown>;
    staleRoad.source_network_sha256 = hex64("stale-network");
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.road" })],
    });
    const issues = await validateCitySelectedPlacement(scenario, {
      ...presentation,
      road: staleRoad,
    });
    expect(issues.some(issue => issue.code === "road_link_mismatch"
      && issue.path === "road.source_network_sha256")).toBe(true);
    // Untrusted road geometry is never fed to overlap checks.
    expect(issues.some(issue => issue.code === "road_overlap")).toBe(false);
  });

  it("accepts a rooftop vertiport measured from verified source triangles", async () => {
    const rooftopMesh = new Map([["b-1", roofMeshTriangles([
      // Level roof spanning the whole 30×20 building envelope; building-local
      // (-15,-10)..(15,10) maps to world (35,-30)..(65,-10).
      [35, 10, -30, 65, 10, -10, 65, 10, -30],
      [35, 0, -30, 65, 0, -30, 35, 10, -30],
    ])]]);
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.roof", placement: "rooftop", buildingId: "b-1",
        supportHeightM: 10, position: { x: 57.5, z: -25 }, widthM: 14, depthM: 10, heightM: 4.5 })],
      completeEnvelopes: [buildingRow({ x: 50, z: -20, width: 30, depth: 20, height: 10 })],
      rooftopMesh,
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues).toEqual([]);
  });

  it("rejects a rooftop site whose building has no flat source roof triangle", async () => {
    // A wall-only mesh bounds the envelope in Y but offers no horizontal surface.
    const rooftopMesh = new Map([["b-1", roofMeshTriangles([
      [35, 0, -30, 65, 0, -30, 35, 10, -30],
    ])]]);
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.ghost", placement: "rooftop", buildingId: "b-1",
        supportHeightM: 10, position: { x: 57.5, z: -25 }, widthM: 14, depthM: 10, heightM: 4.5 })],
      completeEnvelopes: [buildingRow({ x: 50, z: -20, width: 30, depth: 20, height: 10 })],
      rooftopMesh,
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "rooftop_unsupported"
      && issue.path === "facilities[0].buildingId")).toBe(true);
    // The floating pad is never silently accepted even though nothing collides.
    expect(issues.some(issue => issue.code === "rooftop_obstacle")).toBe(false);
  });

  it("rejects a rooftop pad whose volume hits an above-roof parapet", async () => {
    const rooftopMesh = new Map([["b-1", roofMeshTriangles([
      // The envelope bounds the whole mesh: its height is the parapet top (11).
      // The flat ceiling at 10 supports the pad; the parapet wall pokes THROUGH the
      // pad volume right under the 14×10 footprint (x 54..62, z -25).
      [35, 10, -30, 65, 10, -10, 65, 10, -30],
      [35, 0, -30, 65, 0, -30, 35, 10, -30],
      [54, 10, -25, 62, 10, -25, 62, 11, -25],
      [54, 10, -25, 62, 11, -25, 54, 11, -25],
    ])]]);
    const { scenario, presentation } = await makeCity({
      facilities: [vertiport({ id: "vp.parapet", placement: "rooftop", buildingId: "b-1",
        supportHeightM: 10, position: { x: 57.5, z: -25 }, widthM: 14, depthM: 10, heightM: 4.5 })],
      completeEnvelopes: [buildingRow({ x: 50, z: -20, width: 30, depth: 20, height: 11 })],
      rooftopMesh,
    });
    const issues = await validateCitySelectedPlacement(scenario, presentation);
    expect(issues.some(issue => issue.code === "rooftop_obstacle"
      && issue.path === "facilities[0].position")).toBe(true);
  });
});
