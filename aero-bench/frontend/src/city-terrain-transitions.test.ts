// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { createTerrainTransitionLayer, planTerrainTransitions,
  TerrainTransitionGrid, recoverTerrainTransitionBoundary, TERRAIN_TRANSITIONS_V1, type TerrainTransitionInput,
  type TerrainTransitionSurface } from "./city-terrain-transitions";
import { BIGCITY_ENVIRONMENT_TREES, CITY_ENVIRONMENT_DEFAULTS, generateCityEnvironment,
  parseCityEnvironmentSource, type EnvironmentPoint, type EnvironmentPolygon } from "./city-environment";
import { parseCityGroundCovers, planCityGroundCovers, triangulateEnvironmentPolygon } from "./city-ground-cover";
import { assignCityGroundMaterial } from "./city-terrain-surfaces";
import { groundMaterialInputFromCover, groundMaterialInputFromGreen } from "./city-ground-material-rules";
import { cityVegetationRoadBinding } from "./city-vegetation-layer";
import { cityVegetationInput } from "./city-vegetation-binding";
import { validateCanonicalCityRoadPayload } from "./city-roads";

function rectTriangles(x1: number, z1: number, x2: number, z2: number): EnvironmentPoint[][] {
  return [
    [[x1, z1], [x2, z1], [x2, z2]],
    [[x1, z1], [x2, z2], [x1, z2]],
  ];
}

function rectPolygon(x1: number, z1: number, x2: number, z2: number): EnvironmentPolygon {
  return { outline: [[x1, z1], [x2, z1], [x2, z2], [x1, z2]], holes: [] };
}

function surface(id: string, family: TerrainTransitionSurface["family"],
    triangles: EnvironmentPoint[][], surfaceY = 0): TerrainTransitionSurface {
  return { id, family, surfaceY, triangles };
}

function tree(id: string, x: number, z: number, heightM: number) {
  return {
    id, assetId: "asset", x, z, y: 0, yaw: 0, scale: 1, heightM, envelopeRadiusM: 1,
    provenance: { kind: "authored-green-tree" as const, greenId: "green", designId: "design" },
  };
}

function input(overrides: Partial<TerrainTransitionInput> = {}): TerrainTransitionInput {
  return {
    surfaces: [surface("lawn", "grass", rectTriangles(0, 0, 10, 10))],
    buildings: [], roadbed: [], walkbed: [], trees: [],
    ...overrides,
  };
}

const LINEAR_TINTS = Object.fromEntries(Object.entries(TERRAIN_TRANSITIONS_V1.classes)
  .map(([name, definition]) => {
    const linear = (channel: number) =>
      channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    return [name, definition.tint.map(linear)] as const;
  }));

