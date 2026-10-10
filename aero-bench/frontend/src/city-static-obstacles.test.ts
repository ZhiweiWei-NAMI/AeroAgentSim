import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { cityStaticObstacles } from "./city-static-obstacles";
import { validateFacilityPlacement } from "./city-workspace-geometry";

describe("static street asset collision geometry", () => {
  it("measures each hidden lamp instance in world metres without treating the ground as an obstacle", () => {
    const roads = new THREE.Group();
    roads.position.x = 20;
    roads.add(new THREE.Mesh(new THREE.BoxGeometry(1000, .1, 1000)));
    const lamps = new THREE.InstancedMesh(new THREE.BoxGeometry(.3, 7, .3), new THREE.MeshStandardMaterial(), 2);
    lamps.name = "street_light_8 SG1"; lamps.visible = false;
    lamps.setMatrixAt(0, new THREE.Matrix4().makeTranslation(5, 3.5, 0));
    lamps.setMatrixAt(1, new THREE.Matrix4().makeTranslation(15, 3.5, 0));
    roads.add(lamps);
    const boxes = cityStaticObstacles(new THREE.Group(), roads, null);
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toMatchObject({ x: 25, z: 0, baseY: 0, heightM: 7 });
    const facility = { id: "port", kind: "vertiport", position: { x: 25, z: 0 },
      widthM: 12, depthM: 8, heightM: 4, rotationDeg: 30 };
    expect(validateFacilityPlacement(facility, boxes, [], [], []).map(issue => issue.code)).toEqual(["static_overlap"]);
    expect(validateFacilityPlacement({ ...facility, position: { x: 50, z: 0 } }, boxes, [], [], [])).toEqual([]);
  });
});
