// @vitest-environment node
import { describe, expect, it } from "vitest";
import { environmentDiskIntersects, type EnvironmentPolygon } from "./city-environment";
import { parseCityAuthoredLandscape } from "./city-authored-landscape";
import { isTerrainCompletionItem, proposeTerrainCompletion, TERRAIN_COMPLETION_V1,
  type TerrainCompletionInput } from "./city-terrain-completion";

function rect(x: number, z: number, width: number, depth: number): EnvironmentPolygon {
  return { outline: [[x, z], [x + width, z], [x + width, z + depth], [x, z + depth]], holes: [] };
}
function input(overrides: Partial<TerrainCompletionInput> = {}): TerrainCompletionInput {
  return { extent: rect(0, 0, 40, 40), roadbed: [], walkbed: [], buildings: [], occupied: [], ...overrides };
}

describe("authored terrain completion", () => {
  it("merges identical runs maximally and produces deterministic parser-valid authored items", () => {
    const road = rect(18, 0, 4, 40), green = rect(0, 0, 8, 8);
    const source = input({ roadbed: [road], occupied: [green.outline] });
    const result = proposeTerrainCompletion(source, "green");
    expect(proposeTerrainCompletion(source, "green")).toEqual(result);
    expect(result.items.map(item => item.polygon)).toEqual([
      [{ x: 10, z: 0 }, { x: 16, z: 0 }, { x: 16, z: 10 }, { x: 10, z: 10 }],
      [{ x: 24, z: 0 }, { x: 40, z: 0 }, { x: 40, z: 40 }, { x: 24, z: 40 }],
      [{ x: 0, z: 10 }, { x: 16, z: 10 }, { x: 16, z: 40 }, { x: 0, z: 40 }],
    ]);
    expect(result.stats).toEqual({ residualCells: 295, rectangles: 3, areaM2: 1180, droppedSmall: 0, truncated: false });
    expect(result.items.map(item => item.id)).toEqual([
      "auto-completion-v1-green-5-0", "auto-completion-v1-green-12-0", "auto-completion-v1-green-0-5",
    ]);
    expect(result.items.map(item => item.label)).toEqual([
      "自动补全·绿地设计 1", "自动补全·绿地设计 2", "自动补全·绿地设计 3",
    ]);
    for (const item of result.items) {
      expect(isTerrainCompletionItem(item)).toBe(true);
      expect(item.provenance).toBe("authored");
      for (const point of item.polygon) for (const obstacle of [road, green])
        expect(environmentDiskIntersects([point.x, point.z], 0, obstacle)).toBe(false);
    }
    expect(parseCityAuthoredLandscape(result.items)).toEqual(result.items);
  });

  it("uses the selected kind and has no implicit kind", () => {
    for (const [kind, label] of [["green", "绿地设计"], ["plaza", "广场设计"], ["planting_strip", "种植带设计"]] as const) {
      const result = proposeTerrainCompletion(input(), kind);
      expect(result.items[0]).toMatchObject({ kind, id: `auto-completion-v1-${kind}-0-0`, label: `自动补全·${label} 1` });
    }
    expect(() => proposeTerrainCompletion(input(), undefined as never)).toThrow(/explicit valid kind/);
  });

  it("drops small rectangles and keeps rectangles at the minimum area", () => {
    expect(proposeTerrainCompletion(input({ extent: rect(0, 0, 2, 18) }), "plaza").stats)
      .toEqual({ residualCells: 9, rectangles: 0, areaM2: 0, droppedSmall: 1, truncated: false });
    expect(proposeTerrainCompletion(input({ extent: rect(0, 0, 2, 20) }), "plaza").items).toHaveLength(1);
    expect(TERRAIN_COMPLETION_V1.minAreaM2).toBe(40);
  });

  it("sorts and truncates more than 400 separate qualifying rectangles", () => {
    const roadbed = Array.from({ length: 401 }, (_, n) => rect(n * 8 + 4, 0, 2, 20));
    const result = proposeTerrainCompletion(input({ extent: rect(0, 0, 3208, 20), roadbed }), "plaza");
    expect(result.items).toHaveLength(400);
    expect(result.stats).toMatchObject({ rectangles: 401, truncated: true, areaM2: 16000 });
    expect(result.items.at(-1)!.id).toBe("auto-completion-v1-plaza-1596-0");
  });

  it("truncation through a lowered cap keeps the largest areas, deterministic over runs", () => {
    // Four road strips isolate four blocks. Corner samples lying on a road edge
    // count as occupied, so the blocks are 2, 2, 4 and 6 m wide (40, 40, 80,
    // 120 m² over the 20 m depth); pure grid order would keep 0, 4, 8 instead.
    const roadbed = [rect(4, 0, 2, 20), rect(12, 0, 2, 20), rect(22, 0, 2, 20), rect(34, 0, 2, 20)];
    const source = input({ extent: rect(0, 0, 36, 20), roadbed });
    const result = proposeTerrainCompletion(source, "plaza", { maxItems: 3 });
    expect(result.items.map(item => item.id)).toEqual([
      "auto-completion-v1-plaza-0-0", "auto-completion-v1-plaza-8-0", "auto-completion-v1-plaza-13-0",
    ]);
    expect(result.items.map(item => item.label)).toEqual([
      "自动补全·广场设计 1", "自动补全·广场设计 2", "自动补全·广场设计 3",
    ]);
    expect(result.stats).toMatchObject({ residualCells: 70, rectangles: 4, areaM2: 240, truncated: true });
    expect(proposeTerrainCompletion(source, "plaza", { maxItems: 3 })).toEqual(result);
    expect(proposeTerrainCompletion(source, "plaza", { maxItems: 4 }).stats).toMatchObject({ truncated: false, areaM2: 280 });
    // The untouched default cap equals passing it explicitly.
    expect(proposeTerrainCompletion(source, "plaza")).toEqual(
      proposeTerrainCompletion(source, "plaza", { maxItems: TERRAIN_COMPLETION_V1.maxItems }));
    expect(TERRAIN_COMPLETION_V1.maxItems).toBe(400);
  });

  it("rejects a non-positive or fractional cap instead of producing an empty set", () => {
    expect(() => proposeTerrainCompletion(input(), "green", { maxItems: 0 })).toThrow(/positive integer/);
    expect(() => proposeTerrainCompletion(input(), "green", { maxItems: 2.5 })).toThrow(/positive integer/);
    expect(() => proposeTerrainCompletion(input(), "green", { maxItems: Number.NaN })).toThrow(/positive integer/);
  });

  it("returns no proposals for fully occupied ground and respects obstacle holes", () => {
    expect(proposeTerrainCompletion(input({ occupied: [rect(0, 0, 40, 40).outline] }), "green").items).toEqual([]);
    const result = proposeTerrainCompletion(input({ buildings: [{ ...rect(0, 0, 40, 40), holes: [rect(10, 10, 20, 20).outline] }] }), "green");
    expect(result.items[0]!.polygon).toEqual([
      { x: 10, z: 10 }, { x: 30, z: 10 }, { x: 30, z: 30 }, { x: 10, z: 30 },
    ]);
  });

  it("excludes thin source triangles between samples and aligns negative coordinates to the world grid", () => {
    const result = proposeTerrainCompletion(input({ extent: rect(-21, -21, 42, 42),
      occupied: [[[0.2, -20], [0.4, 20], [0.6, -20]]] }), "green");
    for (const item of result.items) {
      for (const p of item.polygon) { expect(Math.abs(p.x % 2)).toBe(0); expect(Math.abs(p.z % 2)).toBe(0); }
      expect(item.polygon[1]!.x <= 0 || item.polygon[0]!.x >= 2).toBe(true);
    }
    expect(result.items).toHaveLength(2);
  });
});
