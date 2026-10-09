import { describe, expect, it } from 'vitest';
import * as T from 'three';
import { createCityVegetation } from './city-surfaces';
import type { MeshPackManifest } from './mesh-pack';

// Synthetic geographic fixture: coordinates are converted exactly the way the pack
// contract does (local Mercator around converter_origin, quantized to 1 mm,
// then the declared stored_translation is added).
const ORIGIN = { latitude_deg: 31, longitude_deg: 121 };
const TRANSLATION: [number, number] = [40, -25];
const EXTENT = { west: -100, east: 100, south: -100, north: 100 };

function pack(): MeshPackManifest {
  return {
    schema_version: 'aero-bench.osm2world-mesh-pack/v1',
    source: { asset_id: 'city-pack/source@1', size_bytes: 1 },
    generator: { revision: 'b'.repeat(40) },
    projection: { name: 'MetricMapProjection', axes: 'east-up-south', origin: ORIGIN },
    coordinate_contract: {
      schema_version: 'aero-bench.osm2world-source-coordinates/v1',
      recipe: 'source-node-bounds-local-Mercator-then-declared-origin-translation',
      converter_origin: ORIGIN,
      stored_translation_xz_m: TRANSLATION,
      earth_circumference_m: 40075016.686,
      native_point_quantization_m: 0.001,
      storage: 'source-mesh-float32-converter-coordinates-then-declared-origin-translation',
    },
    extent: EXTENT,
    original_mesh_count: 0,
    objects: [],
    textures: {},
    batches: [],
  };
}

type OsmElement =
  | { type: 'node'; id: number; lat: number; lon: number; tags?: Record<string, string> }
  | { type: 'way'; id: number; nodes: number[]; tags?: Record<string, string> }
  | { type: 'relation'; id: number; tags: Record<string, string>; members: { type: string; ref: number; role: string }[] };

// Park donut: outer ring 95.42 m x 111.32 m in pack space, concentric hole
// 38.168 m x 44.528 m. A residential road crosses the park, a building sits
// inside the green, and three natural=tree nodes are scattered around.
const PARK_OUTER = [
  { id: 101, lat: 30.9995, lon: 120.9995 }, { id: 102, lat: 30.9995, lon: 121.0005 },
  { id: 103, lat: 31.0005, lon: 121.0005 }, { id: 104, lat: 31.0005, lon: 120.9995 },
];
const PARK_HOLE = [
  { id: 111, lat: 30.9998, lon: 120.9998 }, { id: 112, lat: 30.9998, lon: 121.0002 },
  { id: 113, lat: 31.0002, lon: 121.0002 }, { id: 114, lat: 31.0002, lon: 120.9998 },
];
const BUILDING = [
  { id: 121, lat: 30.9999, lon: 120.9999 }, { id: 122, lat: 30.9999, lon: 121.0001 },
  { id: 123, lat: 31.0001, lon: 121.0001 }, { id: 124, lat: 31.0001, lon: 120.9999 },
];
const ROAD = [
  { id: 131, lat: 31.0002, lon: 120.9985 }, { id: 132, lat: 31.0002, lon: 121 },
  { id: 133, lat: 31.0002, lon: 121.0015 },
];
// natural=tree: two inside the extent, one beyond the east extent edge.
const TREES = [
  { id: 141, lat: 31.0001, lon: 120.9993 },
  { id: 142, lat: 30.9999, lon: 121.0007 },
  { id: 143, lat: 31.0004, lon: 120.9996 },
];
const node = (n: { id: number; lat: number; lon: number }, tags?: Record<string, string>): OsmElement =>
  ({ type: 'node', id: n.id, lat: n.lat, lon: n.lon, ...(tags ? { tags } : {}) });
const ringWay = (id: number, corners: { id: number }[]): OsmElement =>
  ({ type: 'way', id, nodes: [...corners.map(n => n.id), corners[0].id] });

function cityFixture(): OsmElement[] {
  return [
    ...[...PARK_OUTER, ...PARK_HOLE, ...BUILDING, ...ROAD].map(n => node(n)),
    node(TREES[0], { natural: 'tree' }), node(TREES[1], { natural: 'tree' }), node(TREES[2], { natural: 'tree' }),
    { ...ringWay(201, PARK_OUTER) },
    { ...ringWay(202, PARK_HOLE) },
    { ...ringWay(203, BUILDING), tags: { building: 'yes' } },
    { type: 'way', id: 204, nodes: ROAD.map(n => n.id), tags: { highway: 'residential' } },
    {
      type: 'relation', id: 301, tags: { type: 'multipolygon', leisure: 'park' },
      members: [
        { type: 'way', ref: 201, role: 'outer' },
        { type: 'way', ref: 202, role: 'inner' },
      ],
    },
  ];
}

