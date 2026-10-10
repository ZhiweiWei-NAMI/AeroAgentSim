import { describe, expect, it } from "vitest";
import {
  assignGroundMaterial,
  authoredGroundMaterialInput,
  CITY_GROUND_MATERIAL_RULES_V1,
  compileGroundMaterialRules,
  groundMaterialInputFromCover,
  groundMaterialInputFromGreen,
  summarizeGroundMaterialAssignments,
  type CityGroundMaterialRuleSet,
  type GroundMaterialInput,
} from "./city-ground-material-rules";

const compiled = compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1);

let nextId = 1;
function input(tags: Record<string, string>,
    overrides: Partial<GroundMaterialInput> = {}): GroundMaterialInput {
  return { polygonId: `test:polygon:${nextId++}`, origin: "source",
    sourceRefs: { sourceSha256: "a".repeat(64), elementType: "way", elementId: String(nextId) },
    tags, ...overrides };
}

function assigned(tags: Record<string, string>, overrides: Partial<GroundMaterialInput> = {},
    seed = 7) {
  return assignGroundMaterial(input(tags, overrides), compiled, seed);
}

describe("rule-set shape", () => {
  it("is frozen, JSON-compatible and complete", () => {
    expect(CITY_GROUND_MATERIAL_RULES_V1.id).toBe("aero-bench.ground-material-rules");
    expect(CITY_GROUND_MATERIAL_RULES_V1.version).toBe(1);
    expect(Object.isFrozen(CITY_GROUND_MATERIAL_RULES_V1)).toBe(true);
    expect(Object.isFrozen(CITY_GROUND_MATERIAL_RULES_V1.presets)).toBe(true);
    expect(Object.isFrozen(CITY_GROUND_MATERIAL_RULES_V1.rules)).toBe(true);
    expect(() => JSON.stringify(CITY_GROUND_MATERIAL_RULES_V1)).not.toThrow();
    const required = ["lawn-maintained", "grass-natural", "grass-sports", "grass-paver",
      "grass-generic", "artificial-turf", "tartan-track", "soil-bare", "soil-construction",
      "soil-brownfield", "sand", "gravel", "mulch", "asphalt", "concrete", "paving-unit",
      "paving-generic", "parking-generic", "plaza-generic", "court-hard-unknown",
      "pitch-turf-unknown", "water-river", "water-canal", "water-pond", "water-lake",
      "water-reservoir", "water-basin", "water-dock", "water-generic", "woodland-floor",
      "unclassified"];
    const ids = CITY_GROUND_MATERIAL_RULES_V1.presets.map(preset => preset.id);
    expect([...ids].sort()).toEqual([...required].sort());
    for (const preset of CITY_GROUND_MATERIAL_RULES_V1.presets) {
      expect(preset.tileSizeM).toBeGreaterThan(0);
      expect(Number.isFinite(preset.tileSizeM)).toBe(true);
      expect(typeof preset.variation.rotate).toBe("boolean");
    }
    expect(CITY_GROUND_MATERIAL_RULES_V1.presets.find(p => p.id === "paving-unit")
      ?.variation.rotate).toBe(true);
  });
});

describe("tier precedence and refine", () => {
  it("lets surface beat landuse and leisure", () => {
    expect(assigned({ landuse: "grass", surface: "asphalt" })).toMatchObject({
      family: "paving_asphalt", preset: "asphalt", basis: "surface-tag",
      familyRule: "surface-asphalt", matchedTags: ["surface=asphalt"] });
    expect(assigned({ leisure: "pitch", landuse: "grass", surface: "concrete" })).toMatchObject({
      family: "paving_concrete", preset: "concrete", basis: "surface-tag" });
  });

  it("refines only within the same family for surface selections", () => {
    expect(assigned({ surface: "asphalt", leisure: "park" })).toMatchObject({
      family: "paving_asphalt", preset: "asphalt", basis: "surface-tag" });
    expect(assigned({ surface: "grass", leisure: "pitch", sport: "football" }))
      .toMatchObject({ family: "grass", preset: "grass-sports",
        familyRule: "surface-grass", presetRule: "leisure-pitch-refine",
        matchedTags: ["surface=grass", "leisure=pitch"] });
  });

  it("refines water presets within the water family", () => {
    expect(assigned({ natural: "water", water: "pond" })).toMatchObject({
      family: "water", preset: "water-pond", basis: "cover-tag",
      familyRule: "natural-water", presetRule: "water-pond" });
    expect(assigned({ landuse: "basin", water: "canal" })).toMatchObject({
      family: "water", preset: "water-basin", familyRule: "landuse-basin",
      presetRule: "landuse-basin", matchedTags: ["landuse=basin"] });
    expect(assigned({ landuse: "basin" })).toMatchObject({
      family: "water", preset: "water-basin", basis: "cover-tag" });
    expect(assigned({ natural: "water" })).toMatchObject({
      family: "water", preset: "water-generic" });
  });

  it("selects tier 2 cover tags over tier 3 use tags", () => {
    expect(assigned({ natural: "wood", leisure: "park" })).toMatchObject({
      family: "woodland_floor", preset: "woodland-floor", basis: "cover-tag" });
    expect(assigned({ landuse: "meadow", amenity: "parking" })).toMatchObject({
      family: "grass", preset: "grass-natural", basis: "cover-tag" });
  });
});

