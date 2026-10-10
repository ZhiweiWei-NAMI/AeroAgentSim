import * as THREE from "three";

export const CITY_FACADE_PANE_PROFILE_KEY = "authoredFacadePaneProfile";
export const CITY_BUILDING_MATERIAL_ROLE_KEY = "authoredBuildingMaterialRole";
export type CityFacadeMaterialKind = "glass-and-masonry" | "masonry-and-windows";
export type CityFacadePixelRectangle = readonly [number, number, number, number];
export type CityFacadePixelPoint = readonly [number, number];

/** Authored interpretation of source texture pixels, never surveyed material truth. */
export interface CityFacadePaneProfile {
  readonly schema_version: "aero-bench.authored-facade-pane-profile/v1";
  readonly provenance: "source-texture-art";
  readonly style_id: string;
  readonly material_kind: CityFacadeMaterialKind;
  readonly coordinate_system: "image-top-left-pixel";
  readonly tile_size_px: readonly [number, number];
  readonly source_crop_px: CityFacadePixelRectangle;
  readonly glass_rectangles_px: readonly CityFacadePixelRectangle[];
  readonly opaque_polygons_px: readonly (readonly CityFacadePixelPoint[])[];
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Authored facade pane profile must be an object");
  }
  return value as Record<string, unknown>;
}

function numbers(value: unknown, count: number, label: string): number[] {
  if (!Array.isArray(value) || value.length !== count
      || !value.every(item => typeof item === "number" && Number.isFinite(item))) {
    throw new Error(`Authored facade ${label} must contain ${count} finite numbers`);
  }
  return [...value] as number[];
}

export function readCityFacadePaneProfile(value: unknown): CityFacadePaneProfile {
  const input = record(value);
  if (input.schema_version !== "aero-bench.authored-facade-pane-profile/v1"
      || input.provenance !== "source-texture-art" || input.coordinate_system !== "image-top-left-pixel"
      || typeof input.style_id !== "string" || !input.style_id.trim()
      || (input.material_kind !== "glass-and-masonry" && input.material_kind !== "masonry-and-windows")) {
    throw new Error("Authored facade pane profile identity or material kind is invalid");
  }
  const size = numbers(input.tile_size_px, 2, "tile size") as [number, number];
  if (!size.every(item => Number.isSafeInteger(item) && item > 0)) {
    throw new Error("Authored facade tile size must be positive integer pixels");
  }
  const crop = numbers(input.source_crop_px, 4, "source crop") as [number, number, number, number];
  if (!crop.every(item => Number.isSafeInteger(item) && item >= 0)
      || crop[2] - crop[0] !== size[0] || crop[3] - crop[1] !== size[1]) {
    throw new Error("Authored facade source crop must match its tile size");
  }
  if (!Array.isArray(input.glass_rectangles_px) || input.glass_rectangles_px.length === 0
      || !Array.isArray(input.opaque_polygons_px)) {
    throw new Error("Authored facade must declare pane rectangles and opaque polygons");
  }
  const panes = input.glass_rectangles_px.map(value => {
    const pane = numbers(value, 4, "pane rectangle") as [number, number, number, number];
    const [left, top, right, bottom] = pane;
    if (left < 0 || top < 0 || right > size[0] || bottom > size[1] || left >= right || top >= bottom) {
      throw new Error("Authored facade pane rectangle is outside its active tile");
    }
    return pane;
  });
  const opaque = input.opaque_polygons_px.map(value => {
    if (!Array.isArray(value) || value.length < 3) throw new Error("Authored opaque polygon needs at least three points");
    const points = value.map(point => {
      const pair = numbers(point, 2, "opaque polygon point") as [number, number];
      if (pair[0] < 0 || pair[1] < 0 || pair[0] > size[0] || pair[1] > size[1]) {
        throw new Error("Authored opaque polygon is outside its active tile");
      }
      return pair;
    });
    const area = points.reduce((sum, point, index) => {
      const next = points[(index + 1) % points.length]!;
      return sum + point[0] * next[1] - next[0] * point[1];
    }, 0);
    if (Math.abs(area) < 1e-8) throw new Error("Authored opaque polygon has zero area");
    return points;
  });
  return { schema_version: input.schema_version, provenance: input.provenance,
    style_id: input.style_id, material_kind: input.material_kind,
    coordinate_system: input.coordinate_system, tile_size_px: size, source_crop_px: crop,
    glass_rectangles_px: panes, opaque_polygons_px: opaque };
}

export function cityMaterialPaneProfile(material: THREE.Material): CityFacadePaneProfile | null {
  const value: unknown = material.userData[CITY_FACADE_PANE_PROFILE_KEY];
  return value === undefined ? null : readCityFacadePaneProfile(value);
}

export function cityBuildingMaterialRole(material: THREE.Material): "opaque-roof" | null {
  const value: unknown = material.userData[CITY_BUILDING_MATERIAL_ROLE_KEY];
  if (value === undefined) return null;
  if (value !== "opaque-roof") throw new Error("Unknown authored building material role");
  return value;
}

function insidePolygon(x: number, y: number, polygon: readonly CityFacadePixelPoint[]): boolean {
  let inside = false;
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const [ax, ay] = polygon[index]!, [bx, by] = polygon[previous]!;
    if ((ay > y) !== (by > y) && x < (bx - ax) * (y - ay) / (by - ay) + ax) inside = !inside;
  }
  return inside;
}

/** Rasterize all declared panes; illumination and albedo brightness cannot remove glazing. */
export function rasterCityFacadePaneRoughness(profile: CityFacadePaneProfile): Uint8Array {
  const [width, height] = profile.tile_size_px;
  const rough = Math.round(255 * 0.88);
  const glass = Math.round(255 * (profile.material_kind === "glass-and-masonry" ? 0.19 : 0.28));
  const data = new Uint8Array(width * height * 4);
  for (let offset = 0; offset < data.length; offset += 4) {
    data[offset] = 255; data[offset + 1] = rough; data[offset + 2] = 0; data[offset + 3] = 255;
  }
  for (const [left, top, right, bottom] of profile.glass_rectangles_px) {
    for (let y = Math.max(0, Math.ceil(top - 0.5)); y < Math.min(height, Math.ceil(bottom - 0.5)); y++) {
      for (let x = Math.max(0, Math.ceil(left - 0.5)); x < Math.min(width, Math.ceil(right - 0.5)); x++) {
        data[(y * width + x) * 4 + 1] = glass;
      }
    }
  }
  for (const polygon of profile.opaque_polygons_px) {
    const left = Math.max(0, Math.floor(Math.min(...polygon.map(point => point[0]))));
    const top = Math.max(0, Math.floor(Math.min(...polygon.map(point => point[1]))));
    const right = Math.min(width, Math.ceil(Math.max(...polygon.map(point => point[0]))));
    const bottom = Math.min(height, Math.ceil(Math.max(...polygon.map(point => point[1]))));
    for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
      if (insidePolygon(x + 0.5, y + 0.5, polygon)) data[(y * width + x) * 4 + 1] = rough;
    }
  }
  return data;
}
