// @vitest-environment node
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { applyCityRoadSurfaceWetness, CITY_ROAD_WETNESS_LIMITS, crossingStripeSegments, laneOffsets,
  meterTextureUvs, prepareStreetLampPartMaterial, ribbonTopTriangles, setCityRoadLighting, streetLampHeadPosition, triangulateUpward, validateCanonicalCityRoadPayload,
  validateCityRoadPreviewPayload, validateStaticCityRoadPayload, verifiedConcreteAsset,
  verifyCityRoadIdentity } from "./city-roads";
import { displaySurfaceSha256 } from "./city-surface-identity";
import { createSurfaceWetnessUniforms } from "./city-surface-wetness";

type Point = readonly [number, number];

describe("curb ribbon top winding", () => {
  const normalY = (a: Point, b: Point, c: Point): number =>
    (b[1] - a[1]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[1] - a[1]);
  it("orients a reversed short-join triangle upward without moving any vertex", () => {
    const left: Point[] = [[-418.1554764667472, 352.4950365734733], [-418.1760216596594, 352.4461194474918]];
    const right: Point[] = [[-418.3605235332528, 352.5749634265267], [-418.35, 352.6]];
    const points = left.flatMap((point, index) => [point, right[index]!]);
    expect(normalY(points[0]!, points[1]!, points[2]!)).toBeLessThan(0);
    const indices = ribbonTopTriangles(left, right);
    expect(indices.slice(0, 3)).toEqual([0, 2, 1]);
    for (let i = 0; i < indices.length; i += 3)
      expect(normalY(points[indices[i]!]!, points[indices[i + 1]!]!, points[indices[i + 2]!]!)).toBeGreaterThanOrEqual(0);
  });
  it("keeps all published Huangpu curb top faces upward, including 752 previously reversed faces", () => {
    const data = JSON.parse(readFileSync(resolve(__dirname, "../public/city-presentation/huangpu-canonical-road-v3.json"), "utf8"));
    let corrected = 0, triangles = 0;
    for (const edge of data.curb_edges as Point[][]) {
      const { left, right } = laneOffsets(edge, .22);
      const points = left.flatMap((point, index) => [point, right[index]!]);
      const indices = ribbonTopTriangles(left, right);
      for (let i = 0; i < indices.length; i += 3) {
        const [a, b, c] = indices.slice(i, i + 3) as [number, number, number];
        expect(normalY(points[a]!, points[b]!, points[c]!)).toBeGreaterThanOrEqual(0);
        if (b !== a + (i / 3 % 2 === 0 ? 1 : 2)) corrected++;
        triangles++;
      }
    }
    expect(triangles).toBe(11298);
    // Compare against each original triangle's own second vertex.
    expect(corrected).toBe(752);
  });
});

describe("zebra crossing continuity", () => {
  it("paints the full multi-lane span with more than three stripes", () => {
    const segments = crossingStripeSegments([[0, 0], [16, 0]]);
    expect(segments.length).toBeGreaterThan(15);
    expect(segments.at(-1)![1][0]).toBeGreaterThan(15);
  });
  it("does not lose paint when the same straight crossing has many short segments", () => {
    const plain = crossingStripeSegments([[0, 0], [16, 0]]);
    const split = crossingStripeSegments(Array.from({length: 81}, (_, i) => [i * .2, 0] as Point));
    const paintLength = (segments: [Point, Point][]) => segments.reduce((sum, [a, b]) =>
      sum + Math.hypot(b[0] - a[0], b[1] - a[1]), 0);
    expect(paintLength(split)).toBeCloseTo(paintLength(plain), 8);
  });
});

