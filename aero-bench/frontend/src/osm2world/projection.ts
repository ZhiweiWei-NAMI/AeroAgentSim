import type { OsmJson, OsmNode } from "./source";

export interface GeographicOrigin { readonly latitude_deg: number; readonly longitude_deg: number; }

const EARTH_CIRCUMFERENCE = 40075016.686;
const radians = Math.PI / 180;
function mercatorY(latitude: number): number {
  const sine = Math.sin(latitude * radians);
  return Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI) + 0.5;
}

// OSM2World MetricMapProjection. The web JSON reader derives its origin from
// all node bounds, not the top-level extract bounds, including outside way nodes.
export function converterOrigin(osm: OsmJson): GeographicOrigin {
  let minLat = Infinity, minLon = Infinity, maxLat = -Infinity, maxLon = -Infinity;
  for (const element of osm.elements) {
    if (element.type !== "node") continue;
    minLat = Math.min(minLat, element.lat); maxLat = Math.max(maxLat, element.lat);
    minLon = Math.min(minLon, element.lon); maxLon = Math.max(maxLon, element.lon);
  }
  if (!Number.isFinite(minLat)) throw new Error("OSM2World source has no nodes");
  return { latitude_deg: (minLat + maxLat) / 2, longitude_deg: (minLon + maxLon) / 2 };
}

export function projectGeographic(latitude: number, longitude: number, origin: GeographicOrigin): { east: number; north: number } {
  const scale = EARTH_CIRCUMFERENCE * Math.cos(origin.latitude_deg * radians);
  return {
    east: scale * (longitude - origin.longitude_deg) / 360,
    north: scale * (mercatorY(latitude) - mercatorY(origin.latitude_deg)),
  };
}

export function osmExtent(osm: OsmJson, origin: GeographicOrigin): { west: number; east: number; south: number; north: number } {
  const nodes = osm.elements.filter((element): element is OsmNode => element.type === "node");
  const bounds = osm.bounds ?? {
    minlat: Math.min(...nodes.map(node => node.lat)), maxlat: Math.max(...nodes.map(node => node.lat)),
    minlon: Math.min(...nodes.map(node => node.lon)), maxlon: Math.max(...nodes.map(node => node.lon)),
  };
  const southwest = projectGeographic(bounds.minlat, bounds.minlon, origin);
  const northeast = projectGeographic(bounds.maxlat, bounds.maxlon, origin);
  return { west: southwest.east, east: northeast.east, south: southwest.north, north: northeast.north };
}
