/** Pure, versioned material selection from retained ground polygon tags. */

/** FNV-1a over the key, then the MurmurHash3 fmix32 finaliser. Same algorithm
 * as `city-grass-clumps.ts`: without the finaliser, keys that differ only in
 * their last character hash to near-constant offsets, which correlates the
 * variates and lines up samples. */
function hash(text: string): number {
  let value = 2166136261;
  for (let i = 0; i < text.length; i++) value = Math.imul(value ^ text.charCodeAt(i), 16777619);
  value ^= value >>> 16; value = Math.imul(value, 0x85ebca6b);
  value ^= value >>> 13; value = Math.imul(value, 0xc2b2ae35);
  value ^= value >>> 16;
  return value >>> 0;
}

/** Same deterministic variate construction as `city-grass-clumps.ts`. */
function randomFrom(ruleSetId: string, ruleVersion: number, seed: number,
    polygonId: string, purpose: string): number {
  return hash(JSON.stringify([ruleSetId, ruleVersion, seed, polygonId, purpose])) / 4294967296;
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

/** Expose a read-only view: Object.freeze(new Map()) still permits Map.set(). */
function frozenLookup<T>(entries: Map<string, T>): ReadonlyMap<string, T> {
  const view: ReadonlyMap<string, T> = {
    size: entries.size,
    get: key => entries.get(key),
    has: key => entries.has(key),
    keys: () => entries.keys(),
    values: () => entries.values(),
    entries: () => entries.entries(),
    [Symbol.iterator]: () => entries[Symbol.iterator](),
    forEach: (callback, thisArg) => entries.forEach((value, key) =>
      callback.call(thisArg, value, key, view)),
  };
  return Object.freeze(view);
}

export type GroundMaterialFamily =
  | "grass" | "artificial_turf" | "synthetic_track" | "soil" | "sand" | "gravel"
  | "mulch" | "paving_asphalt" | "paving_concrete" | "paving_unit" | "paving_generic"
  | "water" | "woodland_floor" | "unclassified";

export interface GroundMaterialVariation {
  /** Peak hue jitter in degrees; the sampled shift stays within +/-hueDeg. */
  readonly hueDeg: number;
  /** Peak value jitter; the sampled shift stays within +/-value. */
  readonly value: number;
  /** Whether the texture may rotate per polygon. */
  readonly rotate: boolean;
}

export interface GroundMaterialPreset {
  readonly id: string;
  readonly family: GroundMaterialFamily;
  readonly label: string;
  /** Nominal real-world texture repeat in metres. The texture module may later
   * replace it with the asset's stated size; the field stays. */
  readonly tileSizeM: number;
  readonly variation: GroundMaterialVariation;
}

export type GroundMaterialBasis =
  | "surface-tag" | "cover-tag" | "use-preset" | "landuse-use-preset" | "unclassified";

export interface GroundMaterialRule {
  readonly id: string;
  readonly tier: 1 | 2 | 3 | 4;
  readonly match: { readonly key: string; readonly values: readonly string[] };
  /** Conjunctive tag condition; considered before unconditional rules on the same key. */
  readonly with?: { readonly key: string; readonly values: readonly string[] };
  /** A refiner changes only the preset, and only from one of these same-family presets. */
  readonly refine?: true;
  readonly refines?: readonly string[];
  readonly family: GroundMaterialFamily;
  readonly preset: string;
}

export interface CityGroundMaterialRuleSet {
  readonly id: string;
  readonly version: number;
  readonly families: readonly GroundMaterialFamily[];
  readonly presets: readonly GroundMaterialPreset[];
  readonly rules: readonly GroundMaterialRule[];
}

export interface CompiledGroundMaterialRules {
  readonly ruleSet: CityGroundMaterialRuleSet;
  readonly ruleById: ReadonlyMap<string, GroundMaterialRule>;
  readonly presetById: ReadonlyMap<string, GroundMaterialPreset>;
  /** Refine-capable rules in rule-set declaration order. */
  readonly refiners: readonly GroundMaterialRule[];
}

export const GROUND_MATERIAL_FAMILIES = Object.freeze([
  "grass", "artificial_turf", "synthetic_track", "soil", "sand", "gravel", "mulch",
  "paving_asphalt", "paving_concrete", "paving_unit", "paving_generic", "water",
  "woodland_floor", "unclassified",
] as const satisfies readonly GroundMaterialFamily[]);

const ZONING_LANDUSES = new Set(["residential", "commercial", "retail", "industrial"]);

const UNKNOWN = "unclassified" as const;
const NO_MATCHING_RULE = "no-matching-rule";
const AUTHORED_WITHOUT_MATERIAL = "authored-without-material";

function preset(id: string, family: GroundMaterialFamily, label: string, tileSizeM: number,
    variation: GroundMaterialVariation): GroundMaterialPreset {
  return { id, family, label, tileSizeM, variation };
}

function rule(id: string, tier: 1 | 2 | 3 | 4, key: string, values: readonly string[],
    family: GroundMaterialFamily, preset: string, refines?: readonly string[]): GroundMaterialRule {
  return { id, tier, match: { key, values }, family, preset,
    ...(refines === undefined ? {} : { refine: true as const, refines }) };
}

const hue = (hueDeg: number, value: number): GroundMaterialVariation =>
  ({ hueDeg, value, rotate: false });
const hueRot = (hueDeg: number, value: number): GroundMaterialVariation =>
  ({ hueDeg, value, rotate: true });

/** The versioned rule set for AERO-Bench ground material work. Frozen and
 * JSON-compatible. */
export const CITY_GROUND_MATERIAL_RULES_V1: CityGroundMaterialRuleSet = deepFreeze({
  id: "aero-bench.ground-material-rules",
  version: 1,
  families: Object.freeze([...GROUND_MATERIAL_FAMILIES]),
  presets: Object.freeze([
    preset("lawn-maintained", "grass", "Maintained lawn", 4, hue(4, 0.06)),
    preset("grass-natural", "grass", "Natural grassland", 5, hue(6, 0.1)),
    preset("grass-sports", "grass", "Sports turf", 3, hue(3, 0.05)),
    preset("grass-paver", "grass", "Grass paver", 0.6, hue(4, 0.06)),
    preset("grass-generic", "grass", "Generic grass surface", 4, hue(6, 0.08)),
    preset("artificial-turf", "artificial_turf", "Artificial turf", 2, hue(4, 0.05)),
    preset("tartan-track", "synthetic_track", "Tartan synthetic track", 2, hue(5, 0.06)),
    preset("soil-bare", "soil", "Bare soil", 4, hue(5, 0.08)),
    preset("soil-construction", "soil", "Construction ground", 6, hue(4, 0.1)),
    preset("soil-brownfield", "soil", "Brownfield ground", 5, hue(4, 0.12)),
    preset("sand", "sand", "Sand", 3, hue(4, 0.06)),
    preset("gravel", "gravel", "Gravel", 1.5, hue(3, 0.07)),
    preset("mulch", "mulch", "Wood mulch", 2, hue(4, 0.08)),
    preset("asphalt", "paving_asphalt", "Asphalt paving", 5, hue(2, 0.04)),
    preset("concrete", "paving_concrete", "Concrete paving", 4, hue(2, 0.05)),
    preset("paving-unit", "paving_unit", "Unit paving", 2, hueRot(3, 0.06)),
    preset("paving-generic", "paving_generic", "Generic paving", 5, hue(3, 0.05)),
    preset("parking-generic", "paving_asphalt", "Parking surface", 5, hue(2, 0.04)),
    preset("plaza-generic", "paving_generic", "Plaza paving", 4, hue(3, 0.06)),
    preset("court-hard-unknown", "paving_generic", "Hard court surface", 3, hue(4, 0.06)),
    preset("pitch-turf-unknown", "grass", "Pitch turf (unknown sport)", 3, hue(3, 0.05)),
    preset("water-river", "water", "River", 8, hue(3, 0.04)),
    preset("water-canal", "water", "Canal", 8, hue(3, 0.04)),
    preset("water-pond", "water", "Pond", 6, hue(3, 0.04)),
    preset("water-lake", "water", "Lake", 12, hue(2, 0.04)),
    preset("water-reservoir", "water", "Reservoir", 12, hue(2, 0.04)),
    preset("water-basin", "water", "Basin", 10, hue(2, 0.04)),
    preset("water-dock", "water", "Dock", 10, hue(2, 0.04)),
    preset("water-generic", "water", "Water body", 8, hue(3, 0.04)),
    preset("woodland-floor", "woodland_floor", "Woodland floor", 6, hue(5, 0.1)),
    preset("unclassified", UNKNOWN, "Unclassified ground", 4, hue(0, 0)),
  ]),
  rules: Object.freeze([
    // Tier 1 - explicit surface tags (basis `surface-tag`).
    rule("surface-grass", 1, "surface", ["grass"], "grass", "grass-generic"),
    rule("surface-grass-paver", 1, "surface", ["grass_paver"], "grass", "grass-paver"),
    rule("surface-artificial-turf", 1, "surface", ["artificial_turf"],
      "artificial_turf", "artificial-turf"),
    rule("surface-tartan", 1, "surface", ["tartan"], "synthetic_track", "tartan-track"),
    rule("surface-asphalt", 1, "surface", ["asphalt"], "paving_asphalt", "asphalt"),
    rule("surface-concrete", 1, "surface",
      ["concrete", "concrete:plates", "concrete:lanes"], "paving_concrete", "concrete"),
    rule("surface-paving-units", 1, "surface",
      ["paving_stones", "sett", "cobblestone", "unhewn_cobblestone", "bricks"],
      "paving_unit", "paving-unit"),
    rule("surface-paved", 1, "surface", ["paved"], "paving_generic", "paving-generic"),
    rule("surface-soil", 1, "surface", ["ground", "dirt", "earth", "mud", "unpaved"],
      "soil", "soil-bare"),
    rule("surface-sand", 1, "surface", ["sand"], "sand", "sand"),
    rule("surface-gravel", 1, "surface", ["gravel", "fine_gravel", "pebblestone", "compacted"],
      "gravel", "gravel"),
    rule("surface-woodchips", 1, "surface", ["woodchips"], "mulch", "mulch"),
    // Tier 2 - physical cover keys (basis `cover-tag`).
    rule("natural-water", 2, "natural", ["water"], "water", "water-generic"),
    rule("natural-grassland", 2, "natural", ["grassland"], "grass", "grass-natural"),
    rule("natural-wood", 2, "natural", ["wood"], "woodland_floor", "woodland-floor"),
    rule("natural-sand", 2, "natural", ["sand", "beach"], "sand", "sand"),
    rule("water-river", 2, "water", ["river", "stream", "oxbow"], "water", "water-river", ["water-generic"]),
    rule("water-canal", 2, "water", ["canal", "ditch", "drain", "moat"], "water", "water-canal", ["water-generic"]),
    rule("water-pond", 2, "water", ["pond", "fishpond"], "water", "water-pond", ["water-generic"]),
    rule("water-lake", 2, "water", ["lake", "lagoon"], "water", "water-lake", ["water-generic"]),
    rule("water-reservoir", 2, "water", ["reservoir"], "water", "water-reservoir", ["water-generic"]),
    rule("water-basin", 2, "water", ["basin"], "water", "water-basin", ["water-generic"]),
    rule("waterway-riverbank", 2, "waterway", ["riverbank"], "water", "water-river"),
    rule("waterway-canal", 2, "waterway", ["canal"], "water", "water-canal"),
    rule("waterway-dock", 2, "waterway", ["dock"], "water", "water-dock"),
    // Cover-valued `landuse` at tier 2 (basis `cover-tag`).
    rule("landuse-grass", 2, "landuse", ["grass"], "grass", "lawn-maintained"),
    rule("landuse-meadow", 2, "landuse", ["meadow"], "grass", "grass-natural"),
    rule("landuse-forest", 2, "landuse", ["forest"], "woodland_floor", "woodland-floor"),
    rule("landuse-reservoir", 2, "landuse", ["reservoir"], "water", "water-reservoir"),
    rule("landuse-basin", 2, "landuse", ["basin"], "water", "water-basin"),
    // Tier 3 - use presets (basis `use-preset`).
    rule("leisure-park-garden", 3, "leisure", ["park", "garden"], "grass", "lawn-maintained"),
    rule("leisure-pitch", 3, "leisure", ["pitch"], "grass", "pitch-turf-unknown"),
    { ...rule("leisure-pitch-hard-court", 3, "leisure", ["pitch"],
      "paving_generic", "court-hard-unknown"),
      with: { key: "sport", values: ["basketball", "tennis", "volleyball", "badminton",
        "table_tennis", "netball"] } },
    rule("leisure-pitch-refine", 3, "leisure", ["pitch"], "grass", "grass-sports", ["grass-generic"]),
    rule("leisure-park-garden-refine", 3, "leisure", ["park", "garden"],
      "grass", "lawn-maintained", ["grass-generic"]),
    rule("amenity-parking", 3, "amenity", ["parking", "parking_space"],
      "paving_asphalt", "parking-generic"),
    rule("highway-pedestrian", 3, "highway", ["pedestrian"], "paving_generic", "plaza-generic"),
    rule("place-square", 3, "place", ["square"], "paving_generic", "plaza-generic"),
    // Tier 4 - other landuse values (basis `landuse-use-preset`).
    rule("landuse-construction", 4, "landuse", ["construction"], "soil", "soil-construction"),
    rule("landuse-brownfield", 4, "landuse", ["brownfield"], "soil", "soil-brownfield"),
    rule("landuse-recreation-ground", 4, "landuse", ["recreation_ground"],
      "grass", "lawn-maintained"),
    rule("landuse-village-green", 3, "landuse", ["village_green"],
      "grass", "lawn-maintained"),
    rule("landuse-village-green-refine", 3, "landuse", ["village_green"],
      "grass", "lawn-maintained", ["grass-generic"]),
    rule("landuse-recreation-ground-refine", 4, "landuse", ["recreation_ground"],
      "grass", "lawn-maintained", ["grass-generic"]),
    rule("landuse-parking", 3, "landuse", ["parking"], "paving_asphalt", "parking-generic"),
  ]),
});

/** Minimal structural views so this module never imports the three.js-based
 * producers; `CityGroundCover` and `EnvironmentGreen` satisfy them exactly. */
export interface GroundMaterialCoverLike {
  readonly id: string;
  readonly provenance: {
    readonly kind: "osm";
    readonly sourceSha256: string;
    readonly elementType: "way" | "relation";
    readonly elementId: string;
    readonly tags: Readonly<Record<string, string>>;
  };
}

export interface GroundMaterialGreenLike {
  readonly id: string;
  readonly provenance:
    | { readonly kind: "osm"; readonly sourceSha256: string;
        readonly elementType: "way" | "relation"; readonly elementId: string;
        readonly tags: Readonly<Record<string, string>> }
    | { readonly kind: "authored"; readonly designId: string };
}

export type GroundMaterialSourceRefs =
  | { readonly sourceSha256: string; readonly elementType: "way" | "relation"; readonly elementId: string }
  | { readonly designId: string };

export interface GroundMaterialInput {
  readonly polygonId: string;
  readonly origin: "source" | "authored";
  readonly tags: Readonly<Record<string, string>>;
  readonly sourceRefs: GroundMaterialSourceRefs;
}

export interface GroundMaterialAssignment {
  readonly polygonId: string;
  readonly origin: "source" | "authored";
  readonly sourceRefs: GroundMaterialSourceRefs;
  readonly family: GroundMaterialFamily;
  readonly preset: string;
  readonly basis: GroundMaterialBasis;
  readonly familyRule: string | null;
  readonly presetRule: string | null;
  readonly matchedTags: readonly string[];
  /** Null unless the assignment is `unclassified`. */
  readonly reason: string | null;
  readonly ruleSetId: string;
  readonly ruleVersion: number;
  readonly seed: number;
  readonly tileSizeM: number;
  readonly variation: {
    readonly hueShiftDeg: number;
    readonly valueShift: number;
    readonly rotationRad: number;
    readonly offsetU: number;
    readonly offsetV: number;
  };
}

export interface GroundMaterialGroupStats {
  readonly count: number;
  readonly areaM2: number;
}

export interface GroundMaterialSummary {
  readonly count: number;
  readonly areaM2: number;
  readonly byFamily: Readonly<Record<string, GroundMaterialGroupStats>>;
  readonly byPreset: Readonly<Record<string, GroundMaterialGroupStats>>;
  readonly byBasis: Readonly<Record<string, GroundMaterialGroupStats>>;
  readonly unclassifiedReasons: Readonly<Record<string, GroundMaterialGroupStats>>;
}

function isRecord(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`ground-material-rules: ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

/** Validates a rule set: unique rule and preset IDs, every rule's family and
 * preset defined, every preset's family defined, tiers 1-4, non-empty values,
 * positive finite `tileSizeM`. Throws a specific error otherwise. */
export function compileGroundMaterialRules(ruleSet: CityGroundMaterialRuleSet): CompiledGroundMaterialRules {
  const set = isRecord(ruleSet, "rule set") as unknown as CityGroundMaterialRuleSet;
  if (typeof set.id !== "string" || set.id.length === 0) {
    throw new Error("ground-material-rules: rule set id must be a non-empty string");
  }
  if (!Number.isSafeInteger(set.version) || set.version < 1) {
    throw new Error(`ground-material-rules: rule set version must be a positive integer: ${String(set.version)}`);
  }
  if (!Array.isArray(set.families) || set.families.length === 0
    || set.families.some(family => typeof family !== "string" || family.length === 0)) {
    throw new Error("ground-material-rules: families must be a non-empty array of non-empty strings");
  }
  if (!Array.isArray(set.presets) || !Array.isArray(set.rules)) {
    throw new Error("ground-material-rules: presets and rules must be arrays");
  }
  const families = new Set<string>(set.families);
  if (families.size !== set.families.length) {
    throw new Error("ground-material-rules: duplicate family");
  }
  if (!families.has(UNKNOWN)) {
    throw new Error(`ground-material-rules: rule set must define the ${UNKNOWN} family`);
  }
  const presetById = new Map<string, GroundMaterialPreset>();
  for (const entry of set.presets) {
    const item = isRecord(entry, "preset") as unknown as GroundMaterialPreset;
    if (typeof item.id !== "string" || item.id.length === 0) {
      throw new Error("ground-material-rules: preset id must be a non-empty string");
    }
    if (presetById.has(item.id)) {
      throw new Error(`ground-material-rules: duplicate preset id: ${item.id}`);
    }
    if (typeof item.family !== "string" || !families.has(item.family)) {
      throw new Error(`ground-material-rules: preset ${item.id} references undefined family ${String(item.family)}`);
    }
    if (typeof item.label !== "string" || item.label.length === 0) {
      throw new Error(`ground-material-rules: preset ${item.id} label must be a non-empty string`);
    }
    if (typeof item.tileSizeM !== "number" || !Number.isFinite(item.tileSizeM) || item.tileSizeM <= 0) {
      throw new Error(`ground-material-rules: preset ${item.id} tileSizeM must be a positive finite number: ${String(item.tileSizeM)}`);
    }
    const variation = isRecord(item.variation, `preset ${item.id} variation`) as unknown as GroundMaterialVariation;
    if (typeof variation.hueDeg !== "number" || !Number.isFinite(variation.hueDeg) || variation.hueDeg < 0) {
      throw new Error(`ground-material-rules: preset ${item.id} variation hueDeg must be a finite number`);
    }
    if (typeof variation.value !== "number" || !Number.isFinite(variation.value) || variation.value < 0) {
      throw new Error(`ground-material-rules: preset ${item.id} variation value must be a finite number`);
    }
    if (typeof variation.rotate !== "boolean") {
      throw new Error(`ground-material-rules: preset ${item.id} variation rotate must be a boolean`);
    }
    presetById.set(item.id, item);
  }
  if (presetById.get(UNKNOWN)?.family !== UNKNOWN) {
    throw new Error(`ground-material-rules: rule set must define a preset for the ${UNKNOWN} family`);
  }
  const ruleById = new Map<string, GroundMaterialRule>();
  for (const entry of set.rules) {
    const item = isRecord(entry, "rule") as unknown as GroundMaterialRule;
    if (typeof item.id !== "string" || item.id.length === 0) {
      throw new Error("ground-material-rules: rule id must be a non-empty string");
    }
    if (ruleById.has(item.id)) {
      throw new Error(`ground-material-rules: duplicate rule id: ${item.id}`);
    }
    if (item.tier !== 1 && item.tier !== 2 && item.tier !== 3 && item.tier !== 4) {
      throw new Error(`ground-material-rules: rule ${item.id} tier must be 1-4: ${String(item.tier)}`);
    }
    validateMatch(item.match, `rule ${item.id} match`);
    if (item.with !== undefined) validateMatch(item.with, `rule ${item.id} with`);
    if (typeof item.family !== "string" || !families.has(item.family)) {
      throw new Error(`ground-material-rules: rule ${item.id} references undefined family ${String(item.family)}`);
    }
    if (typeof item.preset !== "string" || !presetById.has(item.preset)) {
      throw new Error(`ground-material-rules: rule ${item.id} references undefined preset ${String(item.preset)}`);
    }
    if (item.refine !== undefined && item.refine !== true) {
      throw new Error(`ground-material-rules: rule ${item.id} refine must be true when present`);
    }
    if (presetById.get(item.preset)!.family !== item.family) {
      throw new Error(`ground-material-rules: rule ${item.id} preset family must match rule family`);
    }
    if (item.refine === true) {
      if (!Array.isArray(item.refines) || item.refines.length === 0) {
        throw new Error(`ground-material-rules: rule ${item.id} refines must be a non-empty array`);
      }
      for (const id of item.refines) {
        if (typeof id !== "string" || !presetById.has(id)) {
          throw new Error(`ground-material-rules: rule ${item.id} refines undefined preset ${String(id)}`);
        }
        if (presetById.get(id)!.family !== item.family) {
          throw new Error(`ground-material-rules: rule ${item.id} refines preset ${id} from another family`);
        }
      }
    } else if (item.refines !== undefined) {
      throw new Error(`ground-material-rules: rule ${item.id} refines requires refine: true`);
    }
    ruleById.set(item.id, item);
  }
  const snapshot = deepFreeze<CityGroundMaterialRuleSet>({ id: set.id, version: set.version,
    families: [...set.families],
    presets: set.presets.map(item => ({ ...item, variation: { ...item.variation } })),
    rules: set.rules.map(item => ({ ...item,
      match: { key: item.match.key, values: [...item.match.values] },
      ...(item.with === undefined ? {} : { with: { key: item.with.key, values: [...item.with.values] } }),
      ...(item.refines === undefined ? {} : { refines: [...item.refines] }) })),
  });
  return Object.freeze({ ruleSet: snapshot,
    ruleById: frozenLookup(new Map(snapshot.rules.map(item => [item.id, item]))),
    presetById: frozenLookup(new Map(snapshot.presets.map(item => [item.id, item]))),
    refiners: Object.freeze(snapshot.rules.filter(item => item.refine === true)
      .sort((a, b) => a.tier - b.tier)),
  });
}

function validateMatch(value: unknown, label: string): void {
  const match = isRecord(value, label);
  if (typeof match.key !== "string" || match.key.length === 0) {
    throw new Error(`ground-material-rules: ${label} key must be a non-empty string`);
  }
  if (!Array.isArray(match.values) || match.values.length === 0
    || match.values.some(value => typeof value !== "string" || value.length === 0)) {
    throw new Error(`ground-material-rules: ${label} values must be a non-empty array of non-empty strings`);
  }
}

function matches(rule: GroundMaterialRule, tags: Readonly<Record<string, string>>): boolean {
  return rule.match.values.includes(tags[rule.match.key]!)
    && (rule.with === undefined || rule.with.values.includes(tags[rule.with.key]!));
}

function matchedTags(rule: GroundMaterialRule, tags: Readonly<Record<string, string>>): string[] {
  return [rule.match, ...(rule.with === undefined ? [] : [rule.with])]
    .map(match => `${match.key}=${tags[match.key]}`);
}

interface Outcome {
  readonly family: GroundMaterialFamily;
  readonly presetId: string;
  readonly basis: GroundMaterialBasis;
  readonly familyRule: GroundMaterialRule | null;
  readonly presetRule: GroundMaterialRule | null;
  readonly matchedTags: readonly string[];
  readonly reason: string | null;
}

function unclassified(reason: string): Outcome {
  return { family: UNKNOWN, presetId: UNKNOWN, basis: "unclassified",
    familyRule: null, presetRule: null, matchedTags: [], reason };
}

/** Family selection follows physical precedence; refinement changes one preset only. */
function classify(tags: Readonly<Record<string, string>>,
    compiled: CompiledGroundMaterialRules, origin: GroundMaterialInput["origin"]): Outcome {
  // a. Buildings are never ground, including tagged roofs and building parts.
  if (tags.building !== undefined || tags["building:part"] !== undefined) {
    return unclassified("building-footprint");
  }
  if (origin === "authored" && Object.keys(tags).length === 0) {
    return unclassified(AUTHORED_WITHOUT_MATERIAL);
  }
  const rules = compiled.ruleSet.rules;
  const select = (tier: number, keys: readonly string[]): GroundMaterialRule | undefined => {
    for (const key of keys) {
      const candidates = rules.filter(rule => rule.tier === tier && rule.match.key === key
        && rule.refine !== true && matches(rule, tags));
      const selected = candidates.find(rule => rule.with !== undefined) ?? candidates[0];
      if (selected !== undefined) return selected;
    }
    return undefined;
  };
  // b. Explicit surfaces select a material or stop with an unknown-surface reason.
  let familyRule: GroundMaterialRule | undefined;
  if (tags.surface !== undefined) {
    familyRule = select(1, ["surface"]);
    if (familyRule === undefined) return unclassified(`unrecognized-surface:${tags.surface}`);
  }
  // c. Physical covers use a fixed key order. A water subtype is itself cover
  // evidence when no other physical cover selects a family (decision e).
  familyRule ??= select(2, ["natural", "landuse", "waterway", "water"]);
  if (familyRule === undefined) {
    familyRule = compiled.refiners.find(rule => rule.tier === 2
      && rule.match.key === "water" && matches(rule, tags));
  }
  // d. Zoning cannot supply a physical cover through a use preset.
  if (familyRule === undefined && ZONING_LANDUSES.has(tags.landuse ?? "")) {
    return unclassified(`zoning-without-physical-cover:${tags.landuse}`);
  }
  // e. Use rules select in key order; sport is only a conjunctive pitch condition.
  // The original mappings place village_green and parking landuse rules at tier 3.
  familyRule ??= select(3, ["leisure", "amenity", "highway", "place", "landuse"]);
  familyRule ??= select(4, ["landuse"]);
  if (familyRule === undefined) return unclassified(NO_MATCHING_RULE);
  const basis: GroundMaterialBasis = familyRule.tier === 1 ? "surface-tag"
    : familyRule.tier === 2 ? "cover-tag"
    : familyRule.tier === 3 ? "use-preset" : "landuse-use-preset";
  // f. One refinement, lowest tier first then declaration order, within family.
  const refiner = compiled.refiners.find(rule => rule.family === familyRule.family
    && rule.refines!.includes(familyRule.preset) && matches(rule, tags));
  const presetRule = refiner ?? familyRule;
  return { family: familyRule.family, presetId: presetRule.preset, basis,
    familyRule, presetRule,
    matchedTags: [...new Set([...matchedTags(familyRule, tags),
      ...(refiner === undefined ? [] : matchedTags(refiner, tags))])], reason: null };
}

function validateInput(input: GroundMaterialInput): void {
  const record = isRecord(input, "input") as unknown as GroundMaterialInput;
  if (typeof record.polygonId !== "string" || record.polygonId.length === 0) {
    throw new Error("ground-material-rules: input polygonId must be a non-empty string");
  }
  if (record.origin !== "source" && record.origin !== "authored") {
    throw new Error(`ground-material-rules: input origin must be "source" or "authored": ${String(record.origin)}`);
  }
  const tags = isRecord(record.tags, "input tags");
  if (Object.values(tags).some(value => typeof value !== "string")) {
    throw new Error("ground-material-rules: input tags values must be strings");
  }
  const refs = isRecord(record.sourceRefs, "input sourceRefs");
  if (record.origin === "authored") {
    if (typeof refs.designId !== "string" || refs.designId.length === 0) {
      throw new Error("ground-material-rules: authored input sourceRefs.designId must be a non-empty string");
    }
  } else if (typeof refs.sourceSha256 !== "string" || refs.sourceSha256.length === 0
    || (refs.elementType !== "way" && refs.elementType !== "relation")
    || typeof refs.elementId !== "string" || refs.elementId.length === 0) {
    throw new Error("ground-material-rules: source input sourceRefs must carry sourceSha256, elementType and elementId");
  }
}

/** Assigns the material family, preset, basis and per-polygon variation for one
 * ground polygon. Deterministic: the same input, compiled rule set and seed
 * always produce the same frozen assignment, and the seed influences only
 * `variation`. */
export function assignGroundMaterial(input: GroundMaterialInput,
    compiled: CompiledGroundMaterialRules, seed: number): GroundMaterialAssignment {
  validateInput(input);
  if (!Number.isSafeInteger(seed)) {
    throw new Error(`ground-material-rules: seed must be a safe integer: ${String(seed)}`);
  }
  const outcome = classify(input.tags, compiled, input.origin);
  const preset = compiled.presetById.get(outcome.presetId);
  if (preset === undefined) {
    throw new Error(`ground-material-rules: preset ${outcome.presetId} is not defined`);
  }
  const random = (purpose: string) => randomFrom(compiled.ruleSet.id,
    compiled.ruleSet.version, seed, input.polygonId, purpose);
  const hueShiftDeg = (random("material-hue") * 2 - 1) * preset.variation.hueDeg;
  const valueShift = (random("material-value") * 2 - 1) * preset.variation.value;
  const rotationRad = preset.variation.rotate
    ? random("material-rotation") * Math.PI * 2 : 0;
  const offsetU = random("material-offset-u");
  const offsetV = random("material-offset-v");
  return deepFreeze({
    polygonId: input.polygonId,
    origin: input.origin,
    sourceRefs: { ...input.sourceRefs },
    family: outcome.family,
    preset: preset.id,
    basis: outcome.basis,
    familyRule: outcome.familyRule?.id ?? null,
    presetRule: outcome.presetRule?.id ?? null,
    matchedTags: Object.freeze([...outcome.matchedTags]),
    reason: outcome.reason,
    ruleSetId: compiled.ruleSet.id,
    ruleVersion: compiled.ruleSet.version,
    seed,
    tileSizeM: preset.tileSizeM,
    variation: Object.freeze({
      hueShiftDeg, valueShift, rotationRad, offsetU, offsetV,
    }),
  });
}

/** Builds a material input from a retained source ground cover. */
export function groundMaterialInputFromCover(cover: GroundMaterialCoverLike): GroundMaterialInput {
  const provenance = isRecord(cover.provenance, "cover provenance") as unknown as GroundMaterialCoverLike["provenance"];
  if (provenance.kind !== "osm") {
    throw new Error(`ground-material-rules: ground cover ${String(cover.id)} provenance must be osm`);
  }
  return { polygonId: cover.id, origin: "source", tags: provenance.tags,
    sourceRefs: { sourceSha256: provenance.sourceSha256,
      elementType: provenance.elementType, elementId: provenance.elementId } };
}

/** Authored greens have no source tags; callers supply authored material tags explicitly. */
export function groundMaterialInputFromGreen(green: GroundMaterialGreenLike): GroundMaterialInput {
  const provenance = isRecord(green.provenance, "green provenance") as unknown as GroundMaterialGreenLike["provenance"];
  if (provenance.kind === "authored") {
    return { polygonId: green.id, origin: "authored", tags: {},
      sourceRefs: { designId: provenance.designId } };
  }
  if (provenance.kind !== "osm") {
    throw new Error(`ground-material-rules: green ${String(green.id)} provenance must be osm or authored`);
  }
  return groundMaterialInputFromCover({ id: green.id, provenance });
}

/** Material tags may be supplied only for an authored green. */
export function authoredGroundMaterialInput(green: GroundMaterialGreenLike,
    tags: Readonly<Record<string, string>>): GroundMaterialInput {
  if (green.provenance.kind !== "authored") {
    throw new Error("ground-material-rules: authored material input requires authored green provenance");
  }
  const values = isRecord(tags, "authored tags");
  const prototype = Object.getPrototypeOf(values);
  if ((prototype !== Object.prototype && prototype !== null)
    || Object.values(values).some(value => typeof value !== "string")) {
    throw new Error("ground-material-rules: authored tags must be a plain object of string values");
  }
  return { polygonId: green.id, origin: "authored", tags: { ...tags },
    sourceRefs: { designId: green.provenance.designId } };
}

interface MutableGroupStats { count: number; areaM2: number }

function group(): MutableGroupStats {
  return { count: 0, areaM2: 0 };
}

function accumulate(index: Record<string, MutableGroupStats>, key: string,
    areaM2: number): void {
  const entry = index[key] ?? (index[key] = group());
  entry.count += 1;
  entry.areaM2 += areaM2;
}

/** Counts and area by family, preset and basis, plus the unclassified reasons
 * with area. Areas come from `areaById` keyed by polygon ID; a polygon without
 * a measured area contributes zero. */
export function summarizeGroundMaterialAssignments(
    assignments: readonly GroundMaterialAssignment[],
    areaById: Readonly<Record<string, number>>): GroundMaterialSummary {
  const byFamily: Record<string, MutableGroupStats> = {};
  const byPreset: Record<string, MutableGroupStats> = {};
  const byBasis: Record<string, MutableGroupStats> = {};
  const unclassifiedReasons: Record<string, MutableGroupStats> = {};
  let count = 0;
  let areaM2 = 0;
  for (const assignment of assignments) {
    const area = areaById[assignment.polygonId] ?? 0;
    count += 1;
    areaM2 += area;
    accumulate(byFamily, assignment.family, area);
    accumulate(byPreset, assignment.preset, area);
    accumulate(byBasis, assignment.basis, area);
    if (assignment.family === UNKNOWN && assignment.reason !== null) {
      accumulate(unclassifiedReasons, assignment.reason, area);
    }
  }
  return { count, areaM2, byFamily, byPreset, byBasis, unclassifiedReasons };
}