describe("use presets", () => {
  it("maps parks, gardens and village greens to maintained lawn", () => {
    expect(assigned({ leisure: "park" })).toMatchObject({
      family: "grass", preset: "lawn-maintained", basis: "use-preset",
      familyRule: "leisure-park-garden", presetRule: "leisure-park-garden" });
    expect(assigned({ leisure: "garden" })).toMatchObject({ preset: "lawn-maintained" });
    expect(assigned({ landuse: "village_green" })).toMatchObject({
      family: "grass", preset: "lawn-maintained", basis: "use-preset",
      familyRule: "landuse-village-green", presetRule: "landuse-village-green" });
  });

  it("maps pitches by sport", () => {
    expect(assigned({ leisure: "pitch" })).toMatchObject({
      family: "grass", preset: "pitch-turf-unknown", basis: "use-preset",
      familyRule: "leisure-pitch", presetRule: "leisure-pitch" });
    expect(assigned({ leisure: "pitch", sport: "football" })).toMatchObject({
      family: "grass", preset: "pitch-turf-unknown", basis: "use-preset",
      familyRule: "leisure-pitch", presetRule: "leisure-pitch",
      matchedTags: ["leisure=pitch"] });
    for (const sport of ["basketball", "tennis", "volleyball", "badminton",
      "table_tennis", "netball"]) {
      expect(assigned({ leisure: "pitch", sport })).toMatchObject({
        family: "paving_generic", preset: "court-hard-unknown", basis: "use-preset",
        familyRule: "leisure-pitch-hard-court", presetRule: "leisure-pitch-hard-court",
        matchedTags: ["leisure=pitch", `sport=${sport}`] });
    }
    expect(assigned({ leisure: "pitch", surface: "tartan" })).toMatchObject({
      family: "synthetic_track", preset: "tartan-track", basis: "surface-tag" });
  });

  it("maps parking, pedestrian and square uses", () => {
    expect(assigned({ amenity: "parking" })).toMatchObject({
      family: "paving_asphalt", preset: "parking-generic", basis: "use-preset" });
    expect(assigned({ amenity: "parking_space" })).toMatchObject({ preset: "parking-generic" });
    expect(assigned({ landuse: "parking" })).toMatchObject({
      family: "paving_asphalt", preset: "parking-generic", basis: "use-preset",
      familyRule: "landuse-parking", presetRule: "landuse-parking" });
    expect(assigned({ highway: "pedestrian" })).toMatchObject({
      family: "paving_generic", preset: "plaza-generic" });
    expect(assigned({ place: "square" })).toMatchObject({
      family: "paving_generic", preset: "plaza-generic" });
  });
});

