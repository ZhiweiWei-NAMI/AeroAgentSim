import { describe, expect, it, vi } from "vitest";
import vm from "node:vm";
import {
  CITY_GROUND_COVER_KINDS,
  createCityGroundCoverLayer,
  groundCoverKindFromTags,
  measureCityGroundCoverDrawnSet,
  parseCityGroundCovers,
  planCityGroundCovers,
  triangulateEnvironmentPolygon,
  type CityGroundCover,
  type CityGroundCoverKind,
} from "./city-ground-cover";
import { CITY_ENVIRONMENT_DEFAULTS, createCityEnvironment, generateCityEnvironment,
  type EnvironmentPoint, type EnvironmentPolygon } from "./city-environment";
import * as THREE from "three";
import { groundMaterialInputFromCover } from "./city-ground-material-rules";
import { assignCityGroundMaterial, createTerrainSurfaceKit } from "./city-terrain-surfaces";
import { createSurfaceWetnessUniforms } from "./city-surface-wetness";
import { createWaterSurfaceUniforms } from "./city-water-surface";
import { ALL_TEXTURE_SET_IDS, fakeTerrainTextureSets, terrainSurfacesForPlan }
  from "./testing/terrain-surface-fixture";

function coverSurfaces(covers: readonly CityGroundCover[]) {
  const assignments = new Map(covers.map(item => [item.id, assignCityGroundMaterial(groundMaterialInputFromCover(item))]));
  return { assignments, kit: createTerrainSurfaceKit([...assignments.values()],
    fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS), createSurfaceWetnessUniforms(), createWaterSurfaceUniforms()) };
}

const osmSha256 = "a".repeat(64), objectsSha256 = "b".repeat(64);
const origin = { latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50 };

function rectangle(minX: number, minZ: number, maxX: number, maxZ: number): EnvironmentPoint[] {
  return [[minX, minZ], [maxX, minZ], [maxX, maxZ], [minX, maxZ], [minX, minZ]];
}

function area(polygon: EnvironmentPolygon): number {
  const ringArea = (ring: readonly EnvironmentPoint[]): number => Math.abs(ring.slice(0, -1).reduce((sum, point, i) => {
    const next = ring[(i + 1) % (ring.length - 1)]!;
    return sum + point[0] * next[1] - next[0] * point[1];
  }, 0) / 2);
  return ringArea(polygon.outline) - polygon.holes.reduce((sum, hole) => sum + ringArea(hole), 0);
}

function cover(index: number, kind: CityGroundCoverKind, tags: Record<string, string>,
    outline: readonly EnvironmentPoint[] = rectangle(0, 0, 10, 10),
    holes: readonly (readonly EnvironmentPoint[])[] = []): CityGroundCover {
  return { id: `osm:way:${index}:0`, kind, surface: tags.surface ?? null, outline, holes,
    provenance: { kind: "osm", sourceSha256: osmSha256, elementType: "way", elementId: String(index),
      sourceElementType: "way", sourceElementId: String(index), tags } };
}

function source(covers: readonly CityGroundCover[]): unknown {
  const counts: Partial<Record<CityGroundCoverKind, number>> = {};
  const areas: Partial<Record<CityGroundCoverKind, number>> = {};
  for (const item of covers) {
    counts[item.kind] = (counts[item.kind] ?? 0) + 1;
    areas[item.kind] = Number(((areas[item.kind] ?? 0) + area(item)).toFixed(6));
  }
  return { schemaVersion: "aero-bench.city-environment-source/v1",
    coordinateFrame: "x-east,y-up,z-south-meters",
    source: { osmSha256, objectsSha256, projection: "WGS84->ECEF->ENU", origin },
    groundCovers: covers,
    inspection: { groundCoverCount: covers.length, groundCoverCountsByKind: counts,
      groundCoverSourceAreaM2ByKind: areas } };
}

const expected = { osmSha256, objectsSha256, origin };
const outside = (x: number): EnvironmentPolygon => ({ outline: rectangle(x, x, x + 1, x + 1), holes: [] });