describe("terrain transition classification", () => {
  it("classifies each side of a 10 m lawn square and keeps the band area exact", () => {
    const plan = planTerrainTransitions(input({
      buildings: [rectPolygon(10, 0, 12, 10)],      // touching the east edge
      walkbed: [rectPolygon(0, -2, 10, 0)],         // along the south edge
      surfaces: [
        surface("lawn", "grass", rectTriangles(0, 0, 10, 10)),
        surface("pond", "water", rectTriangles(-4, 0, 0, 10)),  // along the west edge
      ],
    }));
    expect(plan.stats.byClass.building).toBe(5);   // east: 10 m / 2 m segments
    expect(plan.stats.byClass.paved).toBe(5);      // south
    expect(plan.stats.byClass.water).toBe(5);      // west
    expect(plan.stats.byClass.open).toBe(5);       // north: nothing beyond
    const widths = TERRAIN_TRANSITIONS_V1.classes;
    const expected = widths.building.widthM * 10 + widths.paved.widthM * 10
      + widths.water.widthM * 10 + widths.open.widthM * 10;
    // W2 replaces corner overlaps with bisector joins; each square side loses w².
    expect(plan.stats.bandAreaM2).toBeCloseTo(expected - Object.values(widths)
      .reduce((sum, rule) => sum + rule.widthM ** 2, 0), 6);
    expect(plan.stats.pieces).toBe(20);
    expect(plan.stats.skippedInterior).toBe(16);   // the shared diagonal, both directions
    expect(plan.stats.skippedSoft).toBe(0);
    expect(plan.version).toBe(TERRAIN_TRANSITIONS_V1.version);
    expect(Object.isFrozen(TERRAIN_TRANSITIONS_V1)).toBe(true);
    expect(plan.layers).toHaveLength(1);
  });

  it("reports only skippedInterior pieces on the diagonal of a two-triangle square", () => {
    const plan = planTerrainTransitions(input());
    // The shared diagonal (14.14 m -> 8 segments) is traversed by both triangles.
    expect(plan.stats.skippedInterior).toBe(16);
    // Only the four outer edges remain, all facing unclassified ground.
    expect(plan.stats.pieces).toBe(plan.stats.byClass.open);
    expect(plan.stats.skippedSoft).toBe(0);
  });

  it("skips the shared edge of two adjacent soft squares on both sides", () => {
    const plan = planTerrainTransitions(input({
      surfaces: [
        surface("west-lawn", "grass", rectTriangles(0, 0, 10, 10)),
        surface("east-lawn", "grass", rectTriangles(10, 0, 20, 10)),
      ],
    }));
    // Shared 10 m edge, 5 pieces per triangle, one triangle per side.
    expect(plan.stats.skippedSoft).toBe(10);
    expect(plan.stats.pieces).toBe(30);  // outer edges of both squares, all open
    expect(plan.stats.byClass.open).toBe(30);
  });

  it("draws no band on the interior edges of a T-junction split", () => {
    const triangles: EnvironmentPoint[][] = [
      [[0, 0], [10, 0], [10, 10]],
      [[0, 0], [10, 10], [0, 10]],
      [[0, 0], [0, 10], [-10, 10]],
      [[0, 0], [-10, 10], [-10, 0]],
    ];
    const plan = planTerrainTransitions(input({ surfaces: [surface("lawn", "grass", triangles)] }));
    // Interior edges: two diagonals (8 segments each way) and one vertical (5 each way).
    expect(plan.stats.skippedInterior).toBe(16 + 10 + 16);
    // The six outer edges of the 20 x 10 rectangle.
    expect(plan.stats.byClass.open).toBe(30);
    expect(plan.layers).toHaveLength(1);
    // Every band vertex stays inside the union rectangle.
    const layer = plan.layers[0]!;
    for (let index = 0; index < layer.positions.length; index += 3) {
      const x = layer.positions[index]!, z = layer.positions[index + 2]!;
      expect(x).toBeGreaterThanOrEqual(-10.0000001);
      expect(x).toBeLessThanOrEqual(10.0000001);
      expect(z).toBeGreaterThanOrEqual(-0.0000001);
      expect(z).toBeLessThanOrEqual(10.0000001);
    }
  });

  it("classifies a soft-paved boundary as paved, not open", () => {
    const bare = planTerrainTransitions(input());
    const withPlaza = planTerrainTransitions(input({
      surfaces: [surface("lawn", "grass", rectTriangles(0, 0, 10, 10)),
        surface("plaza", "paving_generic", rectTriangles(10, 0, 20, 10))],
    }));
    expect(bare.stats.byClass.open).toBe(20);
    expect(withPlaza.stats.byClass.open).toBe(15);
    expect(withPlaza.stats.byClass.paved).toBe(5);
  });

  it("classifies a building contact along one edge only", () => {
    const plan = planTerrainTransitions(input({ buildings: [rectPolygon(-2, -2, 0, 12)] }));
    expect(plan.stats.byClass.building).toBe(5);   // west edge
    expect(plan.stats.byClass.open).toBe(15);      // north, east, south
  });

  it("keeps the probe outward for both triangle windings", () => {
    const eastBuilding = [rectPolygon(10, 0, 20, 10)];
    const cw = planTerrainTransitions(input({
      surfaces: [surface("lawn", "grass", [[[0, 0], [10, 10], [10, 0]]])],
      buildings: eastBuilding }));
    const ccw = planTerrainTransitions(input({
      surfaces: [surface("lawn", "grass", [[[0, 0], [10, 0], [10, 10]]])],
      buildings: eastBuilding }));
    // Half-square: hypotenuse (8 pieces) + south (5) open, east (5) building.
    expect(cw.stats.byClass.building).toBe(5);
    expect(ccw.stats.byClass.building).toBe(5);
    expect(cw.stats.byClass.open).toBe(13);
    expect(ccw.stats.byClass.open).toBe(13);
    expect(cw.stats.bandAreaM2).toBeCloseTo(ccw.stats.bandAreaM2, 9);
  });
});