describe("tier 4 landuse uses", () => {
  it("maps construction, brownfield and recreation ground", () => {
    expect(assigned({ landuse: "construction" })).toMatchObject({
      family: "soil", preset: "soil-construction", basis: "landuse-use-preset",
      familyRule: "landuse-construction" });
    expect(assigned({ landuse: "brownfield" })).toMatchObject({
      family: "soil", preset: "soil-brownfield", basis: "landuse-use-preset" });
    expect(assigned({ landuse: "recreation_ground" })).toMatchObject({
      family: "grass", preset: "lawn-maintained", basis: "landuse-use-preset" });
  });

  it("maps cover-valued landuse at tier 2", () => {
    expect(assigned({ landuse: "grass" })).toMatchObject({
      family: "grass", preset: "lawn-maintained", basis: "cover-tag",
      familyRule: "landuse-grass", presetRule: "landuse-grass" });
    expect(assigned({ landuse: "meadow" })).toMatchObject({
      family: "grass", preset: "grass-natural", basis: "cover-tag" });
    expect(assigned({ landuse: "forest" })).toMatchObject({
      family: "woodland_floor", preset: "woodland-floor", basis: "cover-tag" });
    expect(assigned({ landuse: "reservoir" })).toMatchObject({
      family: "water", preset: "water-reservoir", basis: "cover-tag" });
    expect(assigned({ landuse: "basin" })).toMatchObject({
      family: "water", preset: "water-basin", basis: "cover-tag" });
  });
});

describe("zoning and building gating", () => {
  const zoning = ["residential", "commercial", "retail", "industrial"];
  const tier3Tags: Record<string, string>[] = [
    { leisure: "park" }, { leisure: "garden" }, { leisure: "pitch" },
    { amenity: "parking" }, { highway: "pedestrian" }, { place: "square" },
    { sport: "basketball" },
  ];

  it("never classifies zoning land use as grass or paving, for every tier 3 tag", () => {
    for (const landuse of zoning) for (const extra of tier3Tags) {
      const assignment = assigned({ landuse, ...extra });
      expect(assignment.family).toBe("unclassified");
      expect(assignment.preset).toBe("unclassified");
      expect(assignment.reason).toBe(`zoning-without-physical-cover:${landuse}`);
      expect(["grass", "paving_asphalt", "paving_concrete", "paving_unit",
        "paving_generic"]).not.toContain(assignment.family);
    }
  });

  it("lets a physical cover tag carry a zoned polygon", () => {
    expect(assigned({ landuse: "residential", surface: "asphalt" })).toMatchObject({
      family: "paving_asphalt", preset: "asphalt", basis: "surface-tag" });
    expect(assigned({ landuse: "residential", natural: "wood" })).toMatchObject({
      family: "woodland_floor", basis: "cover-tag" });
    expect(assigned({ landuse: "industrial", waterway: "canal" })).toMatchObject({
      family: "water", preset: "water-canal" });
    expect(assigned({ landuse: "residential", natural: "unsupported" })).toMatchObject({
      family: "unclassified", reason: "zoning-without-physical-cover:residential" });
  });

  it("keeps building polygons unclassified regardless of physical cover tags", () => {
    const cases: Record<string, string>[] = [{ building: "yes", leisure: "park" },
      { "building:part": "yes", landuse: "grass" }, { building: "roof", surface: "grass" },
      { building: "yes", natural: "water" }, { "building:part": "yes", surface: "concrete" }];
    for (const tags of cases) {
      expect(assigned(tags)).toMatchObject({
        family: "unclassified", reason: "building-footprint" });
    }
  });
});

describe("unknown surface and no match", () => {
  it("keeps an unknown surface value explicit and never falls through", () => {
    expect(assigned({ surface: "solar_panels", leisure: "pitch" })).toMatchObject({
      family: "unclassified", preset: "unclassified", basis: "unclassified",
      familyRule: null, presetRule: null, matchedTags: [],
      reason: "unrecognized-surface:solar_panels" });
    expect(assigned({ surface: "glass", landuse: "grass" })).toMatchObject({
      family: "unclassified", reason: "unrecognized-surface:glass" });
  });

  it("reports no-matching-rule when nothing matches", () => {
    expect(assigned({ landuse: "quarry" })).toMatchObject({
      family: "unclassified", basis: "unclassified", reason: "no-matching-rule" });
    expect(assigned({})).toMatchObject({
      family: "unclassified", reason: "no-matching-rule" });
    expect(assigned({ natural: "scrub" })).toMatchObject({
      family: "unclassified", reason: "no-matching-rule" });
  });
});