describe("city ground-cover source contract", () => {
  it("accepts each supported source class and preserves explicit tags and IDs", () => {
    const definitions: readonly [CityGroundCoverKind, Record<string, string>][] = [
      ["pitch", { leisure: "pitch" }], ["construction", { landuse: "construction" }],
      ["brownfield", { landuse: "brownfield" }], ["parking", { amenity: "parking" }],
      ["pedestrian_area", { highway: "pedestrian" }], ["square", { place: "square" }],
      ["water", { natural: "water" }], ["explicit_surface", { surface: "compacted" }],
    ];
    const parsed = parseCityGroundCovers(source(definitions.map(([kind, tags], index) =>
      cover(index + 1, kind, tags, rectangle(index * 20, 0, index * 20 + 10, 10)))), expected);
    expect(parsed.map(item => item.kind)).toEqual(CITY_GROUND_COVER_KINDS);
    expect(parsed[7]!.surface).toBe("compacted");
    expect(parsed[7]!.provenance.tags).toEqual({ surface: "compacted" });
  });

  it("rejects zoning, forged classes, authority drift, duplicate IDs, and malformed holes", () => {
    expect(groundCoverKindFromTags({ landuse: "residential", surface: "asphalt" })).toBeNull();
    expect(groundCoverKindFromTags({ landuse: "commercial" })).toBeNull();
    expect(groundCoverKindFromTags({ landuse: "retail" })).toBeNull();
    const forged = cover(1, "parking", { landuse: "residential" });
    expect(() => parseCityGroundCovers(source([forged]), expected)).toThrow(/tags disagree/);
    expect(() => parseCityGroundCovers(source([cover(1, "construction", { landuse: "construction" })]),
      { ...expected, osmSha256: "c".repeat(64) })).toThrow(/authority mismatch/);
    const duplicate = cover(1, "construction", { landuse: "construction" });
    expect(() => parseCityGroundCovers(source([duplicate, duplicate]), expected)).toThrow(/identity/);
    const badHole = cover(1, "construction", { landuse: "construction" }, rectangle(0, 0, 10, 10),
      [rectangle(20, 20, 21, 21)]);
    expect(() => parseCityGroundCovers(source([badHole]), expected)).toThrow(/polygon is invalid/);
  });

  it("triangulates a polygon with a hole without filling the hole", () => {
    const polygon = { outline: rectangle(0, 0, 10, 10), holes: [rectangle(3, 3, 7, 7)] };
    const triangles = triangulateEnvironmentPolygon(polygon);
    const triangleArea = triangles.reduce((sum, triangle) => sum + Math.abs(
      (triangle[0]![0] * (triangle[1]![1] - triangle[2]![1])
        + triangle[1]![0] * (triangle[2]![1] - triangle[0]![1])
        + triangle[2]![0] * (triangle[0]![1] - triangle[1]![1])) / 2), 0);
    expect(triangleArea).toBeCloseTo(84, 8);
  });
});