describe("street light pool", () => {
  it("keeps twilight output and applies distinct night lamp, halo and pool values", () => {
    const group = new THREE.Group(), camera = new THREE.PerspectiveCamera();
    const light = new THREE.PointLight(0xffffff, 0);
    const lens = new THREE.MeshStandardMaterial();
    const haloMaterial = new THREE.SpriteMaterial({ opacity: 0.62 });
    const halo = new THREE.Sprite(haloMaterial);
    const poolGeometry = new THREE.PlaneGeometry();
    const poolMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.48 });
    const pools = new THREE.InstancedMesh(poolGeometry, poolMaterial, 1);
    group.add(light);
    Object.assign(group.userData, {
      streetLampMaterials: [lens], streetLampLights: [light], streetLampHalos: [halo],
      streetLampGroundPools: pools,
      streetLampLocations: [{ x: 0, z: 0, rotation_deg: 0 }],
      streetLampLensOffset: { alongArmM: -3.4, acrossArmM: 0, heightM: 6.6 },
    });
    setCityRoadLighting(group, camera, "twilight");
    expect(light.intensity).toBe(350);
    expect(light.position.toArray()).toEqual([-3.4, 6.6, 0]);
    expect(lens.emissiveIntensity).toBe(2.8);
    expect(halo.material.opacity).toBe(0.62);
    expect(poolMaterial.opacity).toBe(0.48);
    setCityRoadLighting(group, camera, "night");
    expect(light.intensity).toBe(500);
    expect(lens.emissiveIntensity).toBe(3.6);
    expect(halo.material.opacity).toBe(0.76);
    expect(poolMaterial.opacity).toBe(0.62);
    camera.position.x = 1000;
    setCityRoadLighting(group, camera, "night");
    expect(light.intensity).toBe(0);
    expect(light.visible).toBe(true);
    camera.position.x = 0;
    setCityRoadLighting(group, camera, "day");
    expect(light.intensity).toBe(0);
    expect(light.visible).toBe(true);
    expect(pools.visible).toBe(false);
    lens.dispose(); haloMaterial.dispose(); poolMaterial.dispose(); poolGeometry.dispose();
  });
});

describe("street lamp part materials", () => {
  async function lampParts(): Promise<Map<string, { material: THREE.MeshStandardMaterial; box: THREE.Box3;
                                                  downward: number }>> {
    const bytes = readFileSync(resolve(__dirname, "../public/models/incoming/furniture/glb/street_light_8.glb"));
    const data = new ArrayBuffer(bytes.byteLength);
    new Uint8Array(data).set(bytes);
    const scene = (await new GLTFLoader().parseAsync(data, "")).scene;
    scene.updateMatrixWorld(true);
    const parts = new Map<string, { material: THREE.MeshStandardMaterial; box: THREE.Box3; downward: number }>();
    scene.traverse(node => {
      if (!(node instanceof THREE.Mesh) || !(node.material instanceof THREE.MeshStandardMaterial)) return;
      // Primitives share one vertex buffer; only indexed vertices belong to this part.
      const geometry = (node.geometry.index === null ? node.geometry.clone() : node.geometry.toNonIndexed())
        .applyMatrix4(node.matrixWorld);
      geometry.computeBoundingBox();
      const normals = geometry.getAttribute("normal");
      let down = 0;
      for (let index = 0; index < normals.count; index++) down += normals.getY(index) < -0.2 ? 1 : 0;
      const entry = parts.get(node.material.name)
        ?? { material: node.material, box: new THREE.Box3(), downward: 0 };
      entry.box.union(geometry.boundingBox!);
      entry.downward = Math.max(entry.downward, down / normals.count);
      parts.set(node.material.name, entry);
    });
    return parts;
  }

  it("lights only the downward lens under the head and keeps the blank pole plate non-emissive at night", async () => {
    const parts = await lampParts();
    const lens = parts.get("street_light_8SG6")!, plate = parts.get("street_light_8SG1")!;
    expect([...parts.keys()].sort()).toEqual(
      ["street_light_8SG", "street_light_8SG1", "street_light_8SG2", "street_light_8SG6"]);
    // The lens is under the 6 m head and faces down; the plate starts on the pole near mid-height.
    expect(lens.box.min.y).toBeGreaterThan(5.5);
    expect(lens.downward).toBeGreaterThan(0.5);
    expect(plate.box.min.y).toBeLessThan(2.5);
    const lit = [...parts].filter(([name, part]) => prepareStreetLampPartMaterial(name, part.material))
      .map(([, part]) => part.material);
    expect(lit).toEqual([lens.material]);
    const group = new THREE.Group(), pools = new THREE.InstancedMesh(new THREE.PlaneGeometry(),
      new THREE.MeshBasicMaterial(), 1);
    Object.assign(group.userData, { streetLampMaterials: lit, streetLampLights: [], streetLampHalos: [],
      streetLampGroundPools: pools, streetLampLocations: [],
      streetLampLensOffset: { alongArmM: -3.4, acrossArmM: 0, heightM: 6.6 } });
    setCityRoadLighting(group, new THREE.PerspectiveCamera(), "night");
    expect(lens.material.emissiveIntensity).toBe(3.6);
    expect(plate.material).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect(plate.material.emissive.getHex()).toBe(0);
    expect(plate.material.emissiveIntensity).toBe(0);
    expect(plate.material.toneMapped).toBe(true);
    expect(plate.material.color.getHex()).toBe(0x78828a);
    for (const name of ["street_light_8SG", "street_light_8SG2"]) {
      expect(parts.get(name)!.material.emissiveIntensity).toBe(0);
    }
  });
});