describe("water subtypes and remaining mappings", () => {
  it("maps every required water subtype", () => {
    expect(assigned({ water: "river" })).toMatchObject({ preset: "water-river", basis: "cover-tag" });
    expect(assigned({ water: "stream" })).toMatchObject({ preset: "water-river" });
    expect(assigned({ water: "oxbow" })).toMatchObject({ preset: "water-river" });
    expect(assigned({ water: "canal" })).toMatchObject({ preset: "water-canal" });
    expect(assigned({ water: "ditch" })).toMatchObject({ preset: "water-canal" });
    expect(assigned({ water: "drain" })).toMatchObject({ preset: "water-canal" });
    expect(assigned({ water: "moat" })).toMatchObject({ preset: "water-canal" });
    expect(assigned({ water: "pond" })).toMatchObject({ preset: "water-pond" });
    expect(assigned({ water: "fishpond" })).toMatchObject({ preset: "water-pond" });
    expect(assigned({ water: "lake" })).toMatchObject({ preset: "water-lake" });
    expect(assigned({ water: "lagoon" })).toMatchObject({ preset: "water-lake" });
    expect(assigned({ water: "reservoir" })).toMatchObject({ preset: "water-reservoir" });
    expect(assigned({ water: "basin" })).toMatchObject({ preset: "water-basin" });
    expect(assigned({ waterway: "riverbank" })).toMatchObject({ preset: "water-river" });
    expect(assigned({ waterway: "canal" })).toMatchObject({ preset: "water-canal" });
    expect(assigned({ waterway: "dock" })).toMatchObject({ preset: "water-dock" });
  });

  it("maps natural covers, tartan, artificial turf and the remaining surfaces", () => {
    expect(assigned({ natural: "grassland" })).toMatchObject({
      family: "grass", preset: "grass-natural" });
    expect(assigned({ natural: "sand" })).toMatchObject({ family: "sand", preset: "sand" });
    expect(assigned({ natural: "beach" })).toMatchObject({ family: "sand", preset: "sand" });
    expect(assigned({ surface: "tartan" })).toMatchObject({
      family: "synthetic_track", preset: "tartan-track" });
    expect(assigned({ surface: "artificial_turf" })).toMatchObject({
      family: "artificial_turf", preset: "artificial-turf" });
    expect(assigned({ surface: "grass_paver" })).toMatchObject({
      family: "grass", preset: "grass-paver" });
    expect(assigned({ surface: "concrete:plates" })).toMatchObject({
      family: "paving_concrete", preset: "concrete" });
    expect(assigned({ surface: "concrete:lanes" })).toMatchObject({ preset: "concrete" });
    for (const surface of ["paving_stones", "sett", "cobblestone", "unhewn_cobblestone",
      "bricks"]) {
      expect(assigned({ surface })).toMatchObject({
        family: "paving_unit", preset: "paving-unit" });
    }
    expect(assigned({ surface: "paved" })).toMatchObject({
      family: "paving_generic", preset: "paving-generic" });
    for (const surface of ["ground", "dirt", "earth", "mud", "unpaved"]) {
      expect(assigned({ surface })).toMatchObject({ family: "soil", preset: "soil-bare" });
    }
    expect(assigned({ surface: "sand" })).toMatchObject({ family: "sand", preset: "sand" });
    for (const surface of ["gravel", "fine_gravel", "pebblestone", "compacted"]) {
      expect(assigned({ surface })).toMatchObject({ family: "gravel", preset: "gravel" });
    }
    expect(assigned({ surface: "woodchips" })).toMatchObject({
      family: "mulch", preset: "mulch" });
  });
});