describe("city ground-cover clipping and measurement", () => {
  it("clips against verified roadbed, walkbed, and building footprints", () => {
    const parsed = parseCityGroundCovers(source([
      cover(1, "construction", { landuse: "construction" }, rectangle(0, 0, 10, 10)),
    ]), expected);
    const plan = planCityGroundCovers(parsed, { geometryId: "verified-road-v3", roadGeometry: "published",
      roadbed: [{ outline: rectangle(0, 0, 2, 10), holes: [] }],
      walkbed: [{ outline: rectangle(2, 0, 4, 10), holes: [] }],
      buildings: [{ outline: rectangle(4, 0, 6, 10), holes: [] }] });
    expect(plan.covers[0]!.sourceAreaM2).toBeCloseTo(100, 8);
    expect(plan.covers[0]!.drawnAreaM2).toBeCloseTo(40, 7);
    expect(plan.covers[0]!.removedAreaM2).toBeCloseTo(60, 7);
  });

  it("does not clip the hole in a building footprint", () => {
    const parsed = parseCityGroundCovers(source([
      cover(1, "brownfield", { landuse: "brownfield" }, rectangle(0, 0, 10, 10)),
    ]), expected);
    const plan = planCityGroundCovers(parsed, { geometryId: "building-with-courtyard", roadGeometry: "published",
      roadbed: [outside(20)], walkbed: [outside(22)],
      buildings: [{ outline: rectangle(2, 2, 8, 8), holes: [rectangle(4, 4, 6, 6)] }] });
    expect(plan.covers[0]!.drawnAreaM2).toBeCloseTo(68, 7);
    expect(plan.covers[0]!.removedAreaM2).toBeCloseTo(32, 7);
  });

  it("measures the instantiated drawn set and reports a real omission", () => {
    const parsed = parseCityGroundCovers(source([
      cover(1, "parking", { amenity: "parking" }),
      cover(2, "pitch", { leisure: "pitch" }, rectangle(20, 0, 30, 10)),
    ]), expected);
    const plan = planCityGroundCovers(parsed, { geometryId: "drawn-set", roadGeometry: "published",
      roadbed: [outside(40)], walkbed: [outside(42)], buildings: [outside(44)] });
    const { assignments, kit } = coverSurfaces(parsed);
    const renderer = createCityGroundCoverLayer(plan, assignments, kit);
    expect(renderer.drawnSet).toMatchObject({ status: "pass", omission_count: 0,
      parser_accepted_ids: ["osm:way:1:0", "osm:way:2:0"] });
    const meshes = renderer.group.children as THREE.Mesh[];
    expect(meshes.map(mesh => [mesh.userData.groundCoverId, mesh.userData.groundMaterial.preset,
      (mesh.material as THREE.Material).userData.terrainPreset])).toEqual([
      ["osm:way:1:0", "parking-generic", "parking-generic"], ["osm:way:2:0", "pitch-turf-unknown", "pitch-turf-unknown"]]);
    renderer.group.remove(renderer.group.children[0]!);
    expect(measureCityGroundCoverDrawnSet(plan, renderer.group)).toMatchObject({
      status: "fail", omission_count: 1, omitted_ids: ["osm:way:1:0"],
    });
    renderer.dispose(); kit.dispose();
  });

  it("shares one kit material per preset, keeps water unshadowed and never disposes kit materials", () => {
    const parsed = parseCityGroundCovers(source([
      cover(1, "parking", { amenity: "parking" }),
      cover(2, "parking", { amenity: "parking" }, rectangle(20, 0, 30, 10)),
      cover(3, "water", { natural: "water", water: "pond" }, rectangle(40, 0, 50, 10)),
      cover(4, "explicit_surface", { surface: "metal_grid" }, rectangle(60, 0, 70, 10)),
    ]), expected);
    const plan = planCityGroundCovers(parsed, { geometryId: "shared-materials", roadGeometry: "published",
      roadbed: [outside(80)], walkbed: [outside(82)], buildings: [outside(84)] });
    const { assignments, kit } = coverSurfaces(parsed);
    const renderer = createCityGroundCoverLayer(plan, assignments, kit);
    const [first, second, water, unknown] = renderer.group.children as THREE.Mesh[];
    expect(first!.material).toBe(second!.material);
    expect(water!.material).toBeInstanceOf(THREE.MeshPhysicalMaterial);
    expect(water!.receiveShadow).toBe(false); expect(first!.receiveShadow).toBe(true);
    expect(water!.geometry.getAttribute("uv")).toBeUndefined();
    expect(unknown!.userData.groundMaterial).toMatchObject({ family: "unclassified", reason: "unrecognized-surface:metal_grid" });
    expect((unknown!.material as THREE.Material).userData.terrainPreset).toBe("unclassified");
    const materialDisposal = (first!.material as THREE.Material).dispose = vi.fn();
    renderer.dispose(); expect(materialDisposal).not.toHaveBeenCalled();
    kit.dispose(); expect(materialDisposal).toHaveBeenCalledOnce();
  });

  it("rejects a drawable cover without a material assignment", () => {
    const parsed = parseCityGroundCovers(source([cover(1, "parking", { amenity: "parking" })]), expected);
    const plan = planCityGroundCovers(parsed, { geometryId: "unassigned", roadGeometry: "published",
      roadbed: [outside(40)], walkbed: [outside(42)], buildings: [outside(44)] });
    const { kit } = coverSurfaces(parsed);
    expect(() => createCityGroundCoverLayer(plan, new Map(), kit)).toThrow(/no material assignment: osm:way:1:0/);
    kit.dispose();
  });

  it("disposes every geometry built so far when a later cover has no assignment", () => {
    const parsed = parseCityGroundCovers(source([
      cover(1, "parking", { amenity: "parking" }),
      cover(2, "pitch", { leisure: "pitch" }, rectangle(20, 0, 30, 10)),
    ]), expected);
    const plan = planCityGroundCovers(parsed, { geometryId: "partial-failure", roadGeometry: "published",
      roadbed: [outside(40)], walkbed: [outside(42)], buildings: [outside(44)] });
    const { assignments, kit } = coverSurfaces(parsed);
    const disposals = new Map<THREE.BufferGeometry, number>();
    const original = THREE.BufferGeometry.prototype.setAttribute;
    vi.spyOn(THREE.BufferGeometry.prototype, "setAttribute").mockImplementation(function mock(this: THREE.BufferGeometry,
        name: string | number | symbol, attribute: THREE.BufferAttribute) {
      const result = original.call(this, name as string, attribute);
      if (name === "position" && !disposals.has(this)) {
        disposals.set(this, 0);
        this.addEventListener("dispose", () => disposals.set(this, (disposals.get(this) ?? 0) + 1));
      }
      return result;
    });
    try {
      expect(() => createCityGroundCoverLayer(plan,
        new Map([[parsed[0]!.id, assignments.get(parsed[0]!.id)!]]), kit))
        .toThrow(new RegExp(`no material assignment: ${parsed[1]!.id}`));
    } finally {
      vi.restoreAllMocks();
    }
    expect([...disposals.values()]).toEqual([1]);
    kit.dispose();
  });

  it("requires the published geometry authority", () => {
    const item = cover(1, "parking", { amenity: "parking" });
    expect(() => planCityGroundCovers([item], { geometryId: "", roadGeometry: "published",
      roadbed: [], walkbed: [], buildings: [] })).toThrow(/require published/);
  });
});