describe("street lamp lens position", () => {
  it("follows the instance orientation used for the lamp model", () => {
    const offset = { alongArmM: -3.4, acrossArmM: 0.05, heightM: 6.6 };
    for (const rotation_deg of [0, 37, 90, 180, -125]) {
      const lamp = { x: 12, z: -7, rotation_deg };
      const expected = new THREE.Vector3(offset.alongArmM, 0, offset.acrossArmM)
        .applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(rotation_deg))
        .add(new THREE.Vector3(lamp.x, offset.heightM, lamp.z));
      const head = streetLampHeadPosition(lamp, offset, new THREE.Vector3());
      expect(head.distanceTo(expected)).toBeLessThan(1e-9);
    }
  });

  it("measures the provided lens beyond the former 2.8 m hand-set arm", async () => {
    const bytes = readFileSync(resolve(__dirname, "../public/models/incoming/furniture/glb/street_light_8.glb"));
    const data = new ArrayBuffer(bytes.byteLength);
    new Uint8Array(data).set(bytes);
    const scene = (await new GLTFLoader().parseAsync(data, "")).scene;
    scene.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(scene), lens = new THREE.Box3();
    scene.traverse(node => {
      if (!(node instanceof THREE.Mesh) || node.material.name !== "street_light_8SG6") return;
      const geometry = node.geometry.toNonIndexed().applyMatrix4(node.matrixWorld);
      geometry.computeBoundingBox();
      lens.union(geometry.boundingBox!);
    });
    const scale = 7.2 / (bounds.max.y - bounds.min.y);
    const center = lens.getCenter(new THREE.Vector3());
    const alongArmM = (center.x - (bounds.max.x - 0.2)) * scale;
    expect(Math.abs(alongArmM)).toBeGreaterThan(3);
    expect(Math.abs(alongArmM)).toBeLessThan(4);
    expect(Math.abs(center.z * scale)).toBeLessThan(0.3);
    expect(.225 + (lens.min.y - bounds.min.y) * scale).toBeGreaterThan(6);
  });
});