// Expected pack-space geometry (independent recomputation of the contract).
const expected = (n: { lat: number; lon: number }): [number, number] => {
  const rad = Math.PI / 180;
  const scale = 40075016.686 / (2 * Math.PI) * Math.cos(ORIGIN.latitude_deg * rad);
  const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * rad / 2));
  return [
    Math.round(scale * (n.lon - ORIGIN.longitude_deg) * rad * 1000) / 1000 + TRANSLATION[0],
    Math.round(-scale * (mercY(n.lat) - mercY(ORIGIN.latitude_deg)) * 1000) / 1000 + TRANSLATION[1],
  ];
};
const PARK: [number, number][] = PARK_OUTER.map(expected);
const HOLE: [number, number][] = PARK_HOLE.map(expected);
const BUILDING_BOX = BUILDING.map(expected);

function grassSurface(root: T.Group): { mesh: T.Mesh; material: T.MeshStandardMaterial } {
  const mesh = root.children.find(child => (child as T.Mesh).isMesh && !(child as unknown as { isInstancedMesh?: boolean }).isInstancedMesh
    && (child as T.Mesh).material instanceof T.MeshStandardMaterial
    && ((child as T.Mesh).material as T.MeshStandardMaterial).userData.displaySurface === 'grass');
  if (!mesh) throw new Error('no grass surface mesh in landscape group');
  return { mesh: mesh as T.Mesh, material: (mesh as T.Mesh).material as T.MeshStandardMaterial };
}
function instanced(root: T.Group, geometryType: string): T.InstancedMesh {
  const mesh = root.children.find(child => (child as T.InstancedMesh).isInstancedMesh && (child as T.InstancedMesh).geometry.type === geometryType);
  if (!mesh) throw new Error(`no ${geometryType} instanced mesh in landscape group`);
  return mesh as T.InstancedMesh;
}
function crownPositions(root: T.Group): [number, number][] {
  const crowns = instanced(root, 'IcosahedronGeometry');
  const position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
  return Array.from({ length: crowns.count }, (_, i) => {
    const matrix = new T.Matrix4();
    crowns.getMatrixAt(i, matrix);
    matrix.decompose(position, quaternion, scale);
    return [position.x, position.z] as [number, number];
  });
}
function triangleAreaSumXZ(mesh: T.Mesh): number {
  const position = mesh.geometry.getAttribute('position') as T.BufferAttribute;
  let area = 0;
  for (let i = 0; i < position.count; i += 3) {
    const ax = position.getX(i), az = position.getZ(i);
    const bx = position.getX(i + 1), bz = position.getZ(i + 1);
    const cx = position.getX(i + 2), cz = position.getZ(i + 2);
    area += Math.abs((bx - ax) * (cz - az) - (cx - ax) * (bz - az)) / 2;
  }
  return area;
}

