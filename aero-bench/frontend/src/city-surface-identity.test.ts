import { describe, expect, it } from "vitest";
import { displaySurfaceSha256, type DisplaySurfacePolygon } from "./city-surface-identity";

const roadbed: readonly DisplaySurfacePolygon[] = [
  {
    outline: [[0, 0], [12.5, 0], [12.5, 4], [0, 4]],
    holes: [[[2, 1], [3, 1], [3, 2], [2, 2]]],
  },
  { outline: [[-1, -2], [0, -2], [0, -1]], holes: [] },
];
const walkbed: readonly DisplaySurfacePolygon[] = [
  { outline: [[0, 5], [12.5, 5], [12.5, 8], [0, 8]], holes: [] },
];
const FIXED_SHA256 = "a404db26fd7476aabca0da048ffb36bc0d97c11675f49024b890d88ec4937f91";

describe("displayed roadbed and walkbed identity", () => {
  it("matches the fixed Python/TypeScript binary fixture", async () => {
    await expect(displaySurfaceSha256(roadbed, walkbed)).resolves.toBe(FIXED_SHA256);
  });

  it("preserves ordered geometry and detects point and hole changes", async () => {
    const baseline = await displaySurfaceSha256(roadbed, walkbed);
    const reordered = [{ ...roadbed[0]!, outline: [...roadbed[0]!.outline].reverse() }, roadbed[1]!];
    const movedPoint = [{ ...roadbed[0]!, outline: [[0, 0], [12.5001, 0], [12.5, 4], [0, 4]] as const,
      holes: roadbed[0]!.holes }, roadbed[1]!];
    const changedHole = [{ ...roadbed[0]!, outline: roadbed[0]!.outline,
      holes: [[[2, 1], [3.01, 1], [3, 2], [2, 2]]] as const }, roadbed[1]!];
    expect(await displaySurfaceSha256(reordered, walkbed)).not.toBe(baseline);
    expect(await displaySurfaceSha256(movedPoint, walkbed)).not.toBe(baseline);
    expect(await displaySurfaceSha256(changedHole, walkbed)).not.toBe(baseline);
  });

  it("canonicalizes negative zero and rejects malformed or non-finite geometry", async () => {
    const negativeZero = [{ outline: [[-0, -0], [12.5, 0], [12.5, 4], [0, 4]] as const,
      holes: roadbed[0]!.holes }, roadbed[1]!];
    expect(await displaySurfaceSha256(negativeZero, walkbed)).toBe(await displaySurfaceSha256(roadbed, walkbed));
    await expect(displaySurfaceSha256([{ outline: [[0, 0], [1, 0]], holes: [] }], []))
      .rejects.toThrow("at least three points");
    await expect(displaySurfaceSha256([{ outline: [[0, 0], [1, 0], [0, Number.NaN]], holes: [] }], []))
      .rejects.toThrow("finite numbers");
  });
});