describe("determinism and variation", () => {
  it("is deterministic for the same input, rule set and seed", () => {
    const base = { polygonId: "fixed:id", origin: "source" as const,
      sourceRefs: { sourceSha256: "a".repeat(64), elementType: "way" as const,
        elementId: "1" }, tags: { leisure: "pitch", sport: "basketball" } };
    const first = assignGroundMaterial(base, compiled, 7);
    const second = assignGroundMaterial(base, compiled, 7);
    expect(second).toEqual(first);
  });

  it("changes only variation when the seed changes", () => {
    const base = { polygonId: "fixed:id", origin: "source" as const,
      sourceRefs: { sourceSha256: "a".repeat(64), elementType: "way" as const,
        elementId: "9" }, tags: { landuse: "construction" } };
    const one = assignGroundMaterial(base, compiled, 7);
    const two = assignGroundMaterial(base, compiled, 8);
    const { variation: variationOne, ...restOne } = one;
    const { variation: variationTwo, ...restTwo } = two;
    expect(restOne).toEqual({ ...restTwo, seed: 7 });
    expect(variationOne).not.toEqual(variationTwo);
    expect(one.seed).toBe(7);
    expect(two.seed).toBe(8);
  });

  it("keeps variation inside preset limits and never changes family, preset or basis", () => {
    const variationTags: Record<string, string>[] = [{ landuse: "grass" },
      { surface: "paving_stones" }, { natural: "water" },
      { landuse: "construction" }, { place: "square" },
      { landuse: "brownfield" }];
    for (const tags of variationTags) {
      for (const seed of [0, 7, 123456]) {
        const assignment = assigned(tags, {}, seed);
        const preset = compiled.presetById.get(assignment.preset)!;
        expect(Math.abs(assignment.variation.hueShiftDeg)).toBeLessThanOrEqual(preset.variation.hueDeg);
        expect(Math.abs(assignment.variation.valueShift)).toBeLessThanOrEqual(preset.variation.value);
        expect(assignment.variation.offsetU).toBeGreaterThanOrEqual(0);
        expect(assignment.variation.offsetU).toBeLessThan(1);
        expect(assignment.variation.offsetV).toBeGreaterThanOrEqual(0);
        expect(assignment.variation.offsetV).toBeLessThan(1);
        expect(assignment.tileSizeM).toBe(preset.tileSizeM);
        if (preset.variation.rotate) {
          expect(assignment.variation.rotationRad).toBeGreaterThanOrEqual(0);
          expect(assignment.variation.rotationRad).toBeLessThan(Math.PI * 2);
        } else {
          expect(assignment.variation.rotationRad).toBe(0);
        }
      }
    }
  });

  it("returns frozen assignments with copied source refs", () => {
    const refs = { sourceSha256: "a".repeat(64), elementType: "way" as const, elementId: "5" };
    const assignment = assigned({ landuse: "grass" }, { sourceRefs: refs, polygonId: "preserved:id" });
    expect(assignment.polygonId).toBe("preserved:id");
    expect(Object.isFrozen(assignment)).toBe(true);
    expect(Object.isFrozen(assignment.sourceRefs)).toBe(true);
    expect(Object.isFrozen(assignment.variation)).toBe(true);
    expect(Object.isFrozen(assignment.matchedTags)).toBe(true);
    expect(assignment.sourceRefs).toEqual(refs);
    expect(assignment.sourceRefs).not.toBe(refs);
  });

  it("rejects invalid inputs and seeds", () => {
    expect(() => assignGroundMaterial(input({}, { polygonId: "" }), compiled, 1))
      .toThrow(/polygonId/);
    expect(() => assignGroundMaterial(input({}, { origin: "virtual" as never }), compiled, 1))
      .toThrow(/origin/);
    expect(() => assignGroundMaterial(input({}, { sourceRefs: { designId: "d" } }), compiled, 1))
      .toThrow(/sourceRefs/);
    expect(() => assignGroundMaterial(input({}, {
      origin: "authored", sourceRefs: { sourceSha256: "x", elementType: "way", elementId: "1" } }),
      compiled, 1)).toThrow(/designId/);
    expect(() => assigned({}, {}, 1.5)).toThrow(/seed/);
  });
});

