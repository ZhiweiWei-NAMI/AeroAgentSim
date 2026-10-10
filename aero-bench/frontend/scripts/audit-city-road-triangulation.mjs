import { readFileSync } from "node:fs";
import * as THREE from "three";

const path = process.argv[2];
if (path === undefined) throw new Error("Pass a generated road preview JSON path");
const road = JSON.parse(readFileSync(path, "utf8"));
if (!["aero-bench.city-road-preview/v2", "aero-bench.city-static-road-candidate/v1"].includes(road.schema_version)
    || !Array.isArray(road.roadbed)
    || road.roadbed.length === 0 || !(road.roadbed_area_m2 > 0)
    || !Array.isArray(road.concrete_roadbed) || !Number.isFinite(road.concrete_roadbed_area_m2)
    || road.concrete_roadbed_area_m2 < 0
    || !Array.isArray(road.walkbed) || road.walkbed.length === 0 || !(road.walkbed_area_m2 > 0)) {
  throw new Error("Road preview has no valid road or walking surface inventory");
}

function triangulatedAreaOf(polygons, label) {
  let triangleCount = 0;
  let triangulatedArea = 0;
  for (const [index, polygon] of polygons.entries()) {
    const outline = polygon.outline.map(([x, z]) => new THREE.Vector2(x, z));
    const holes = polygon.holes.map(hole => hole.map(([x, z]) => new THREE.Vector2(x, z)));
    const points = [...polygon.outline, ...polygon.holes.flat()];
    const triangles = THREE.ShapeUtils.triangulateShape(outline, holes);
    if (triangles.length === 0) throw new Error(`${label} tile ${index} has no triangles`);
    for (const [a, b, c] of triangles) {
      const p = points[a], q = points[b], r = points[c];
      if (p === undefined || q === undefined || r === undefined) {
        throw new Error(`${label} tile ${index} references an absent vertex`);
      }
      const area = Math.abs((q[0] - p[0]) * (r[1] - p[1])
        - (q[1] - p[1]) * (r[0] - p[0])) / 2;
      if (area > 1e-8) { triangulatedArea += area; triangleCount++; }
    }
  }
  return { triangleCount, triangulatedArea };
}
const result = {};
for (const [label, polygons, expectedArea] of [
  ["roadbed", road.roadbed, road.roadbed_area_m2],
  ["walkbed", road.walkbed, road.walkbed_area_m2],
  ["concrete_roadbed", road.concrete_roadbed, road.concrete_roadbed_area_m2],
]) {
  const { triangleCount, triangulatedArea } = triangulatedAreaOf(polygons, label);
  const errorFraction = expectedArea === 0 ? (triangulatedArea === 0 ? 0 : Infinity)
    : Math.abs(triangulatedArea - expectedArea) / expectedArea;
  if (errorFraction > 0.001) {
    throw new Error(`Browser ${label} triangles differ from the declared area by ${(errorFraction * 100).toFixed(3)}%`);
  }
  result[label] = { tiles: polygons.length, triangles: triangleCount,
    triangulated_area_m2: Math.round(triangulatedArea * 100) / 100, area_error_fraction: errorFraction };
}
console.log(JSON.stringify(result, null, 2));