describe("live audit observer against the real environment renderer", () => {
  interface AuditPatch { id: string; meshUuid: string; insideDrawRange: boolean; }
  interface AuditView { renderCalls: number; groundMeshDrawCounts: Record<string, number>;
    drawn: string[]; drawCountsById: Record<string, number>; omittedInFrustum: string[];
    expectedInFrustum: string[]; outsideFrustum: string[]; drawnOutsideFrustum: string[]; }
  interface AuditGrass { patches: AuditPatch[]; woodlandFloorPatches: AuditPatch[];
    planPatchCount: number; woodlandFloorPlanPatchCount: number;
    grassFullyExcludedCount: number; treePlacementsByGreenId: Record<string, number>; }
  interface AuditApi { renderAndObserve(): AuditView; grass(): AuditGrass;
    inventory(): unknown; plan(): unknown; extent(): unknown; exportedDrawnSet(): unknown;
    freeze(view: unknown): unknown; setTint(enabled: boolean): void; }

  const readObserver = () => {
    const { readFileSync } = require("node:fs") as typeof import("node:fs");
    const ts = require("typescript") as typeof import("typescript");
    const sourceText = readFileSync("scripts/audit-city-ground-cover-live.mjs", "utf8");
    const ast = ts.createSourceFile("audit.mjs", sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    const declaration = ast.statements.find(statement =>
      ts.isFunctionDeclaration(statement) && statement.name?.text === "installObserver") as
      unknown as { getText(sourceFile: unknown): string } | undefined;
    if (declaration === undefined) throw new Error("installObserver not found in the audit script");
    return declaration.getText(ast);
  };

  function environmentFixture(tags: Record<string, string>) {
    const sha = "a".repeat(64);
    const point = (x: number, z: number): EnvironmentPoint => [x, z];
    const rect = (x1: number, z1: number, x2: number, z2: number) =>
      ({ outline: [point(x1, z1), point(x2, z1), point(x2, z2), point(x1, z2)], holes: [] });
    const greens = [{ ...rect(0, 0, 6, 6), id: "green-a",
      provenance: { kind: "osm" as const, sourceSha256: sha, elementType: "way" as const,
        elementId: "1", sourceElementId: "1", sourceElementType: "way" as const, tags } }];
    const plan = generateCityEnvironment({
      geometryId: "observer-fixture", roadGeometry: "published",
      roadbed: [rect(20, 0, 22, 10)], walkbed: [rect(22, 0, 24, 10)],
      buildings: [{ ...rect(26, 0, 28, 10), id: "building" }], crossings: [], junctions: [],
      lamps: [], signals: [], extent: rect(-10, -10, 40, 30), sourceTrees: [], greens,
    }, { ...CITY_ENVIRONMENT_DEFAULTS, greenTrees: false });
    const surfaces = terrainSurfacesForPlan(plan);
    const renderer = createCityEnvironment(plan, { trees: new Map(), dispose() {} }, surfaces);
    const scene = new THREE.Scene();
    scene.add(renderer.group);
    const camera = new THREE.PerspectiveCamera();
    camera.updateMatrixWorld();
    return { plan, renderer, surfaces, scene, camera };
  }

  /** Installs the extracted observer against a minimal viewer-map stand-in. The stand-in's
   * `renderPreviewFrame` plays the real render pass: it invokes `onAfterRender` for the
   * ground meshes whose green ids are selected, exactly as the live audit's frame does. */
  function runObserver(fixture: ReturnType<typeof environmentFixture>, renderIds: string[]): AuditApi {
    const observer = readObserver();
    const ground = fixture.renderer.group.children.filter(
      (child): child is THREE.Mesh => Array.isArray(child.userData.greenIds));
    const map: Record<string, unknown> = { __aeroVisualMap: {
      scene: fixture.scene, camera: fixture.camera,
      controls: { target: new THREE.Vector3() },
      cityVegetationLayer: { group: fixture.renderer.group },
      focusSunShadow() {},
      renderPreviewFrame() {
        for (const mesh of ground) {
          if ((mesh.userData.greenIds as string[]).some(id => renderIds.includes(id))) {
            mesh.onAfterRender(undefined as unknown as THREE.WebGLRenderer, fixture.scene,
              fixture.camera, mesh.geometry, mesh.material as THREE.Material,
              null as unknown as THREE.Group);
          }
        }
      },
      renderer: { info: { render: { calls: 1 } }, getContext: () => ({ finish() {} }) },
      root: { querySelector: () => ({ hidden: true }) },
    } };
    vm.runInNewContext(`${observer}\ninstallObserver();`, { window: map, console });
    return map.__aeroGroundCoverAudit as AuditApi;
  }

  it("A1: ground draw keys stay out of the cover drawn set and are counted per mesh", () => {
    const fixture = environmentFixture({ landuse: "grass" });
    try {
      const audit = runObserver(fixture, ["green-a"]);
      const view = audit.renderAndObserve();
      // The lawn mesh was drawn, but its internal key must not appear in the cover drawn set.
      expect(view.drawn).toEqual([]);
      expect(Object.values(view.groundMeshDrawCounts)).toEqual([1]);
      expect(Object.keys(view.drawCountsById)).toEqual([]);
    } finally {
      fixture.renderer.dispose();
      fixture.surfaces.kit.dispose();
    }
  });

  it("A2: an existing but never drawn woodland-floor patch is exposed for the omission check", () => {
    const fixture = environmentFixture({ natural: "wood" });
    try {
      expect(fixture.plan.grass.map(patch => patch.id)).toEqual([]);
      expect(fixture.plan.woodlandFloor.map(patch => patch.id)).toEqual(["green-a"]);
      const drawnAudit = runObserver(fixture, ["green-a"]);
      const drawn = drawnAudit.renderAndObserve();
      expect(Object.values(drawn.groundMeshDrawCounts)).toEqual([1]);
      // Render nothing: the floor patch exists in the plan but its mesh is never drawn in
      // any audited view, so the classification must report it as an omission.
      const undrawnAudit = runObserver(fixture, []);
      const undrawn = undrawnAudit.renderAndObserve();
      expect(undrawn.groundMeshDrawCounts).toEqual({});
      expect(undrawn.drawn).toEqual([]);
      const inspected = undrawnAudit.grass();
      expect(inspected.woodlandFloorPlanPatchCount).toBe(1);
      expect(inspected.woodlandFloorPatches.map(patch => patch.id)).toEqual(["green-a"]);
    } finally {
      fixture.renderer.dispose();
      fixture.surfaces.kit.dispose();
    }
  });

  it("A3: observer classifies merged meshes by plan role under preset/role mismatch", () => {
    // `natural=wood` with an explicit asphalt surface: the planner files the patch under the
    // woodland floor while the mesh carries the asphalt preset; a `leisure=park` green
    // carrying the woodland-floor preset stays a lawn patch. Under the old preset-based
    // classification the lawn/floor counts were wrong in both directions and threw.
    const tagSets: Record<string, string>[] = [
      { natural: "wood", surface: "asphalt" }, { leisure: "park", landuse: "forest" }];
    for (const tags of tagSets) {
      const fixture = environmentFixture(tags);
      try {
        const wood = tags.natural === "wood";
        expect(fixture.plan.grass.map(patch => patch.id)).toEqual(wood ? [] : ["green-a"]);
        expect(fixture.plan.woodlandFloor.map(patch => patch.id)).toEqual(wood ? ["green-a"] : []);
        const audit = runObserver(fixture, ["green-a"]);
        const view = audit.renderAndObserve();
        expect(Object.values(view.groundMeshDrawCounts)).toEqual([1]);
        const grass = audit.grass();
        expect(grass.patches.map(patch => patch.id))
          .toEqual(fixture.plan.grass.map(patch => patch.id));
        expect(grass.planPatchCount).toBe(fixture.plan.grass.length);
        expect(grass.woodlandFloorPatches.map(patch => patch.id))
          .toEqual(fixture.plan.woodlandFloor.map(patch => patch.id));
        expect(grass.woodlandFloorPlanPatchCount).toBe(fixture.plan.woodlandFloor.length);
      } finally {
        fixture.renderer.dispose();
        fixture.surfaces.kit.dispose();
      }
    }
  });
});