describe("road surface wetness", () => {
  it("shares one weather uniform while applying bounded asphalt, paving and crossing responses", () => {
    const asphalt = new THREE.MeshStandardMaterial();
    const paving = new THREE.MeshStandardMaterial();
    const crossing = new THREE.MeshStandardMaterial();
    const facade = new THREE.MeshStandardMaterial();
    const wetness = createSurfaceWetnessUniforms();
    expect(applyCityRoadSurfaceWetness({ asphalt: [asphalt], paving: [paving], crossings: [crossing] }, wetness))
      .toBe(3);
    const fragment = (material: THREE.MeshStandardMaterial): string => {
      const shader = { uniforms: {} as Record<string, THREE.IUniform>,
        vertexShader: "#include <common>\n#include <beginnormal_vertex>",
        fragmentShader: "#include <common>\n#include <map_fragment>\n#include <roughnessmap_fragment>" };
      material.onBeforeCompile(shader as never, {} as never);
      expect(shader.uniforms.uSurfaceWetness).toBe(wetness.uWetness);
      return shader.fragmentShader;
    };
    expect(fragment(asphalt)).toContain(CITY_ROAD_WETNESS_LIMITS.asphalt.maxDarkening.toFixed(6));
    expect(fragment(asphalt)).toContain("0.180000");
    expect(fragment(paving)).toContain("0.220000"); expect(fragment(paving)).toContain("0.480000");
    expect(fragment(crossing)).toContain("0.120000"); expect(fragment(crossing)).toContain("0.380000");
    expect(facade.userData.surfaceWetness).toBeUndefined();
    expect(asphalt.userData.cityRoadWetnessClass).toBe("asphalt");
    expect(paving.userData.cityRoadWetnessClass).toBe("paving");
    expect(crossing.userData.cityRoadWetnessClass).toBe("crossings");
    asphalt.dispose(); paving.dispose(); crossing.dispose(); facade.dispose();
  });

  it("rejects an ambiguous material class or an invalid shared value", () => {
    const shared = new THREE.MeshStandardMaterial();
    expect(() => applyCityRoadSurfaceWetness({ asphalt: [shared], paving: [shared], crossings: [] },
      createSurfaceWetnessUniforms())).toThrow(/multiple surface classes/);
    const invalid = createSurfaceWetnessUniforms(); invalid.uWetness.value = 1.1;
    expect(() => applyCityRoadSurfaceWetness({ asphalt: [], paving: [], crossings: [] }, invalid))
      .toThrow(RangeError);
    shared.dispose();
  });
});

function signedArea(points: readonly Point[]): number {
  return points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]!;
    return sum + point[0] * next[1] - next[0] * point[1];
  }, 0) / 2;
}

function triangleArea(points: readonly Point[], indices: readonly number[]): number {
  let area = 0;
  for (let index = 0; index < indices.length; index += 3) {
    const p = points[indices[index]!]!, q = points[indices[index + 1]!]!, r = points[indices[index + 2]!]!;
    const signed = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    expect(signed).toBeLessThan(0);
    area -= signed / 2;
  }
  return area;
}

function validRoadPreviewV2(): Record<string, unknown> {
  const ring = [[0, 0], [10, 0], [10, 10], [0, 10]];
  const polygon = { outline: ring, holes: [] };
  return {
    schema_version: "aero-bench.city-road-preview/v2",
    source_network_sha256: "a".repeat(64),
    mesh_pack_source_sha256: "b".repeat(64),
    building_placement_sha256: "c".repeat(64),
    displayed_surface_sha256: "d".repeat(64),
    road_surface_materials: {
      asphalt: { texture: "/asphalt.png", source: "pinned asphalt triangles" },
      concrete: { texture: "/concrete.jpg", source: "tagged concrete road triangles",
        texture_asset: { url: `/osm2world/packs/test/assets/${"e".repeat(64)}`,
          sha256: "e".repeat(64), size_bytes: 1024 },
        uv_repeat_m: [1.2, 0.6],
        source_way_count: 1, source_way_triangles: 1, source_junction_count: 0,
        source_junction_triangles: 0, source_triangle_count: 1, rendered_area_m2: 50 },
    },
    concrete_roadbed_area_m2: 50,
    roadbed_area_m2: 100,
    walkbed_area_m2: 20,
    internal_lane_count: 0,
    roadbed: [polygon],
    concrete_roadbed: [polygon],
    walkbed: [polygon],
    curb_edges: [],
    signal_road_coverage: {},
    lanes: [{ id: "w123#0_0", width: 3.2, kind: "motor", shape: [[0, 0], [10, 0]] }],
    marking_widths_m: { lane_edge: 0.14, lane_divider: 0.13, derived_direction_guide: 0.15 },
    direction_guides: [{ id: "direction-guide:w123:0", source_way_id: "w123",
      edge_ids: ["w123#0", "-w123#0"], marking: "derived_direction_guide",
      source_kind: "sumo-lane-topology-not-surveyed-marking", width_m: 0.15,
      shape: [[0, 0], [10, 0]] }],
    junctions: [],
    crossings: [],
    street_lamps: [],
    street_layout: { source_kind: "sumo-topology-with-explicit-derived-urban-design",
      walking_area_source_count: 0, walking_area_count: 0, omitted_degenerate_walking_areas: [],
      sidewalk_provenance: { source_kind: "derived_osm_eligible_corridor_visual_geometry", sumo_lane_topology_modified: false, surface_precision_normalized: true,
        sidewalk_kind: "derived_paved_sidewalk_not_surveyed", median_kind: "derived_paved_narrow_enclosed_strip_not_surveyed",
        crossing_cuts_affect: "curb_lines_only" },
      sidewalk_stats: { derived_walkbed_area_m2: 0, median_beds_area_m2: 0, sidewalk_width_m: 2.2,
        building_clearance_m: .15, vehicle_clearance_m: .15, precision_grid_m: .001,
        effective_building_clearance_m: .153, effective_vehicle_clearance_m: .153,
        raw_roadbed_area_m2: 100, roadbed_normalization_symmetric_difference_m2: 0,
        precision_clearance_margin_m: .003, road_walk_separation_m: .003,
        normalized_roadbed_area_m2: 100, normalized_source_walkbed_area_m2: 20 },
      road_height_m: .075, sidewalk_height_m: .225, pavement_edges: [], derived_walkbed: [], median_beds: [],
      markings: [], arrows: [] },
  };
}