describe("helpers", () => {
  it("builds inputs from source covers and greens", () => {
    const cover = groundMaterialInputFromCover({
      id: "osm:way:1:0",
      provenance: { kind: "osm", sourceSha256: "a".repeat(64), elementType: "way",
        elementId: "1", tags: { leisure: "pitch", surface: "tartan" } } });
    expect(cover).toMatchObject({ polygonId: "osm:way:1:0", origin: "source",
      tags: { leisure: "pitch", surface: "tartan" } });
    expect(assignGroundMaterial(cover, compiled, 7)).toMatchObject({
      family: "synthetic_track", preset: "tartan-track", basis: "surface-tag" });

    const green = groundMaterialInputFromGreen({
      id: "osm:way:2:0",
      provenance: { kind: "osm", sourceSha256: "b".repeat(64), elementType: "relation",
        elementId: "2", tags: { leisure: "park" } } });
    expect(assignGroundMaterial(green, compiled, 7)).toMatchObject({
      family: "grass", preset: "lawn-maintained" });

    const authored = groundMaterialInputFromGreen({
      id: "authored:green:1", provenance: { kind: "authored", designId: "lawn-default" } });
    expect(authored).toMatchObject({ origin: "authored", tags: {},
      sourceRefs: { designId: "lawn-default" } });
    expect(assignGroundMaterial(authored, compiled, 7)).toMatchObject({
      origin: "authored", family: "unclassified", basis: "unclassified",
      reason: "authored-without-material" });
    expect(assignGroundMaterial(
      authoredGroundMaterialInput({ id: "authored:green:1",
        provenance: { kind: "authored", designId: "lawn-default" } },
        { landuse: "grass" }), compiled, 7)).toMatchObject({
        origin: "authored", family: "grass", preset: "lawn-maintained" });
    expect(() => groundMaterialInputFromCover({ id: "x",
      provenance: { kind: "authored", designId: "d" } as never })).toThrow(/osm/);
  });
});

describe("summary", () => {
  it("counts and sums area by family, preset, basis and unclassified reason", () => {
    const assignments = [
      assigned({ landuse: "construction" }, { polygonId: "a" }),
      assigned({ landuse: "construction" }, { polygonId: "b" }),
      assigned({ landuse: "grass" }, { polygonId: "c" }),
      assigned({ surface: "solar_panels" }, { polygonId: "d" }),
      assigned({ leisure: "pitch" }, { polygonId: "e" }),
    ];
    const summary = summarizeGroundMaterialAssignments(assignments,
      { a: 100, b: 40, c: 25, d: 5 });
    expect(summary.count).toBe(5);
    expect(summary.areaM2).toBe(170);
    expect(summary.byFamily.soil).toEqual({ count: 2, areaM2: 140 });
    expect(summary.byFamily.grass).toEqual({ count: 2, areaM2: 25 });
    expect(summary.byFamily.unclassified).toEqual({ count: 1, areaM2: 5 });
    expect(summary.byPreset["soil-construction"]).toEqual({ count: 2, areaM2: 140 });
    expect(summary.byPreset["lawn-maintained"]).toEqual({ count: 1, areaM2: 25 });
    expect(summary.byBasis["landuse-use-preset"]).toEqual({ count: 2, areaM2: 140 });
    expect(summary.byBasis["cover-tag"]).toEqual({ count: 1, areaM2: 25 });
    expect(summary.byBasis["use-preset"]).toEqual({ count: 1, areaM2: 0 });
    expect(summary.unclassifiedReasons["unrecognized-surface:solar_panels"])
      .toEqual({ count: 1, areaM2: 5 });
  });
});

type DeepMutable<T> = T extends object ? { -readonly [K in keyof T]: DeepMutable<T[K]> } : T;
type DeepMutableRuleSet = DeepMutable<CityGroundMaterialRuleSet>;

