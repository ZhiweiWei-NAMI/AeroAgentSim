/**
 * Local ENU ↔ WGS84 conversion — the exact formula the scene compiler uses
 * (src/aeroagentsim/authoring/scene.py: WGS84 geodetic radii at the origin latitude).
 * east  = (lon − lon0) · rad · a / sqrt(1 − e2·sin²(lat0))
 * north = (lat − lat0) · rad · a · (1 − e2) / (1 − e2·sin²(lat0))^1.5
 * This is the projection behind C10 preview.geometry and compiled scene roads, so
 * every map coordinate here round-trips with what the server writes.
 */
export interface EnuOrigin { lat: number; lon: number; alt: number }

const RAD = Math.PI / 180, A = 6378137, E2 = 6.69437999014e-3;

/** Metres per radian of east/lon and north/lat at the origin latitude. */
function metresPerRadian(originLat: number): { east: number; north: number } {
  const denominator = 1 - E2 * Math.sin(originLat * RAD) ** 2;
  return {
    east: A / Math.sqrt(denominator) * Math.cos(originLat * RAD),
    north: A * (1 - E2) / denominator ** 1.5,
  };
}

/** WGS84 degrees → local ENU metres relative to `origin` (alt is carried through, not used). */
export function lonLatToEnu(lon: number, lat: number, origin: EnuOrigin): { east: number; north: number; up: number } {
  if (![lon, lat, origin.lat, origin.lon].every(Number.isFinite)) return { east: NaN, north: NaN, up: NaN };
  const scale = metresPerRadian(origin.lat);
  return {
    east: (lon - origin.lon) * RAD * scale.east,
    north: (lat - origin.lat) * RAD * scale.north,
    up: Number.isFinite(origin.alt) ? origin.alt : 0,
  };
}

/** Local ENU metres → WGS84 degrees; the exact inverse of `lonLatToEnu`. */
export function enuToLonLat(east: number, north: number, origin: EnuOrigin): { lat: number; lon: number } {
  if (![east, north, origin.lat, origin.lon].every(Number.isFinite)) return { lat: NaN, lon: NaN };
  const scale = metresPerRadian(origin.lat);
  return {
    lon: origin.lon + east / (RAD * scale.east),
    lat: origin.lat + north / (RAD * scale.north),
  };
}