describe("SUMO road preview v2 contract", () => {
  it("places right edges and left dividers on the correct side in east/up/south coordinates", () => {
    expect(laneOffsets([[0, 0], [20, 0]], 3.2)).toEqual({
      left: [[0, -1.6], [20, -1.6]], right: [[0, 1.6], [20, 1.6]],
    });
    expect(laneOffsets([[20, 0], [0, 0]], 3.2)).toEqual({
      left: [[20, 1.6], [0, 1.6]], right: [[20, -1.6], [0, -1.6]],
    });
    expect(laneOffsets([[0, 0], [0, 20]], 3.2)).toEqual({
      left: [[1.6, 0], [1.6, 20]], right: [[-1.6, 0], [-1.6, 20]],
    });
  });

  it("binds texture bytes and URL to the verified pack material, rejecting valid-but-wrong assets", () => {
    const material = validateCityRoadPreviewPayload(validRoadPreviewV2()).road_surface_materials.concrete;
    const pack = {
      textures: { [material.texture]: { sha256: "e".repeat(64), size_bytes: 1024 } },
      batches: [{ layer: "roads", material: { base_color_texture: material.texture } }],
    } as unknown as Parameters<typeof verifiedConcreteAsset>[1];
    expect(verifiedConcreteAsset(material, pack, "/osm2world/packs/test/")).toEqual(material.texture_asset);
    for (const mutation of [
      { sha256: "f".repeat(64), url: `/osm2world/packs/test/assets/${"f".repeat(64)}` },
      { size_bytes: 1025 },
      { url: `/osm2world/packs/other/assets/${"e".repeat(64)}` },
    ]) expect(() => verifiedConcreteAsset({ ...material,
      texture_asset: { ...material.texture_asset!, ...mutation } }, pack,
      "/osm2world/packs/test/")).toThrow(/verified mesh-pack/);
  });

  it("maps concrete atlas UVs to its declared 1.2 m by 0.6 m repeat", () => {
    expect(meterTextureUvs([0, 0, 6 / 5, 3 / 5], [1.2, 0.6])).toEqual([0, 0, 5, 5]);
  });

  it("requires concrete footprints and physically sized SUMO-derived guide lines", () => {
    const result = validateCityRoadPreviewPayload(validRoadPreviewV2());
    expect(result.schema_version).toBe("aero-bench.city-road-preview/v2");
    expect(result.direction_guides[0]?.marking).toBe("derived_direction_guide");
    expect(result.direction_guides[0]?.source_kind).toBe("sumo-lane-topology-not-surveyed-marking");
    expect(result.road_surface_materials.concrete.source_triangle_count).toBe(1);
  });

  it("rejects stale v1 assets instead of silently dropping concrete and direction guides", () => {
    expect(() => validateCityRoadPreviewPayload({ ...validRoadPreviewV2(),
      schema_version: "aero-bench.city-road-preview/v1" })).toThrow(/current v2/);
  });

  it("accepts a canonical v3 road bound to a source context with physical clearance PASS", () => {
    const { building_placement_sha256: _placement, ...rest } = validRoadPreviewV2();
    const canonical = { ...rest, schema_version: "aero-bench.city-road-preview/v3",
      source_context: { scene_id: "unit" }, physical_clearance: { status: "PASS" } };
    expect(validateCanonicalCityRoadPayload(canonical).source_context).toEqual({ scene_id: "unit" });
    expect(() => validateCanonicalCityRoadPayload({ ...canonical, physical_clearance: { status: "BLOCKED" } }))
      .toThrow(/source context or physical clearance PASS/);
    expect(() => validateCanonicalCityRoadPayload({ ...canonical, building_placement_sha256: "a".repeat(64) }))
      .toThrow(/source context or physical clearance PASS/);
    expect(() => validateCanonicalCityRoadPayload(validRoadPreviewV2())).toThrow(/v3 road-surface/);
    expect(() => validateCityRoadPreviewPayload(canonical)).toThrow(/current v2/);
  });

  it("accepts this selection's static road geometry without traffic frames or a fixed curb count", () => {
    const payload: Record<string, unknown> = { ...validRoadPreviewV2(), schema_version: "aero-bench.city-static-road/v1",
      source_osm_sha256: "1".repeat(64), mesh_pack_manifest_sha256: "2".repeat(64),
      signal_inventory_sha256: "3".repeat(64) };
    const materials = payload.road_surface_materials as Record<string, Record<string, unknown>>;
    payload.road_surface_materials = { ...materials, concrete: { ...materials.concrete,
      texture_asset: { url: `/authoring/v1/scenes/${"4".repeat(64)}/pack/assets/${"e".repeat(64)}`,
        sha256: "e".repeat(64), size_bytes: 1024 } } };
    expect(validateStaticCityRoadPayload(payload).curb_edges).toHaveLength(0);
    expect(() => validateStaticCityRoadPayload({ ...payload,
      traffic_recorded_building_placement_sha256: "5".repeat(64) })).toThrow(/recorded traffic/);
    expect(() => validateStaticCityRoadPayload({ ...payload, road_surface_materials: {
      ...materials, concrete: { ...materials.concrete,
        texture_asset: { url: `/osm2world/packs/old/assets/${"e".repeat(64)}`,
          sha256: "e".repeat(64), size_bytes: 1024 } },
    } })).toThrow(/concrete/);
  });

  it("rejects separators wider than the road marking contract", () => {
    const payload = validRoadPreviewV2();
    payload.direction_guides = [{ ...(payload.direction_guides as Record<string, unknown>[])[0],
      width_m: 0.3 }];
    expect(() => validateCityRoadPreviewPayload(payload)).toThrow(/invalid derived direction guide/);
  });

  it("rejects malformed curb segments instead of imposing a region-specific curb count", () => {
    for (const curb_edges of [[[0, 0]], [[0, 0], [Number.NaN, 1]], [[0, 0], [1]]]) {
      expect(() => validateCityRoadPreviewPayload({ ...validRoadPreviewV2(), curb_edges: [curb_edges] }))
        .toThrow(/required v2 road geometry/);
    }
  });

  it.each([
    ["default-scene-v1.json", 622],
    ["jingan-engineering-preview-v1.json", 57],
  ])("accepts the verified geometry count of published %s", async (filename, curbCount) => {
    const scene = JSON.parse(readFileSync(resolve("public/city-presentation", filename), "utf8"));
    const data = validateCanonicalCityRoadPayload(JSON.parse(readFileSync(
      resolve("public", scene.road_assets.road.url.slice(1)), "utf8")));
    const traffic = JSON.parse(readFileSync(resolve("public", scene.road_assets.traffic.url.slice(1)), "utf8"));
    const identity = { sourceContext: data.source_context!, streetLampIndices: new Set<number>() };
    const verify = (overrides: { network?: string; source?: string; surface?: string;
        context?: Readonly<Record<string, unknown>>; traffic?: typeof traffic } = {}) => verifyCityRoadIdentity(
      data, overrides.network ?? scene.road_assets.source_network_sha256,
      overrides.source ?? scene.road_assets.mesh_pack_source_sha256,
      { ...identity, sourceContext: overrides.context ?? identity.sourceContext },
      overrides.surface ?? scene.road_assets.displayed_surface_sha256, overrides.traffic ?? traffic);
    expect(data.curb_edges).toHaveLength(curbCount);
    expect(() => verify()).not.toThrow();
    expect(await displaySurfaceSha256(data.roadbed, data.walkbed)).toBe(scene.road_assets.displayed_surface_sha256);
    for (const overrides of [
      { network: "0".repeat(64) }, { source: "0".repeat(64) }, { surface: "0".repeat(64) },
      { context: { ...identity.sourceContext, scene_id: "another-region" } },
      { traffic: { ...traffic, source_network_sha256: "0".repeat(64) } },
    ]) expect(() => verify(overrides)).toThrow(/displayed network and city source/);
  });
});