describe("terrain transition clipping", () => {
  it("bands stay inside a concave patch made of many triangles", () => {
    // L-shaped lawn: foot 10 x 4 plus stem 4 x 10; the notch is unclassified ground.
    const triangles: EnvironmentPoint[][] = [
      [[0, 0], [10, 0], [10, 4]], [[0, 0], [10, 4], [0, 4]],
      [[0, 4], [4, 4], [4, 10]], [[0, 4], [4, 10], [0, 10]],
    ];
    const plan = planTerrainTransitions(input({ surfaces: [surface("lawn", "grass", triangles)] }));
    expect(plan.stats.pieces).toBeGreaterThan(0);
    const layer = plan.layers[0]!;
    for (let index = 0; index < layer.positions.length; index += 3) {
      const x = layer.positions[index]!, z = layer.positions[index + 2]!;
      const inFoot = x >= -0.001 && x <= 10.001 && z >= -0.001 && z <= 4.001;
      const inStem = x >= -0.001 && x <= 4.001 && z >= -0.001 && z <= 10.001;
      expect(inFoot || inStem).toBe(true);
    }
  });

  it("clips a tree base disc at the lawn edge and skips non-grass hosts", () => {
    const lawnOnly = planTerrainTransitions(input({
      trees: [tree("edge-tree", 9.5, 5, 5)],   // radius clamp(0.18*5=0.9) -> crosses x=10
    }));
    expect(lawnOnly.stats.treeBases).toBe(1);
    const treeColor = new THREE.Color().setRGB(...TERRAIN_TRANSITIONS_V1.treeBase.tint, THREE.SRGBColorSpace);
    let discArea = 0;
    for (const layer of lawnOnly.layers) for (let i = 0; i < layer.alphas.length; i += 3) {
      if (Math.abs(layer.colors[i * 3]! - treeColor.r) > 1e-6) continue;
      const p = layer.positions, base = i * 3;
      discArea += Math.abs((p[base + 3]! - p[base]!) * (p[base + 8]! - p[base + 2]!)
        - (p[base + 5]! - p[base + 2]!) * (p[base + 6]! - p[base]!)) / 2;
    }
    expect(discArea).toBeGreaterThan(0);
    expect(discArea).toBeLessThan(16 / 2 * 0.9 ** 2 * Math.sin(2 * Math.PI / 16));
    const layer = lawnOnly.layers[0]!;
    // The layer holds both the perimeter bands and the fan; isolate the fan vertices as
    // those within one radius of the tree (the perimeter is everywhere farther away).
    const radius = 0.9;
    let fanVertices = 0, rimCount = 0, westRim = false;
    for (let index = 0; index < layer.positions.length; index += 3) {
      const x = layer.positions[index]!, z = layer.positions[index + 2]!;
      const distance = Math.hypot(x - 9.5, z - 5);
      if (distance > radius + 1e-6) continue;
      fanVertices++;
      expect(x).toBeLessThanOrEqual(10 + 1e-9);   // clipped: never beyond the east edge
      if (Math.abs(distance - radius) < 1e-5) rimCount++;
      if (Math.abs(x - 8.6) < 1e-5) westRim = true;
    }
    expect(fanVertices).toBeGreaterThan(0);
    expect(rimCount).toBeGreaterThan(0);
    expect(westRim).toBe(true);   // the far side of the disc is present
    expect(lawnOnly.stats.bandAreaM2).toBeCloseTo(19, 6);  // joined open perimeter: 40w - 4w²

    const paved = planTerrainTransitions(input({
      surfaces: [surface("lot", "paving_generic", rectTriangles(0, 0, 20, 20))],
      trees: [tree("lot-tree", 10, 10, 5)],
    }));
    expect(paved.stats.treeBases).toBe(0);
    expect(paved.layers).toHaveLength(0);   // paved ground draws no band and no base

    const woodland = planTerrainTransitions(input({
      surfaces: [surface("wood", "woodland_floor", rectTriangles(0, 0, 20, 20))],
      trees: [tree("wood-tree", 10, 10, 5)],
    }));
    expect(woodland.stats.treeBases).toBe(0);
  });

  it("scales the base radius with the tree height within the clamp", () => {
    const wide = planTerrainTransitions(input({
      surfaces: [surface("park", "grass", rectTriangles(0, 0, 60, 60))],
      trees: [tree("small", 30, 30, 2), tree("large", 30, 30, 20)],
    }));
    expect(wide.stats.treeBases).toBe(2);
    const reach = (limit: number) => {
      const layer = wide.layers[0]!;
      let max = 0;
      for (let index = 0; index < layer.positions.length; index += 3) {
        const distance = Math.hypot(layer.positions[index]! - 30, layer.positions[index + 2]! - 30);
        if (distance < limit) max = Math.max(max, distance);
      }
      return max;
    };
    // Only the small (0.6 m) disc reaches inside 1 m; only the large (1.6 m) inside 2 m.
    expect(reach(1)).toBeCloseTo(0.6, 4);
    expect(reach(2)).toBeCloseTo(1.6, 4);
  });
});