describe("rule-set validation", () => {
  it("accepts the shipped set and rejects specific defects", () => {
    expect(compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1).ruleSet.version).toBe(1);
    const broken = (mutate: (set: DeepMutableRuleSet) => void, message: RegExp): void => {
      const set = JSON.parse(JSON.stringify(CITY_GROUND_MATERIAL_RULES_V1)) as
        DeepMutableRuleSet;
      mutate(set);
      expect(() => compileGroundMaterialRules(set)).toThrow(message);
    };
    broken(set => { set.id = ""; }, /rule set id/);
    broken(set => { set.version = 0; }, /version/);
    broken(set => { set.version = 1.5; }, /version/);
    broken(set => { set.families = []; }, /families/);
    broken(set => { set.families = [...set.families, "grass"]; }, /duplicate family/);
    broken(set => { set.presets = [...set.presets, set.presets[0]!]; }, /duplicate preset id/);
    broken(set => { set.rules = [...set.rules, set.rules[0]!]; }, /duplicate rule id/);
    broken(set => { set.rules[0]!.family = "plutonium" as never; }, /undefined family/);
    broken(set => { set.rules[0]!.preset = "not-a-preset"; }, /undefined preset/);
    broken(set => { set.presets[0]!.family = "plutonium" as never; }, /undefined family/);
    broken(set => { set.rules[0]!.tier = 5 as never; }, /tier/);
    broken(set => { set.rules[0]!.tier = 0 as never; }, /tier/);
    broken(set => { set.rules[0]!.match.values = []; }, /values/);
    broken(set => { set.rules[0]!.match.values = [""]; }, /values/);
    broken(set => { set.rules[0]!.match.key = ""; }, /match key/);
    broken(set => { set.presets[0]!.tileSizeM = 0; }, /tileSizeM/);
    broken(set => { set.presets[0]!.tileSizeM = -1; }, /tileSizeM/);
    broken(set => { set.presets[0]!.tileSizeM = Number.POSITIVE_INFINITY; }, /tileSizeM/);
    broken(set => { set.rules[0]!.refine = false as never; }, /refine/);
    broken(set => { set.rules[0]!.preset = "asphalt"; }, /preset family/);
    broken(set => { set.rules[0]!.with = { key: "", values: ["pitch"] }; }, /with key/);
    broken(set => { set.rules[0]!.with = { key: "sport", values: [] }; }, /with values/);
    broken(set => { set.rules[0]!.with = null as never; }, /with must be an object/);
    broken(set => { set.rules[0]!.refine = true; }, /refines/);
    broken(set => { set.rules[0]!.refines = ["grass-generic"]; }, /requires refine/);
    broken(set => { const r = set.rules.find(r => r.refine)!; r.refines = []; }, /refines/);
    broken(set => { const r = set.rules.find(r => r.refine)!; r.refines = ["missing"]; }, /undefined preset/);
    broken(set => { const r = set.rules.find(r => r.refine)!; r.refines = ["asphalt"]; }, /another family/);
  });
});


