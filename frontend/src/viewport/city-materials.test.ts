import { describe, expect, it } from 'vitest';
import * as T from 'three';
import type { PackedBatch, PackedMaterial, PackFile, PackedObject, PackedRange } from './mesh-pack';
import { decorateBuildings } from './city-materials';

// Minimal typed fixtures: decorateBuildings only reads batch.ranges and object tags.
const PACK_FILE: PackFile = { asset_id: 'fixture-pack-chunk@1', size_bytes: 128 };
const PACK_MATERIAL: PackedMaterial = {
  color: [1, 1, 1],
  base_color_texture: null,
  normal_texture: null,
  orm_texture: null,
  opacity_texture: null,
  transparent: false,
  clamp: false,
};

function batchOf(ranges: readonly PackedRange[], vertices: number, indices: number): PackedBatch {
  return { file: PACK_FILE, vertices, indices, layer: 'buildings', material: PACK_MATERIAL, ranges };
}

function tagged(id: string, tags: Record<string, string>): PackedObject {
  return { id, tags };
}

function v(x: number, y: number, z: number): T.Vector3 {
  return new T.Vector3(x, y, z);
}

type Triangle = readonly [T.Vector3, T.Vector3, T.Vector3];

function geometryFromTriangles(triangles: readonly Triangle[]): T.BufferGeometry {
  const geometry = new T.BufferGeometry();
  const positions: number[] = [];
  for (const [a, b, c] of triangles) positions.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
  geometry.setAttribute('position', new T.Float32BufferAttribute(positions, 3));
  return geometry;
}

function colorAt(attribute: T.BufferAttribute, index: number): T.Color {
  return new T.Color().fromBufferAttribute(attribute, index);
}

function expectSameColor(actual: T.Color, expected: T.Color): void {
  expect(actual.r).toBeCloseTo(expected.r, 5);
  expect(actual.g).toBeCloseTo(expected.g, 5);
  expect(actual.b).toBeCloseTo(expected.b, 5);
}

/** Wall tint the material applies on top of the resolved tag/fallback color. */
function tinted(base: string, variation: number): T.Color {
  return new T.Color(base).multiplyScalar(0.91 + variation * 0.16);
}

