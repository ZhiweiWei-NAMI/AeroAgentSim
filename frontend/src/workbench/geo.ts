/**
 * Local ENU ↔ WGS84 conversion — the exact formula the scene compiler uses
 * (src/aeroagentsim/authoring/scene.py: WGS84 geodetic radii at the origin latitude).
 * east  = (lon − lon0) · rad · a / sqrt(1 − e2·sin²(lat0)) · cos(lat0)
 * north = (lat − lat0) · rad · a · (1 − e2) / (1 − e2·sin²(lat0))^1.5
 * This is the projection behind C10 preview.geometry and compiled scene roads, so
 * every map coordinate here round-trips with what the server writes.
 */
export interface EnuOrigin { lat: number; lon: number; alt: number }

/** Only ordered, finite WGS84 extents can become a Leaflet rectangle or camera view. */
export function validWgs84Bounds(bounds: number[] | null | undefined): bounds is number[] {
  return !!bounds && bounds.length === 4 && bounds.every(Number.isFinite)
    && bounds[0] >= -180 && bounds[2] <= 180 && bounds[1] > -90 && bounds[3] < 90
    && bounds[0] < bounds[2] && bounds[1] < bounds[3];
}

export function validEnuOrigin(origin: EnuOrigin | null | undefined): origin is EnuOrigin {
  return !!origin && [origin.lat, origin.lon, origin.alt].every(Number.isFinite)
    && origin.lat > -90 && origin.lat < 90 && origin.lon >= -180 && origin.lon <= 180;
}

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
  if (![lon, lat].every(Number.isFinite) || !validEnuOrigin(origin)) return { east: NaN, north: NaN, up: NaN };
  const scale = metresPerRadian(origin.lat);
  return {
    east: (lon - origin.lon) * RAD * scale.east,
    north: (lat - origin.lat) * RAD * scale.north,
    up: origin.alt,
  };
}

/** Local ENU metres → WGS84 degrees; the exact inverse of `lonLatToEnu`. */
export function enuToLonLat(east: number, north: number, origin: EnuOrigin): { lat: number; lon: number } {
  if (![east, north].every(Number.isFinite) || !validEnuOrigin(origin)) return { lat: NaN, lon: NaN };
  const scale = metresPerRadian(origin.lat);
  return {
    lon: origin.lon + east / (RAD * scale.east),
    lat: origin.lat + north / (RAD * scale.north),
  };
}