describe("terrain transition vertex data", () => {
  it("sets alpha 1 x strength at the edge, 0 at the width, linear between", () => {
    // Isolate the building band: a building down the whole east edge, no walkbed, and a
    // water strip beyond the west edge so no open band pollutes the sampled column.
    const plan = planTerrainTransitions(input({
      buildings: [rectPolygon(10, 0, 12, 10)],
      surfaces: [surface("lawn", "grass", rectTriangles(0, 0, 10, 10)),
        surface("pond", "water", rectTriangles(-4, 0, 0, 10))],
    }));
    const layer = plan.layers[0]!;
    const width = TERRAIN_TRANSITIONS_V1.classes.building.widthM;
    const strength = TERRAIN_TRANSITIONS_V1.classes.building.strength;
    const buildingTint = LINEAR_TINTS.building!;
    const alphaAt = (x: number) => {
      const distance = Math.abs(x - 10);  // building edge is the line x = 10
      return Math.max(0, Math.min(1, 1 - distance / width)) * strength;
    };
    let sawEdge = false, sawRim = false;
    for (let index = 0; index < layer.positions.length / 3; index++) {
      const x = layer.positions[index * 3]!;
      if (x < 10 - width - 1e-6 || x > 10 + 1e-6) continue;
      // Building-band vertices carry the building tint (the west water band is tinted
      // differently and farther than width from x = 10).
      const base = index * 3;
      if (Math.abs(layer.colors[base]! - buildingTint[0]!) > 1e-5
        || Math.abs(layer.colors[base + 1]! - buildingTint[1]!) > 1e-5
        || Math.abs(layer.colors[base + 2]! - buildingTint[2]!) > 1e-5) continue;
      const alpha = layer.alphas[index]!;
      expect(alpha).toBeCloseTo(alphaAt(x), 5);
      if (Math.abs(x - 10) < 1e-6 && Math.abs(alpha - strength) < 1e-5) sawEdge = true;
      if (Math.abs(x - (10 - width)) < 1e-6 && alpha < 1e-6) sawRim = true;
    }
    expect(sawEdge).toBe(true);
    expect(sawRim).toBe(true);
    // The gradient is linear in the distance to the edge: the interpolated alpha at the
    // mid-band line x = 10 - width/2 is strength/2 on every band triangle that spans it.
    const triangles: [number, number, number][][] = [];
    for (let index = 0; index < layer.positions.length / 3; index += 3) {
      const triangle = [0, 1, 2].map(offset => [layer.positions[(index + offset) * 3]!,
        layer.positions[(index + offset) * 3 + 2]!, layer.alphas[index + offset]!] as [number, number, number]);
      if (triangle.some(point => Math.abs(point[0] - 10) < 1e-6)
        && triangle.some(point => Math.abs(point[0] - (10 - width)) < 1e-6)) {
        triangles.push(triangle);
      }
    }
    expect(triangles.length).toBeGreaterThan(0);
    const target = 10 - width / 2;
    for (const triangle of triangles) {
      // Find the interpolated alpha where the segment from the edge vertex to the rim
      // vertex crosses x = target (the third vertex shares one of the two columns).
      const edge = triangle.find(point => point[0] > 9)!;
      const rim = triangle.find(point => point[0] < 10 - width + 1)!;
      if (Math.abs(edge[1] - rim[1]) < 1e-9) continue;   // same z: degenerate row
      const t = (target - edge[0]) / (rim[0] - edge[0]);
      const alpha = edge[2] + t * (rim[2] - edge[2]);
      expect(alpha).toBeCloseTo(strength / 2, 5);
    }
  });

  it("emits the class tint in linear RGB for every shaded vertex", () => {
    const plan = planTerrainTransitions(input({
      buildings: [rectPolygon(10, 0, 12, 10)], walkbed: [rectPolygon(0, -2, 10, 0)],
    }));
    const layer = plan.layers[0]!;
    const matches = (index: number, tint: readonly number[]) => {
      const base = index * 3;
      return Math.abs(layer.colors[base]! - tint[0]!) < 1e-5
        && Math.abs(layer.colors[base + 1]! - tint[1]!) < 1e-5
        && Math.abs(layer.colors[base + 2]! - tint[2]!) < 1e-5;
    };
    const strengths = Object.entries(TERRAIN_TRANSITIONS_V1.classes)
      .map(([name, definition]) => [name, definition.strength, LINEAR_TINTS[name]!] as const);
    const seen = new Set<string>();
    for (let index = 0; index < layer.positions.length / 3; index++) {
      const alpha = layer.alphas[index]!;
      if (alpha === 0) continue;
      // Alpha may be any 0..strength value along the band gradient; only require that
      // every shaded vertex matches one class tint exactly (linear RGB), and that the
      // full-strength values for each present class are actually reached somewhere.
      const withTint = strengths.filter(([, , tint]) => matches(index, tint));
      expect(withTint.length).toBeGreaterThan(0);
      for (const [name, strengthValue] of withTint) {
        if (Math.abs(alpha - strengthValue) < 1e-5) seen.add(name);
      }
    }
    expect([...seen].sort()).toEqual(["building", "open", "paved"]);
  });

  it("lifts vertices 0.0015 above the surface height and sorts layers by surfaceY", () => {
    const plan = planTerrainTransitions(input({
      surfaces: [
        surface("lawn-high", "grass", rectTriangles(0, 0, 10, 10), 0.018),
        surface("gravel-low", "gravel", rectTriangles(20, 20, 30, 30), 0.012),
      ],
    }));
    expect(plan.layers.map(layer => layer.surfaceY)).toEqual([0.012, 0.018]);
    for (const layer of plan.layers) {
      for (let index = 1; index < layer.positions.length; index += 3) {
        expect(layer.positions[index]!).toBeCloseTo(layer.surfaceY + 0.0015, 6);
      }
    }
  });
});