describe("SUMO road junction triangulation", () => {
  it.each([
    [[0, 0], [4, 0], [4, 4], [0, 4]],
    [[0, 4], [4, 4], [4, 0], [0, 0]],
    [[-78.86, 41.72], [-65.14, 63.42], [-76.25, 60.29], [-80.4, 54.27],
      [-78.84, 52.34], [-78.86, 50.2], [-79.66, 47.94], [-80.46, 45.7], [-80.46, 43.59]],
  ] as Point[][])("produces upward faces covering the source polygon", (...points) => {
    const indices = triangulateUpward(points);
    expect(indices.length).toBeGreaterThan(0);
    expect(triangleArea(points, indices)).toBeCloseTo(Math.abs(signedArea(points)), 6);
  });

  it("triangulates a roadbed with a city block hole", () => {
    const outline: Point[] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    const hole: Point[] = [[3, 3], [7, 3], [7, 7], [3, 7]];
    const indices = triangulateUpward(outline, [hole]);
    expect(triangleArea([...outline, ...hole], indices)).toBeCloseTo(84, 6);
  });

  it("covers the generated road and clipped walking surfaces", async () => {
    const scene = JSON.parse(readFileSync(resolve("public/city-presentation/building-render-scene-v1.json"),
                                          "utf8")) as { road_assets: { road: { url: string } } };
    const data = JSON.parse(readFileSync(resolve("public", scene.road_assets.road.url.slice(1)),
                                         "utf8")) as {
      roadbed_area_m2: number;
      displayed_surface_sha256: string;
      roadbed: { outline: Point[]; holes: Point[][] }[];
      walkbed_area_m2: number;
      walkbed: { outline: Point[]; holes: Point[][] }[];
    };
    for (const kind of ["roadbed", "walkbed"] as const) {
      let renderedArea = 0;
      for (const polygon of data[kind]) {
        const points = [...polygon.outline, ...polygon.holes.flat()];
        renderedArea += triangleArea(points, triangulateUpward(polygon.outline, polygon.holes));
      }
      expect(Math.abs(renderedArea - data[`${kind}_area_m2`]) / data[`${kind}_area_m2`]).toBeLessThan(0.001);
    }
    expect(await displaySurfaceSha256(data.roadbed, data.walkbed)).toBe(data.displayed_surface_sha256);
    const changedRoadbed = data.roadbed.map((entry, index) => index !== 0 ? entry : ({
      ...entry,
      outline: entry.outline.map((point, pointIndex) => pointIndex !== 0
        ? point : [point[0] + 0.25, point[1]] as Point),
    }));
    expect(await displaySurfaceSha256(changedRoadbed, data.walkbed)).not.toBe(data.displayed_surface_sha256);
  });
});