describe('decorateBuildings display attributes', () => {
  it('expands shared indexed vertices so two range targets keep separate tag colors', () => {
    // Two triangles whose source index buffer literally shares corner vertices 1 and 2.
    const geometry = new T.BufferGeometry();
    geometry.setAttribute('position', new T.Float32BufferAttribute([
      0, 0, 0, 0, 12, 0, 6, 12, -4, 0, 0, -8,
    ], 3));
    geometry.setIndex([0, 1, 2, 2, 1, 3]);
    expect(geometry.index).not.toBeNull();
    const objects = [
      tagged('way/a', { building: 'yes', 'building:colour': '#ff0000' }),
      tagged('way/b', { building: 'yes', 'building:colour': '#0000ff' }),
    ];
    const material = decorateBuildings(
      geometry,
      batchOf([
        { end: 1, target: { kind: 'building', id: 'way/a' } },
        { end: 2, target: { kind: 'building', id: 'way/b' } },
      ], 4, 6),
      objects,
      false,
    );

    // Ranges partition triangles, so the shared corners must be expanded apart.
    expect(geometry.index).toBeNull();
    expect((geometry.getAttribute('position') as T.BufferAttribute).count).toBe(6);
    expect(material.userData.taggedRanges).toBe(2);
    const wall = geometry.getAttribute('displayWallColor') as T.BufferAttribute;
    const style = geometry.getAttribute('displayFacadeStyle') as T.BufferAttribute;

    const colorA = colorAt(wall, 0);
    const colorB = colorAt(wall, 3);
    expectSameColor(colorA, tinted('#ff0000', style.getZ(0)));
    expectSameColor(colorB, tinted('#0000ff', style.getZ(3)));
    expect(colorA.equals(colorB)).toBe(false);
    // Every vertex of each triangle carries its own building's tag color, including
    // the expanded copies of the two originally shared corners.
    for (let vertex = 0; vertex < 3; vertex++) expectSameColor(colorAt(wall, vertex), colorA);
    for (let vertex = 3; vertex < 6; vertex++) expectSameColor(colorAt(wall, vertex), colorB);
  });

  it('wall style flags glass from the material tag and scales pitch by building:levels', () => {
    const geometry = geometryFromTriangles([
      [v(0, 0, 0), v(8, 0, -2), v(8, 12, -2)], // 12 m tall: explicit glass tag must win over the height heuristic
      [v(0, 0, 4), v(6, 0, 4), v(6, 3, 4)], // low block without material or levels tags
    ]);
    decorateBuildings(
      geometry,
      batchOf([
        { end: 1, target: { kind: 'building', id: 'way/glass' } },
        { end: 2, target: { kind: 'building', id: 'way/plain' } },
      ], 6, 6),
      [
        tagged('way/glass', { building: 'retail', 'building:material': 'glass', 'building:levels': '4' }),
        tagged('way/plain', { building: 'house' }),
      ],
      false,
    );
    const style = geometry.getAttribute('displayFacadeStyle') as T.BufferAttribute;

    for (let vertex = 0; vertex < 3; vertex++) {
      expect(style.getX(vertex)).toBe(1);
      expect(style.getY(vertex)).toBeCloseTo(12 / 4, 5); // (top - bottom) / levels, not the fallback
      expect(style.getW(vertex)).toBe(0); // no roof:shape tag -> not pitched
    }
    for (let vertex = 3; vertex < 6; vertex++) {
      expect(style.getX(vertex)).toBe(0);
      expect(style.getY(vertex)).toBeCloseTo(3.2, 5); // missing levels -> documented fallback pitch
    }
  });

  it('wall and roof colors respect supplied colour tags', () => {
    const geometry = geometryFromTriangles([
      [v(0, 0, 0), v(10, 0, 0), v(10, 9, 0)],
      [v(0, 0, 5), v(7, 0, 5), v(7, 4, 5)],
    ]);
    decorateBuildings(
      geometry,
      batchOf([
        { end: 1, target: { kind: 'building', id: 'way/painted' } },
        { end: 2, target: { kind: 'building', id: 'way/alias' } },
      ], 6, 6),
      [
        tagged('way/painted', { 'building:colour': '#2f6f4f', 'roof:colour': '#7a3b2e' }),
        tagged('way/alias', { colour: '#123456' }),
      ],
      false,
    );
    const wall = geometry.getAttribute('displayWallColor') as T.BufferAttribute;
    const roof = geometry.getAttribute('displayRoofColor') as T.BufferAttribute;
    const style = geometry.getAttribute('displayFacadeStyle') as T.BufferAttribute;

    for (let vertex = 0; vertex < 3; vertex++) {
      expectSameColor(colorAt(wall, vertex), tinted('#2f6f4f', style.getZ(vertex)));
      // Roof finish keeps the tagged colour verbatim (no per-building tint multiplier).
      expectSameColor(colorAt(roof, vertex), new T.Color('#7a3b2e'));
    }
    for (let vertex = 3; vertex < 6; vertex++) {
      expectSameColor(colorAt(wall, vertex), tinted('#123456', style.getZ(vertex)));
      expectSameColor(colorAt(roof, vertex), new T.Color('#777f83')); // no roof:colour tag -> fallback
    }
  });

  it('dusk uniform stays mutable without recompilation', () => {
    const duskGeometry = geometryFromTriangles([[v(0, 0, 0), v(4, 0, 0), v(4, 3, 0)]]);
    const material = decorateBuildings(
      duskGeometry,
      batchOf([{ end: 1, target: { kind: 'building', id: 'way/dusk' } }], 3, 3),
      [tagged('way/dusk', {})],
      true,
    );
    expect(material).toBeInstanceOf(T.MeshStandardMaterial);
    const dusk = material.userData.displayDusk as { value: number };
    expect(dusk.value).toBe(1);

    const cacheKeyBefore = material.customProgramCacheKey?.();
    dusk.value = 0;
    expect(material.userData.displayDusk).toBe(dusk); // mutated in place, same uniform object
    expect(dusk.value).toBe(0);
    expect(material.customProgramCacheKey?.()).toBe(cacheKeyBefore); // dusk cannot invalidate the program

    // onBeforeCompile wires that exact object into the program, so later value
    // changes reach the compiled material without any recompilation.
    const shader = {
      uniforms: {} as Record<string, unknown>,
      vertexShader: '#include <begin_vertex>',
      fragmentShader: '#include <color_fragment>',
    };
    material.onBeforeCompile(shader as never, {} as never);
    expect(shader.uniforms.displayDusk).toBe(dusk);

    const dayGeometry = geometryFromTriangles([[v(0, 0, 0), v(4, 0, 0), v(4, 3, 0)]]);
    const dayMaterial = decorateBuildings(
      dayGeometry,
      batchOf([{ end: 1, target: { kind: 'building', id: 'way/day' } }], 3, 3),
      [tagged('way/day', {})],
      false,
    );
    expect((dayMaterial.userData.displayDusk as { value: number }).value).toBe(0);
  });

  it('null trace target still yields usable procedural estimates', () => {
    const geometry = geometryFromTriangles([
      [v(0, 0, 0), v(9, 0, 0), v(9, 6, 0)], // low unattributed block
      [v(0, 0, 2), v(11, 0, 2), v(11, 40, 2)], // tall unattributed tower
    ]);
    const material = decorateBuildings(
      geometry,
      batchOf([{ end: 1, target: null }, { end: 2, target: null }], 6, 6),
      [],
      false,
    );
    expect(material.userData.taggedRanges).toBe(0);
    // Facts vs estimates: the display-estimate disclosure survives without
    // attribution — it still discloses the procedural windows/roof estimate.
    const estimate = String(material.userData.displayEstimate);
    expect(estimate).toMatch(/windows/i);
    expect(estimate).toMatch(/roof/i);
    expect(estimate).toMatch(/geometry/i);

    const wall = geometry.getAttribute('displayWallColor') as T.BufferAttribute;
    const roof = geometry.getAttribute('displayRoofColor') as T.BufferAttribute;
    const style = geometry.getAttribute('displayFacadeStyle') as T.BufferAttribute;
    for (let vertex = 0; vertex < 6; vertex++) {
      const variation = style.getZ(vertex);
      expect(variation).toBeGreaterThanOrEqual(0);
      expect(variation).toBeLessThan(1);
      // Unattributed walls fall back to the seeded gray family — except the
      // tall block, where the height>=28 glass heuristic still applies.
      const glass = style.getX(vertex) === 1;
      const fallback = glass ? '#788a96' : variation > 0.5 ? '#c0bdb5' : '#b3b6b4';
      expectSameColor(colorAt(wall, vertex), tinted(fallback, variation));
      // ...and roofs to the plain fallback, exactly.
      expectSameColor(colorAt(roof, vertex), new T.Color('#777f83'));
      expect(style.getY(vertex)).toBeGreaterThanOrEqual(1.8);
      expect(style.getW(vertex)).toBe(0);
      // Procedural estimate still fires: the tall block gets glazing, the low one not.
      expect(style.getX(vertex)).toBe(vertex < 3 ? 0 : 1);
    }
    // One seeded variation per range keeps the estimate stable within the block.
    expectSameColor(colorAt(wall, 1), colorAt(wall, 0));
    expectSameColor(colorAt(wall, 2), colorAt(wall, 0));
  });
});