describe("terrain transition validation and determinism", () => {
  it("throws on non-finite coordinates and duplicate surface IDs", () => {
    const bad: EnvironmentPoint[][] = [[[0, 0], [10, 0], [Number.NaN, 10]]];
    expect(() => planTerrainTransitions(input({ surfaces: [surface("bad", "grass", bad)] })))
      .toThrow(/not finite/);
    expect(() => planTerrainTransitions(input({
      surfaces: [surface("dup", "grass", rectTriangles(0, 0, 10, 10)),
        surface("dup", "grass", rectTriangles(20, 0, 30, 10))] })))
      .toThrow(/duplicate surface ID/);
  });

  it("is deterministic and order-insensitive up to vertex order", () => {
    const pond = surface("pond", "water", rectTriangles(-4, 0, 0, 10));
    const lawn = surface("lawn", "grass", rectTriangles(0, 0, 10, 10));
    const common = {
      buildings: [rectPolygon(10, 0, 12, 10)], walkbed: [rectPolygon(0, -2, 10, 0)],
      trees: [tree("t", 5, 5, 5)],
    };
    const first = planTerrainTransitions(input({ ...common, surfaces: [pond, lawn] }));
    const second = planTerrainTransitions(input({ ...common, surfaces: [lawn, pond] }));
    expect(first.stats).toEqual(second.stats);
    expect(first.layers.map(layer => layer.surfaceY)).toEqual(second.layers.map(layer => layer.surfaceY));
    const sorted = (plan: typeof first) => plan.layers.flatMap(layer =>
      Array.from({ length: layer.positions.length / 3 }, (_, index) =>
        [layer.positions[index * 3]!.toFixed(6), layer.positions[index * 3 + 2]!.toFixed(6),
          layer.alphas[index]!.toFixed(9)] as const))
      .sort((a, b) => a[0]!.localeCompare(b[0]!) || a[1]!.localeCompare(b[1]!)
        || a[2]!.localeCompare(b[2]!));
    expect(sorted(first)).toEqual(sorted(second));
    // Identical input, byte-identical arrays.
    const third = planTerrainTransitions(input({ ...common, surfaces: [pond, lawn] }));
    expect(third.layers[0]!.positions).toEqual(first.layers[0]!.positions);
    expect(third.layers[0]!.alphas).toEqual(first.layers[0]!.alphas);
  });
});

describe("terrain transition renderer", () => {
  it("creates multiply-blended meshes per layer with the required flags", () => {
    const plan = planTerrainTransitions(input({
      buildings: [rectPolygon(10, 0, 12, 10)],
      surfaces: [
        surface("lawn", "grass", rectTriangles(0, 0, 10, 10), 0.018),
        surface("gravel", "gravel", rectTriangles(20, 20, 30, 30), 0.012),
      ],
    }));
    const renderer = createTerrainTransitionLayer(plan);
    const meshes = renderer.group.children as unknown as THREE.Mesh[];
    expect(meshes).toHaveLength(2);
    expect(meshes.map(mesh => mesh.name).sort())
      .toEqual(["Terrain transitions y=0.012", "Terrain transitions y=0.018"]);
    for (const mesh of meshes) {
      const material = mesh.material as THREE.ShaderMaterial;
      expect(material.blending).toBe(THREE.MultiplyBlending);
      expect(material.premultipliedAlpha).toBe(true);
      expect(material.transparent).toBe(true);
      expect(material.depthWrite).toBe(false);
      expect(material.toneMapped).toBe(false);
      expect(material.fog).toBe(true);
      expect(material.polygonOffset).toBe(true);
      expect(material.polygonOffsetFactor).toBe(-1);
      expect(material.polygonOffsetUnits).toBe(-1);
      expect(Object.keys(material.uniforms)).toEqual(expect.arrayContaining(["fogColor", "fogNear", "fogFar"]));
      expect(mesh.renderOrder).toBe(1);
      expect(mesh.castShadow).toBe(false);
      expect(mesh.receiveShadow).toBe(false);
      expect(mesh.userData.terrainTransitions).toBe(plan.stats);
      expect(mesh.geometry.getAttribute("position")).toBeInstanceOf(THREE.BufferAttribute);
      expect(mesh.geometry.getAttribute("tint")).toBeInstanceOf(THREE.BufferAttribute);
      expect(mesh.geometry.getAttribute("alpha")).toBeInstanceOf(THREE.BufferAttribute);
      expect(material.fragmentShader).not.toContain("#include <fog_fragment>");
    }
    expect(renderer.group.userData.terrainTransitions).toBe(plan.stats);
    renderer.dispose();
    expect(renderer.group.children).toHaveLength(0);
  });

  it("produces an empty group for an empty plan and fires material dispose once", () => {
    const emptyPlan = planTerrainTransitions({ surfaces: [], buildings: [], roadbed: [],
      walkbed: [], trees: [] });
    expect(emptyPlan.layers).toHaveLength(0);
    const empty = createTerrainTransitionLayer(emptyPlan);
    expect(empty.group.children).toHaveLength(0);
    empty.dispose();
    const plan = planTerrainTransitions(input({ buildings: [rectPolygon(10, 0, 12, 10)] }));
    const renderer = createTerrainTransitionLayer(plan);
    const mesh = renderer.group.children[0] as THREE.Mesh;
    const material = mesh.material as THREE.Material;
    let disposed = 0;
    material.addEventListener("dispose", () => { disposed++; });
    renderer.dispose();
    expect(disposed).toBe(1);
  });

  it("emits upward-facing triangles for both source windings so FrontSide stays visible from above",
      () => {
    // Regression for fix round 1 (V1): the first build emitted downward-facing
    // triangles, which FrontSide culling hid completely from the usual top-down view.
    const trees = [tree("t", 5, 5, 5), tree("edge", 0.6, 5, 5)];
    for (const reverse of [false, true] as const) {
      const triangles = rectTriangles(0, 0, 10, 10).map(triangle => reverse ? [...triangle].reverse() : triangle);
      const plan = planTerrainTransitions(input({
        surfaces: [surface("lawn", "grass", triangles)],
        buildings: [rectPolygon(10, 0, 12, 10)],   // classified band, not only open
        trees,
      }));
      expect(plan.stats.pieces).toBeGreaterThan(0);
      expect(plan.stats.treeBases).toBe(2);
      const renderer = createTerrainTransitionLayer(plan);
      renderer.group.updateMatrixWorld(true);
      let up = 0, down = 0, degenerate = 0;
      for (const mesh of renderer.group.children as unknown as THREE.Mesh[]) {
        expect((mesh.material as THREE.ShaderMaterial).side).toBe(THREE.FrontSide);
        const position = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
        for (let index = 0; index < position.count; index += 3) {
          const ax = position.getX(index)!, az = position.getZ(index)!;
          const bx = position.getX(index + 1)!, bz = position.getZ(index + 1)!;
          const cx = position.getX(index + 2)!, cz = position.getZ(index + 2)!;
          // Three-dimensional right-hand-rule normal y for an XZ-plane triangle.
          const ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az);
          if (ny > 1e-8) up++;
          else if (ny < -1e-8) down++;
          else degenerate++;
        }
      }
      expect(down).toBe(0);
      expect(up).toBeGreaterThan(0);
      expect(degenerate).toBe(0);
      // Positive control: rays from above actually hit the layer with FrontSide.
      const bandHit = new THREE.Raycaster(new THREE.Vector3(9.5, 10, 5),
        new THREE.Vector3(0, -1, 0)).intersectObject(renderer.group, true);
      expect(bandHit.length).toBeGreaterThan(0);
      const treeHit = new THREE.Raycaster(new THREE.Vector3(5, 10, 5),
        new THREE.Vector3(0, -1, 0)).intersectObject(renderer.group, true);
      expect(treeHit.length).toBeGreaterThan(0);
      renderer.dispose();
    }
  });

  it("builds every point and overlap query on the 32 m grid", () => {
    // Ticket B7 / fix round 1 (V3): same-surface interior probes, band overlap queries
    // and tree-fan overlap queries must not scan all host triangles.
    const source = readFileSync(new URL("./city-terrain-transitions.ts", import.meta.url), "utf8");
    for (const scan of ["indexed.some", "for (const entry of indexed)",
      "for (const triangle of host.triangles) {"]) {
      expect(source).not.toContain(scan);
    }
    expect(source).toContain("surfaceGrid.query(quadBounds)");
    expect(source).toContain("hostGrid.query(fanBounds)");
    expect(source).toContain("softGrid.at(probe)");
    expect(source).toContain("vertexGrid.query(");
  });
});


