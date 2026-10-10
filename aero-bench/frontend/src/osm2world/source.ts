import type { PublicScenario } from "../generated/aero-bench-contracts";
import { converterOrigin, projectGeographic } from "./projection";

export type OsmBuildingKind = "residential" | "commercial" | "office" | "industrial" | "public" | "logistics";
export type OsmEntityKind = "uav" | "ugv" | "pedestrian" | "static_asset";

export interface OsmNode {
  readonly type: "node";
  readonly id: number;
  readonly lat: number;
  readonly lon: number;
  readonly tags?: Readonly<Record<string, string>>;
}
export interface OsmWay {
  readonly type: "way";
  readonly id: number;
  readonly nodes: readonly number[];
  readonly tags?: Readonly<Record<string, string>>;
}
export interface OsmRelationMember {
  readonly type: "node" | "way" | "relation";
  readonly ref: number;
  readonly role: string;
}
export interface OsmRelation {
  readonly type: "relation";
  readonly id: number;
  readonly members: readonly OsmRelationMember[];
  readonly tags?: Readonly<Record<string, string>>;
}
export type OsmElement = OsmNode | OsmWay | OsmRelation;
export interface OsmBounds {
  readonly minlat: number;
  readonly minlon: number;
  readonly maxlat: number;
  readonly maxlon: number;
}
export interface OsmJson {
  readonly version: 0.6;
  readonly generator?: string;
  readonly bounds?: OsmBounds;
  readonly elements: readonly OsmElement[];
}

export interface OsmTrafficSignal {
  readonly id: string;
  readonly junction_id: string;
  readonly east_m: number;
  readonly north_m: number;
  readonly cycle_s: number;
  readonly phase_offset_s: number;
  readonly phases: readonly { readonly state: string; readonly duration_s: number }[];
  readonly controlled_road_ids: readonly string[];
}
export interface OsmFixture {
  readonly id: string;
  readonly kind: OsmEntityKind;
  readonly east_m: number;
  readonly north_m: number;
  readonly up_m: number;
  readonly heading_deg: number;
  readonly color: string;
}
export interface OsmOverlay {
  readonly schema_version: "aero-bench.osm2world-overlay/v1";
  readonly name: string;
  readonly traffic_signals: readonly OsmTrafficSignal[];
  readonly fixtures: readonly OsmFixture[];
  readonly provenance: { readonly source_dataset: string; readonly offline: true };
}
export interface Osm2WorldSource {
  readonly schema_version: "aero-bench.osm2world-source/v2";
  readonly name: string;
  readonly generator: { readonly name: "OSM2World"; readonly version: string; readonly style: string };
  readonly coordinate_frame: "WGS84+ENU";
  readonly units: "deg+m";
  readonly origin_wgs84: { readonly latitude_deg: number; readonly longitude_deg: number };
  readonly osm: OsmJson;
  readonly overlay: OsmOverlay;
  readonly provenance: { readonly source_dataset: string; readonly license: string; readonly attribution: string; readonly offline: true };
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`OSM2World ${label} must be an object`);
  return value as Record<string, unknown>;
}
function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`OSM2World ${label} must be finite`);
  return value;
}
function nonEmpty(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`OSM2World ${label} must be non-empty`);
  return value;
}
function parseTags(value: unknown, label: string): Readonly<Record<string, string>> | undefined {
  if (value === undefined) return undefined;
  const tags = record(value, label);
  const result: Record<string, string> = {};
  for (const [key, tag] of Object.entries(tags)) result[key] = nonEmpty(tag, `${label}.${key}`);
  return result;
}

export function parseOsmJson(value: unknown): OsmJson {
  const root = record(value, "OSM JSON");
  if (root.version !== 0.6 || !Array.isArray(root.elements)) throw new Error("OSM2World requires standard OSM JSON 0.6");
  const elements: OsmElement[] = root.elements.map((raw, index) => {
    const item = record(raw, `element[${index}]`);
    const type = item.type;
    const idValue = finite(item.id, `element[${index}].id`);
    if (!Number.isInteger(idValue)) throw new Error(`OSM2World element[${index}] id must be an integer`);
    if (type === "node") {
      return { type, id: idValue, lat: finite(item.lat, `node[${index}].lat`), lon: finite(item.lon, `node[${index}].lon`), tags: parseTags(item.tags, `node[${index}].tags`) };
    }
    if (type === "way") {
      if (!Array.isArray(item.nodes) || item.nodes.length < 2 || item.nodes.some((node) => typeof node !== "number" || !Number.isInteger(node))) throw new Error(`OSM2World way[${index}] nodes are invalid`);
      return { type, id: idValue, nodes: [...item.nodes] as number[], tags: parseTags(item.tags, `way[${index}].tags`) };
    }
    if (type === "relation") {
      if (!Array.isArray(item.members)) throw new Error(`OSM2World relation[${index}] members are invalid`);
      const members = item.members.map((rawMember, memberIndex) => {
        const member = record(rawMember, `relation[${index}].member[${memberIndex}]`);
        const memberType = member.type;
        if (memberType !== "node" && memberType !== "way" && memberType !== "relation") throw new Error(`OSM2World relation member type is invalid`);
        const ref = finite(member.ref, `relation[${index}].member[${memberIndex}].ref`);
        if (!Number.isInteger(ref)) throw new Error(`OSM2World relation member ref must be an integer`);
        return { type: memberType as OsmRelationMember["type"], ref, role: typeof member.role === "string" ? member.role : "" };
      });
      return { type, id: idValue, members, tags: parseTags(item.tags, `relation[${index}].tags`) };
    }
    throw new Error(`OSM2World element[${index}] has unsupported type`);
  });
  const nodes = elements.filter((element): element is OsmNode => element.type === "node");
  const ways = elements.filter((element): element is OsmWay => element.type === "way");
  const nodeIds = new Set(nodes.map((node) => node.id));
  for (const way of ways) {
    for (let index = 0; index < way.nodes.length; index += 1) if (!nodeIds.has(way.nodes[index]!)) throw new Error(`OSM2World way ${way.id} references missing node ${way.nodes[index]}`);
  }
  const elementIds = new Set(elements.map(element => `${element.type}:${element.id}`));
  if (elementIds.size !== elements.length) throw new Error("OSM2World element ids must be unique");
  for (const element of elements) if (element.type === "relation") {
    for (const member of element.members) if (!elementIds.has(`${member.type}:${member.ref}`)) throw new Error(`OSM2World relation ${element.id} references missing ${member.type} ${member.ref}`);
  }
  let bounds: OsmBounds | undefined;
  if (root.bounds !== undefined) {
    const rawBounds = record(root.bounds, "bounds");
    bounds = {
      minlat: finite(rawBounds.minlat, "bounds.minlat"),
      minlon: finite(rawBounds.minlon, "bounds.minlon"),
      maxlat: finite(rawBounds.maxlat, "bounds.maxlat"),
      maxlon: finite(rawBounds.maxlon, "bounds.maxlon"),
    };
    if (bounds.minlat >= bounds.maxlat || bounds.minlon >= bounds.maxlon) throw new Error("OSM2World bounds are inverted");
  }
  return { version: 0.6, generator: typeof root.generator === "string" ? root.generator : undefined, bounds, elements };
}