describe('createCityVegetation', () => {
  it('converts landuse source nodes with converter_origin and stored_translation into expected pack space', () => {
    const root = createCityVegetation({ elements: cityFixture() }, pack());
    expect(root.name).toBe('display-osm-landscape');
    const { mesh, material } = grassSurface(root);
    const position = mesh.geometry.getAttribute('position') as T.BufferAttribute;
    const xs = Array.from({ length: position.count }, (_, i) => position.getX(i));
    const zs = Array.from({ length: position.count }, (_, i) => position.getZ(i));
    // Ring corners land exactly where the pack contract recipe puts them:
    // local Mercator around converter_origin plus stored_translation_xz_m.
    // (5-digit tolerance absorbs the declared float32 vertex storage only.)
    expect(Math.min(...xs)).toBeCloseTo(Math.min(...PARK.map(p => p[0])), 5);
    expect(Math.max(...xs)).toBeCloseTo(Math.max(...PARK.map(p => p[0])), 5);
    expect(Math.min(...zs)).toBeCloseTo(Math.min(...PARK.map(p => p[1])), 5);
    expect(Math.max(...zs)).toBeCloseTo(Math.max(...PARK.map(p => p[1])), 5);
    // The translation is genuinely applied: without it the west edge would be -47.71.
    expect(Math.min(...xs)).toBeCloseTo(-7.71, 5);
    expect(material.userData.displayEstimate).toMatch(/procedural finish/);
    root.traverse(child => (child as T.Mesh).geometry?.dispose?.());
  });

  it('retains natural=tree observations in the merged vegetation set', () => {
    const root = createCityVegetation({ elements: cityFixture() }, pack());
    expect(root.userData.source).toBe('verified-original-osm');
    expect(root.userData.observedTrees).toBe(2); // the second tree lies beyond extent.east
    expect(root.userData.estimatedTrees).toBeGreaterThan(0);
    const crowns = instanced(root, 'IcosahedronGeometry');
    expect(crowns.count).toBe(root.userData.observedTrees + root.userData.estimatedTrees);
    const placed = crownPositions(root);
    for (const observed of [TREES[0], TREES[2]].map(expected)) {
      expect(placed.some(([x, z]) => Math.abs(x - observed[0]) < 0.01 && Math.abs(z - observed[1]) < 0.01)).toBe(true);
    }
    root.traverse(child => (child as T.Mesh).geometry?.dispose?.());
  });

  it('triangulates a multipolygon park donut without filling the hole', () => {
    const root = createCityVegetation({ elements: cityFixture() }, pack());
    expect(root.userData.surfaceCount).toBe(1);
    const { mesh } = grassSurface(root);
    expect(mesh.userData.displayDecoration).toBe(true);
    const position = mesh.geometry.getAttribute('position') as T.BufferAttribute;
    expect(position.count % 3).toBe(0); // raw triangle soup, no indices
    const holeCenter: [number, number] = expected({ lat: 30.99995, lon: 121.00005 });
    // Hole vertices remain on the boundary; the area assertion below detects filling.
    const cornersOf = (i: number): [number, number][] =>
      [[position.getX(i), position.getZ(i)], [position.getX(i + 1), position.getZ(i + 1)], [position.getX(i + 2), position.getZ(i + 2)]];
    for (let i = 0; i < position.count; i += 3) {
      const corners = cornersOf(i);
      for (const [x, z] of corners) {
        expect(Math.hypot(x - holeCenter[0], z - holeCenter[1])).toBeGreaterThan(19.1);
      }
    }
    // Total triangle area must equal the outer polygon area minus the hole.
    const ringArea = (ring: [number, number][]) => {
      let area = 0;
      for (let i = 0; i < ring.length - 1; i++) area += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
      return area / 2;
    };
    const expectedArea = Math.abs(ringArea([...PARK, PARK[0]])) - Math.abs(ringArea([...HOLE, HOLE[0]]));
    expect(triangleAreaSumXZ(mesh)).toBeCloseTo(expectedArea, 3);
    root.traverse(child => (child as T.Mesh).geometry?.dispose?.());
  });

  it('keeps estimated trees out of building footprints and road corridors', () => {
    const root = createCityVegetation({ elements: cityFixture() }, pack());
    expect(root.userData.estimatedTrees).toBeGreaterThan(0);
    const placed = crownPositions(root);
    const insideBuilding = ([x, z]: [number, number]) =>
      x > Math.min(...BUILDING_BOX.map(p => p[0])) + 0.01 && x < Math.max(...BUILDING_BOX.map(p => p[0])) - 0.01
      && z > Math.min(...BUILDING_BOX.map(p => p[1])) + 0.01 && z < Math.max(...BUILDING_BOX.map(p => p[1])) - 0.01;
    expect(placed.some(insideBuilding)).toBe(false);
    // residential estimate 6.4 m wide -> halfWidth 3.2 m plus a 1.8 m keep-out margin
    expect(placed.some(([, z]) => Math.abs(z - expected(ROAD[1])[1]) < 4.99)).toBe(false);
    for (let i = 0; i < placed.length; i++) for (let j = i + 1; j < placed.length; j++) {
      expect(Math.hypot(placed[i][0] - placed[j][0], placed[i][1] - placed[j][1])).toBeGreaterThanOrEqual(3.99);
    }
    root.traverse(child => (child as T.Mesh).geometry?.dispose?.());
  });

  it('fails closed on invalid source references and malformed input', () => {
    const manifest = pack();
    expect(() => createCityVegetation(null, manifest)).toThrow(/original OSM elements/);
    expect(() => createCityVegetation({ elements: [] }, manifest)).toThrow(/no geographic nodes/);
    expect(() => createCityVegetation({ elements: [{ type: 'node', id: 1, lat: Number.NaN, lon: 121 }] }, manifest)).toThrow(/Invalid OSM node coordinate/);
    expect(() => createCityVegetation({
      elements: [
        node({ id: 1, lat: 31, lon: 121 }),
        { type: 'way', id: 9, nodes: [1, 2] },
      ],
    }, manifest)).toThrow(/absent node 2/);
    expect(() => createCityVegetation({
      elements: [
        node({ id: 1, lat: 31, lon: 121 }), node({ id: 2, lat: 31.0001, lon: 121 }),
        {
          type: 'relation', id: 8, tags: { type: 'multipolygon', leisure: 'park' },
          members: [{ type: 'way', ref: 77, role: 'outer' }],
        },
      ],
    }, manifest)).toThrow(/absent way 77/);
  });
});