/** Sample the same barycentric attributes and radial corner expression used by the
 * shader. Deduplicate hits on triangle edges; independent overlapping contributions
 * still count separately when a sample lies strictly inside two triangles. */
function sampleBand(plan: ReturnType<typeof planTerrainTransitions>, point: EnvironmentPoint,
    tint = LINEAR_TINTS.water!): { alpha: number; interiorHits: number } {
  let alpha = -1, interiorHits = 0;
  for (const layer of plan.layers) for (let i = 0; i < layer.alphas.length; i += 3) {
    if (Math.abs(layer.colors[i * 3]! - tint[0]!) > 1e-6) continue;
    const p = [0, 1, 2].map(j => [layer.positions[(i + j) * 3]!, layer.positions[(i + j) * 3 + 2]!] as const);
    const cross = (a: EnvironmentPoint, b: EnvironmentPoint, c: EnvironmentPoint) =>
      (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    const area = cross(p[0]!, p[1]!, p[2]!);
    const weights = [cross(point, p[1]!, p[2]!) / area, cross(p[0]!, point, p[2]!) / area,
      cross(p[0]!, p[1]!, point) / area];
    if (weights.some(w => w < -1e-6)) continue;
    if (weights.every(w => w > 1e-6)) interiorHits++;
    const interpolate = (data: Float32Array, stride: number, channel: number) =>
      weights.reduce((sum, w, j) => sum + w * data[(i + j) * stride + channel]!, 0);
    const inverseWidth = interpolate(layer.radial, 4, 2);
    const value = inverseWidth > 0 ? Math.max(0, 1 - Math.hypot(interpolate(layer.radial, 4, 0),
      interpolate(layer.radial, 4, 1)) * inverseWidth) * interpolate(layer.radial, 4, 3)
      : interpolate(layer.alphas, 1, 0);
    if (alpha >= 0) expect(value).toBeCloseTo(alpha, 6);
    alpha = value;
  }
  return { alpha, interiorHits };
}
function pondInput(hole: EnvironmentPoint[], sliver = false): TerrainTransitionInput {
  const triangles = triangulateEnvironmentPolygon({ ...rectPolygon(-25, -25, 25, 25), holes: [hole] });
  if (sliver) triangles.push([[hole[0]![0], hole[0]![1]], [hole[1]![0], hole[1]![1]],
    [hole[0]![0] + 0.00001, hole[0]![1] - 0.00001]]);
  return input({ surfaces: [surface("lawn", "grass", triangles),
    surface("water", "water", triangulateEnvironmentPolygon({ outline: hole, holes: [] }))] });
}
function assertRingSamples(hole: EnvironmentPoint[], distances: number[], sliver = false): void {
  const plan = planTerrainTransitions(pondInput(hole, sliver));
  for (let i = 0; i < hole.length; i++) {
    const a = hole[i]!, b = hole[(i + 1) % hole.length]!, previous = hole[(i + hole.length - 1) % hole.length]!;
    const normal = (a: EnvironmentPoint, b: EnvironmentPoint): EnvironmentPoint => {
      const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
      return [(b[1] - a[1]) / length, -(b[0] - a[0]) / length];
    };
    const n = normal(a, b), pn = normal(previous, a);
    const bisectorLength = Math.hypot(n[0] + pn[0], n[1] + pn[1]);
    for (const d of distances) {
      // Sample inside each piece, at splits, on the bisector, and either side of it.
      const points: EnvironmentPoint[] = [0.213, 0.5, 0.787].map(t => [a[0] + (b[0] - a[0]) * t + n[0] * d,
        a[1] + (b[1] - a[1]) * t + n[1] * d]);
      for (const t of [0.25, 0.5, 0.75]) {
        const nx = n[0] * t + pn[0] * (1 - t), nz = n[1] * t + pn[1] * (1 - t), length = Math.hypot(nx, nz);
        if (bisectorLength > 0) points.push([a[0] + d * nx / length, a[1] + d * nz / length]);
      }
      for (const point of points) {
        const sampled = sampleBand(plan, point);
        expect(sampled.alpha, JSON.stringify({ i, d, point })).toBeCloseTo(0.4 * (1 - d / 1.2), 6);
        expect(sampled.interiorHits).toBeLessThanOrEqual(1);
      }
    }
  }
}

describe("continuous boundary rings (W1–W4)", () => {
  it("recovers a split square hole once, dropping a collapsed sliver", () => {
    const hole: EnvironmentPoint[] = [[-5, -5], [0, -5], [5, -5], [5, 0], [5, 5], [0, 5], [-5, 5], [-5, 0]];
    const data = pondInput(hole, true), lawn = data.surfaces[0]!;
    const boundary = recoverTerrainTransitionBoundary(lawn.triangles);
    expect(boundary.rings).toHaveLength(2);
    const plan = planTerrainTransitions(data);
    // Outer inward strip: P*w - 4w². Hole outward mitred strip: P*w + 4w².
    const expected = 200 * 0.5 - 4 * 0.5 ** 2 + 40 * 1.2 + 4 * 1.2 ** 2;
    expect(Math.abs(plan.stats.bandAreaM2 - expected) / expected).toBeLessThan(0.01);
    expect(plan.stats.byClass.water).toBe(24); // eight 5 m edges, three pieces each
    assertRingSamples(hole, [0.1, 0.6, 1.1], true);
  });
  it("has equal alpha along a 16-gon shoreline, including every vertex", () => {
    const hole: EnvironmentPoint[] = Array.from({ length: 16 }, (_, i) =>
      [10 * Math.cos(i * 2 * Math.PI / 16), 10 * Math.sin(i * 2 * Math.PI / 16)]);
    assertRingSamples(hole, [0.1, 0.6, 1.1]);
    expect(planTerrainTransitions(pondInput(hole)).stats.byClass.water).toBe(32);
  });
  it("rounds an expanding corner beyond the four-width miter limit", () => {
    const data = pondInput([[0, 0], [10, -1], [10, 1]]);
    // A building supplies a uniform class even where the 25 cm probe crosses the
    // narrow tip of the hole. This isolates the miter fallback from classification.
    const plan = planTerrainTransitions({ ...data, buildings: [rectPolygon(-2, -3, 12, 3)] });
    for (const d of [0.1, 0.6, 0.8]) for (const angle of [Math.PI * 0.55, Math.PI, Math.PI * 1.45]) {
      const sampled = sampleBand(plan, [d * Math.cos(angle), d * Math.sin(angle)], LINEAR_TINTS.building!);
      expect(sampled.alpha).toBeCloseTo(0.3 * (1 - d / 0.9), 6);
      expect(sampled.interiorHits).toBeLessThanOrEqual(1);
    }
  });
  it("cancels a true T-junction with differently split shared edges", () => {
    const triangles = [...rectTriangles(0, 0, 10, 10),
      [[0, 0], [0, 5], [-10, 5]], [[0, 5], [0, 10], [-10, 5]],
      [[0, 10], [-10, 10], [-10, 5]], [[0, 0], [-10, 5], [-10, 0]]] as EnvironmentPoint[][];
    const rings = recoverTerrainTransitionBoundary(triangles).rings;
    expect(rings).toHaveLength(1);
    expect(rings[0]!.every(p => p[0] === -10 || p[0] === 10 || p[1] === 0 || p[1] === 10)).toBe(true);
  });
  it("returns cross-cell overlap candidates in registration order", () => {
    const grid = new TerrainTransitionGrid<string>();
    grid.add("first", [[40, 0], [60, 10]]);
    grid.add("second", [[0, 0], [10, 10]]);
    grid.add("third", [[0, 0], [60, 10]]);
    grid.add("first", [[40, 0], [60, 10]]);
    expect(grid.query({ minX: 0, maxX: 60, minZ: 0, maxZ: 10 })).toEqual(["first", "second", "third"]);
    expect(grid.at([45, 5])).toEqual(["first", "third"]);
  });
  it("matches a scanning reference byte for byte on published Huangpu", () => {
    const asset = (url: string) => resolve(process.cwd(), "public", url.replace(/^\/+/, ""));
    const json = (url: string) => JSON.parse(readFileSync(asset(url), "utf8"));
    const scene = json("/city-presentation/default-scene-v1.json");
    const render = json(scene.building_render.base_url + "manifest.json"), pack = json(scene.mesh_pack.base_url + "manifest.json");
    const binding = cityVegetationRoadBinding(validateCanonicalCityRoadPayload(json(scene.road_assets.road.url)),
      json(scene.road_assets.effective_fixtures.url).effective_fixtures);
    const layerInput = cityVegetationInput(scene.environment_source, render.scene, scene.mesh_pack.manifest.sha256, pack.extent, binding);
    const raw = json(layerInput.source.url), source = parseCityEnvironmentSource(raw, layerInput.authority);
    const covers = parseCityGroundCovers(raw, layerInput.authority);
    const assignments = new Map([...source.greens.map(groundMaterialInputFromGreen), ...covers.map(groundMaterialInputFromCover)]
      .map(value => { const assignment = assignCityGroundMaterial(value); return [assignment.polygonId, assignment] as const; }));
    const plan = generateCityEnvironment({ geometryId: scene.road_assets.displayed_surface_sha256, roadGeometry: "published",
      ...binding, extent: layerInput.extent, buildings: source.buildings, greens: source.greens, sourceTrees: source.sourceTrees },
    { ...CITY_ENVIRONMENT_DEFAULTS }, BIGCITY_ENVIRONMENT_TREES);
    const coverPlan = planCityGroundCovers(covers, { geometryId: scene.road_assets.displayed_surface_sha256, roadGeometry: "published",
      roadbed: binding.roadbed, walkbed: binding.walkbed, buildings: source.buildings });
    const data = input({ surfaces: [...[...plan.grass, ...plan.woodlandFloor].map(p => ({ id: p.id,
      family: assignments.get(p.id)!.family, surfaceY: plan.config.grassY, triangles: p.triangles })),
    ...coverPlan.covers.filter(p => p.triangles.length).map(p => ({ id: p.id, family: assignments.get(p.id)!.family,
      surfaceY: 0.012, triangles: p.triangles }))], buildings: source.buildings, roadbed: binding.roadbed,
    walkbed: binding.walkbed, trees: plan.trees });
    const gridded = planTerrainTransitions(data);
    expect(planTerrainTransitions(data)).toEqual(gridded);
    type Box = { minX: number; maxX: number; minZ: number; maxZ: number };
    // Replace only index lookup, keeping the planner and clipping code identical.
    const boxes = (grid: unknown) => (grid as { boxes: Map<unknown, Box> }).boxes;
    const query = vi.spyOn(TerrainTransitionGrid.prototype, "query").mockImplementation(function (this: unknown, q: Box) {
      return [...boxes(this)].filter(([, b]) => b.minX <= q.maxX && b.maxX >= q.minX
        && b.minZ <= q.maxZ && b.maxZ >= q.minZ).map(([value]) => value);
    });
    const at = vi.spyOn(TerrainTransitionGrid.prototype, "at").mockImplementation(function (this: unknown, p: EnvironmentPoint) {
      return [...boxes(this)].filter(([, b]) => p[0] >= b.minX && p[0] <= b.maxX && p[1] >= b.minZ && p[1] <= b.maxZ).map(([value]) => value);
    });
    try {
      const scanned = planTerrainTransitions(data);
      expect(scanned.stats).toEqual(gridded.stats);
      for (let i = 0; i < scanned.layers.length; i++) {
        for (const key of ["positions", "colors", "alphas", "radial"] as const) {
          expect(Buffer.from(scanned.layers[i]![key].buffer)).toEqual(Buffer.from(gridded.layers[i]![key].buffer));
        }
      }
    } finally { query.mockRestore(); at.mockRestore(); }
  }, 30000);
});