export const OSM2WORLD_GENERATOR = { name: "OSM2World", version: "0.5.0-e58e986-aero1", style: "OSM2World-default-style" } as const;

const highwayTags: Readonly<Record<string, Readonly<Record<string, string>>>> = {
  vehicle_lane: { highway: "residential" },
  pedestrian_path: { highway: "footway" },
  service_road: { highway: "service" },
  runway: { aeroway: "runway" },
  taxiway: { aeroway: "taxiway" },
};

const scenarioSources = new WeakMap<PublicScenario, Osm2WorldSource>();

export function sourceFromScenario(scenario: PublicScenario, sceneOsm?: OsmJson): Osm2WorldSource {
  const cached = scenarioSources.get(scenario);
  if (cached !== undefined && (sceneOsm === undefined || cached.osm === sceneOsm)) return cached;
  const elements: OsmElement[] = [];
  const nodeIds = new Map<string, number>();
  let wayId = -1;
  const nodeFor = (latitude: number, longitude: number): number => {
    const key = `${latitude}:${longitude}`;
    const existing = nodeIds.get(key);
    if (existing !== undefined) return existing;
    const id = -(nodeIds.size + 1);
    nodeIds.set(key, id);
    elements.push({ type: "node", id, lat: latitude, lon: longitude });
    return id;
  };
  for (const road of scenario.roads) {
    const nodes = road.terrain_points.map(point => nodeFor(point.wgs84.latitude_deg, point.wgs84.longitude_deg));
    elements.push({ type: "way", id: wayId--, nodes, tags: { ...highwayTags[road.kind], width: String(road.width_m), "aero:road_id": road.road_id } });
  }
  for (const building of scenario.buildings) {
    const nodes = building.base_vertices.map(point => nodeFor(point.wgs84.latitude_deg, point.wgs84.longitude_deg));
    if (nodes[0] !== nodes.at(-1)) nodes.push(nodes[0]!);
    const base = Math.min(...building.base_vertices.map(point => point.enu.up_m));
    const top = Math.max(...building.top_vertices.map(point => point.enu.up_m));
    elements.push({ type: "way", id: wayId--, nodes, tags: { building: "yes", height: String(top - base), "aero:building_id": building.building_id } });
  }
  // An empty operational world still declares its geodetic origin; do not load another city.
  if (elements.length === 0) nodeFor(scenario.frame_authority.origin.wgs84.latitude_deg, scenario.frame_authority.origin.wgs84.longitude_deg);
  const osm: OsmJson = sceneOsm ?? { version: 0.6, generator: "AERO-BENCH PublicScenario", elements };
  const origin = converterOrigin(osm);
  const colors: Record<OsmEntityKind, string> = { uav: "#22d3ee", ugv: "#e8a44c", pedestrian: "#2f7d4a", static_asset: "#9a6842" };
  const fixtures: OsmFixture[] = scenario.entities.map(entity => {
    const position = entity.initial_pose.position;
    const projected = projectGeographic(position.wgs84.latitude_deg, position.wgs84.longitude_deg, origin);
    return { id: entity.entity_id, kind: entity.kind, east_m: projected.east, north_m: projected.north, up_m: position.enu.up_m, heading_deg: 0, color: colors[entity.kind] };
  });
  const source: Osm2WorldSource = { schema_version: "aero-bench.osm2world-source/v2", name: `${scenario.world_id} OSM2World`, generator: OSM2WORLD_GENERATOR, coordinate_frame: "WGS84+ENU", units: "deg+m", origin_wgs84: origin, osm, overlay: { schema_version: "aero-bench.osm2world-overlay/v1", name: `${scenario.world_id} operational overlay`, traffic_signals: [], fixtures, provenance: { source_dataset: "AERO-BENCH PublicScenario", offline: true } }, provenance: { source_dataset: "AERO-BENCH PublicScenario", license: "AERO-BENCH sealed run", attribution: scenario.world_id, offline: true } };
  scenarioSources.set(scenario, source);
  return source;
}
