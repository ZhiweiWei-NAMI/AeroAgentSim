const SURFACE_DOMAIN = new TextEncoder().encode("AERO-BENCH-DISPLAY-SURFACE\0v1\0");
const UINT32_MAX = 0xffff_ffff;

export type DisplaySurfacePoint = readonly [x: number, z: number];

export interface DisplaySurfacePolygon {
  readonly outline: readonly DisplaySurfacePoint[];
  readonly holes: readonly (readonly DisplaySurfacePoint[])[];
}

function assertArray(value: unknown, label: string): asserts value is readonly unknown[] {
  if (!Array.isArray(value)) throw new TypeError(`${label} must be an array`);
}

function assertCount(value: number, label: string): void {
  if (!Number.isSafeInteger(value) || value < 0 || value > UINT32_MAX) {
    throw new RangeError(`${label} count must fit in an unsigned 32-bit integer`);
  }
}

function pointCoordinates(value: unknown, label: string): readonly [number, number] {
  assertArray(value, label);
  if (value.length !== 2) throw new TypeError(`${label} must contain exactly [x, z]`);
  const x = value[0];
  const z = value[1];
  if (typeof x !== "number" || !Number.isFinite(x) || typeof z !== "number" || !Number.isFinite(z)) {
    throw new TypeError(`${label} coordinates must be finite numbers`);
  }
  return [Object.is(x, -0) ? 0 : x, Object.is(z, -0) ? 0 : z];
}

function validateRing(value: unknown, label: string): readonly unknown[] {
  assertArray(value, label);
  if (value.length < 3) throw new RangeError(`${label} must contain at least three points`);
  assertCount(value.length, `${label} point`);
  for (let index = 0; index < value.length; index += 1) pointCoordinates(value[index], `${label}[${index}]`);
  return value;
}

function checkedPolygons(value: readonly DisplaySurfacePolygon[], label: string): readonly DisplaySurfacePolygon[] {
  assertArray(value, label);
  assertCount(value.length, `${label} polygon`);
  for (let index = 0; index < value.length; index += 1) {
    const polygon: unknown = value[index];
    if (polygon === null || typeof polygon !== "object" || Array.isArray(polygon)) {
      throw new TypeError(`${label}[${index}] must be a polygon object`);
    }
    const fields = polygon as { readonly outline?: unknown; readonly holes?: unknown };
    validateRing(fields.outline, `${label}[${index}].outline`);
    assertArray(fields.holes, `${label}[${index}].holes`);
    assertCount(fields.holes.length, `${label}[${index}] hole`);
    for (let holeIndex = 0; holeIndex < fields.holes.length; holeIndex += 1) {
      validateRing(fields.holes[holeIndex], `${label}[${index}].holes[${holeIndex}]`);
    }
  }
  return value;
}

function serializedSize(polygons: readonly DisplaySurfacePolygon[]): number {
  let size = 4;
  for (const polygon of polygons) {
    size += 8 + polygon.outline.length * 16;
    for (const hole of polygon.holes) size += 4 + hole.length * 16;
  }
  if (!Number.isSafeInteger(size)) throw new RangeError("surface identity stream is too large");
  return size;
}

function writeRing(view: DataView, offset: number, points: readonly unknown[]): number {
  view.setUint32(offset, points.length, true);
  offset += 4;
  for (let index = 0; index < points.length; index += 1) {
    const [x, z] = pointCoordinates(points[index], `surface point ${index}`);
    view.setFloat64(offset, x, true);
    view.setFloat64(offset + 8, z, true);
    offset += 16;
  }
  return offset;
}

function writePolygons(view: DataView, offset: number, polygons: readonly DisplaySurfacePolygon[]): number {
  view.setUint32(offset, polygons.length, true);
  offset += 4;
  for (const polygon of polygons) {
    offset = writeRing(view, offset, polygon.outline);
    view.setUint32(offset, polygon.holes.length, true);
    offset += 4;
    for (const hole of polygon.holes) offset = writeRing(view, offset, hole);
  }
  return offset;
}

/**
 * Hashes the ordered roadbed and walkbed polygons using the shared Python/TS
 * binary contract. Coordinates are x/z metres and are serialized without rounding.
 */
export async function displaySurfaceSha256(
  roadbed: readonly DisplaySurfacePolygon[],
  walkbed: readonly DisplaySurfacePolygon[],
): Promise<string> {
  const checkedRoadbed = checkedPolygons(roadbed, "roadbed");
  const checkedWalkbed = checkedPolygons(walkbed, "walkbed");
  const byteLength = SURFACE_DOMAIN.length + serializedSize(checkedRoadbed) + serializedSize(checkedWalkbed);
  const bytes = new Uint8Array(byteLength);
  bytes.set(SURFACE_DOMAIN);
  const view = new DataView(bytes.buffer);
  const offset = writePolygons(view, SURFACE_DOMAIN.length, checkedRoadbed);
  writePolygons(view, offset, checkedWalkbed);
  if (globalThis.crypto?.subtle === undefined) throw new Error("Web Crypto is required for surface identity");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}