describe("round 2 regressions", () => {
  const cases: [Record<string, string>, string, string][] = [
    [{ surface: "grass", leisure: "pitch" }, "grass", "grass-sports"],
    ...["football", "basketball", "tennis", "unknown"].map(sport =>
      [{ surface: "grass", leisure: "pitch", sport }, "grass", "grass-sports"] as
        [Record<string, string>, string, string]),
    [{ surface: "grass", leisure: "park" }, "grass", "lawn-maintained"],
    [{ surface: "grass", leisure: "garden" }, "grass", "lawn-maintained"],
    [{ surface: "grass", landuse: "village_green" }, "grass", "lawn-maintained"],
    [{ surface: "grass", landuse: "recreation_ground" }, "grass", "lawn-maintained"],
    [{ surface: "grass", sport: "basketball" }, "grass", "grass-generic"],
    [{ natural: "grassland", leisure: "park" }, "grass", "grass-natural"],
    [{ surface: "grass_paver", leisure: "pitch" }, "grass", "grass-paver"],
    [{ natural: "water", water: "pond" }, "water", "water-pond"],
    [{ water: "pond" }, "water", "water-pond"],
    [{ leisure: "pitch", sport: "basketball" }, "paving_generic", "court-hard-unknown"],
    [{ leisure: "pitch" }, "grass", "pitch-turf-unknown"],
    [{ leisure: "park", sport: "basketball" }, "grass", "lawn-maintained"],
    [{ amenity: "parking", sport: "basketball" }, "paving_asphalt", "parking-generic"],
    [{ surface: "asphalt", landuse: "grass", leisure: "park" }, "paving_asphalt", "asphalt"],
    [{ surface: "asphalt", leisure: "pitch", sport: "basketball" }, "paving_asphalt", "asphalt"],
    [{ surface: "concrete", amenity: "parking" }, "paving_concrete", "concrete"],
    [{ surface: "artificial_turf", leisure: "pitch" }, "artificial_turf", "artificial-turf"],
    [{ building: "roof", surface: "grass" }, "unclassified", "unclassified"],
    [{ landuse: "residential", surface: "asphalt" }, "paving_asphalt", "asphalt"],
    [{ landuse: "commercial", leisure: "park" }, "unclassified", "unclassified"],
    [{ surface: "solar_panels", leisure: "pitch" }, "unclassified", "unclassified"],
    [{ sport: "basketball" }, "unclassified", "unclassified"],
  ];
  it.each(cases)("selects %j as %s/%s", (tags, family, preset) => {
    expect(assigned(tags)).toMatchObject({ family, preset });
  });

  it("uses tier then declaration order for one refinement step", () => {
    const base = CITY_GROUND_MATERIAL_RULES_V1;
    const extra = (id: string, tier: 3 | 4, target: string, refines: string[]) => ({
      id, tier, match: { key: "custom", values: ["yes"] }, refine: true as const,
      refines, family: "grass" as const, preset: target });
    const r = compileGroundMaterialRules({ ...base, rules: [
      ...base.rules, extra("late-tier", 4, "lawn-maintained", ["grass-generic"]),
      extra("first-tier", 3, "grass-sports", ["grass-generic"]),
      extra("same-tier", 3, "grass-natural", ["grass-generic"]),
      extra("chain", 3, "grass-natural", ["grass-sports"]),
    ] });
    expect(assignGroundMaterial(input({ surface: "grass", custom: "yes" }), r, 7))
      .toMatchObject({ familyRule: "surface-grass", presetRule: "first-tier", preset: "grass-sports" });
  });

  it("changes only variation and recorded identity when rule-set ID or version changes", () => {
    const i = input({ surface: "grass" });
    const a = assignGroundMaterial(i, compiled, 7);
    for (const change of [{ id: "other-rules" }, { version: 2 }]) {
      const b = assignGroundMaterial(i,
        compileGroundMaterialRules({ ...CITY_GROUND_MATERIAL_RULES_V1, ...change }), 7);
      const { variation: av, ruleSetId: _ai, ruleVersion: _av, ...ar } = a;
      const { variation: bv, ruleSetId: _bi, ruleVersion: _bv, ...br } = b;
      expect(ar).toEqual(br);
      expect(av).not.toEqual(bv);
    }
  });

  it("deep-freezes defaults, compiled lookups and snapshots", () => {
    const assertFrozen = (value: unknown): void => {
      if (value !== null && typeof value === "object") {
        expect(Object.isFrozen(value)).toBe(true);
        Object.values(value).forEach(assertFrozen);
      }
    };
    assertFrozen(CITY_GROUND_MATERIAL_RULES_V1);
    assertFrozen(compiled);
    for (const rule of compiled.ruleById.values()) assertFrozen(rule);
    for (const preset of compiled.presetById.values()) assertFrozen(preset);
    expect("set" in compiled.ruleById).toBe(false);
    expect("clear" in compiled.presetById).toBe(false);
    const set = JSON.parse(JSON.stringify(CITY_GROUND_MATERIAL_RULES_V1)) as DeepMutableRuleSet;
    const r = compileGroundMaterialRules(set);
    set.rules[0]!.match.values[0] = "changed";
    set.presets[0]!.variation.hueDeg = 100;
    expect(r.ruleById.get("surface-grass")!.match.values).toEqual(["grass"]);
    expect(r.presetById.get("lawn-maintained")!.variation.hueDeg).toBe(4);
  });

  it("preserves source tags through callbacks and rejects source authored-tag input", () => {
    const green = { id: "source:1", provenance: { kind: "osm" as const,
      sourceSha256: "a".repeat(64), elementType: "way" as const, elementId: "1",
      tags: { natural: "wood" } } };
    expect([green].map(groundMaterialInputFromGreen)[0]!.tags).toEqual({ natural: "wood" });
    expect(groundMaterialInputFromGreen.length).toBe(1);
    expect(groundMaterialInputFromCover.length).toBe(1);
    expect(() => authoredGroundMaterialInput(green, { surface: "asphalt" })).toThrow(/authored/);
    const authored = { id: "authored:1", provenance: { kind: "authored" as const, designId: "d" } };
    for (const tags of [undefined, null, 42, "invalid", [], { surface: 1 }, new Date()]) {
      expect(() => authoredGroundMaterialInput(authored, tags as never)).toThrow(/tags/);
    }
    const assignment = assignGroundMaterial(authoredGroundMaterialInput(authored,
      { surface: "grass" }), compiled, 7);
    expect(assignment).toMatchObject({ polygonId: "authored:1", origin: "authored",
      sourceRefs: { designId: "d" }, preset: "grass-generic" });
    expect(Object.isFrozen(assignment.sourceRefs)).toBe(true);
  });
});
