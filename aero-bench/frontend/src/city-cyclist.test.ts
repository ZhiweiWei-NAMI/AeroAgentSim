import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { clone as cloneSkeleton } from "three/addons/utils/SkeletonUtils.js";
import { animateCyclist, bindCyclist, cacheCyclistStaticTransforms, cyclistKneePosition,
  type CyclistRig } from "./city-cyclist";

describe("city cyclist animation", () => {
  it("keeps both knees ahead of the hip-to-pedal line throughout a pedal turn", () => {
    const forward = new THREE.Vector3(0, 0, 1);
    for (const side of [-1, 1]) {
      const hip = new THREE.Vector3(side * 0.08, 0.87, -0.14);
      for (let frame = 0; frame < 16; frame++) {
        const angle = frame / 16 * Math.PI * 2;
        const pedal = new THREE.Vector3(side * 0.13,
          0.32 + Math.cos(angle) * 0.16, -0.12 + Math.sin(angle) * 0.16);
        const knee = cyclistKneePosition(hip, pedal, 0.37, 0.4, forward);
        const leg = pedal.clone().sub(hip).normalize();
        expect(knee.distanceTo(hip)).toBeCloseTo(0.37, 5);
        expect(knee.distanceTo(pedal)).toBeCloseTo(0.4, 5);
        expect(knee.clone().sub(hip).projectOnPlane(leg).dot(forward)).toBeGreaterThan(0.08);
        expect(Math.sign(knee.x)).toBe(side);
      }
    }
  });

  it("freezes only the parts animateCyclist does not write and keeps clone matrices exact", () => {
    const named = <T extends THREE.Object3D>(object: T, name: string, x: number): T => {
      object.name = name; object.position.set(x, 0.3, -0.2); object.rotation.set(0.1, 0.2, 0.3); return object;
    };
    const model = new THREE.Group();
    const frame = named(new THREE.Group(), "Bicycle_LOD0", 0.1);
    const rider = named(new THREE.Group(), "Citizens cyclist", 0.02);
    const pelvis = named(new THREE.Bone(), "Bip01_Pelvis", 0.05);
    const legs = ["Bip01_L_Thigh", "Bip01_L_Calf", "Bip01_R_Thigh", "Bip01_R_Calf"].map((name, i) => named(new THREE.Bone(), name, i * 0.1));
    const foot = named(new THREE.Bone(), "Bip01_L_Foot", 0.04);
    pelvis.add(legs[0]!, legs[2]!); legs[0]!.add(legs[1]!); legs[2]!.add(legs[3]!); legs[1]!.add(foot);
    rider.add(pelvis);
    const wheels = ["Wheel_back", "Wheel_front"].map((name, i) => named(new THREE.Group(), name, i));
    const hub = named(new THREE.Group(), "Wheel_back_LOD0", 0.01); wheels[0]!.add(hub);
    const crank = named(new THREE.Group(), "Pedaly", 0.3);
    model.add(frame, rider, crank, ...wheels);
    cacheCyclistStaticTransforms(model);
    const dynamic = new Set<THREE.Object3D>([model, ...legs, ...wheels, crank]);
    model.traverse(node => expect(node.matrixAutoUpdate).toBe(dynamic.has(node)));

    const poses = Array.from({ length: 16 }, (_, index) => legs.map(() =>
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), index / 16)));
    const frozen = cloneSkeleton(model) as THREE.Group, stock = cloneSkeleton(model) as THREE.Group;
    stock.traverse(node => { node.matrixAutoUpdate = true; });
    for (const clone of [frozen, stock]) {
      clone.position.set(4, 0, -2); clone.rotation.y = 1.2;
      animateCyclist(bindCyclist(clone), poses, 2.7);
      clone.updateMatrixWorld();
    }
    const stockNodes: THREE.Object3D[] = []; stock.traverse(node => stockNodes.push(node));
    let index = 0;
    frozen.traverse(node => {
      const expected = stockNodes[index++]!;
      expect(node.name).toBe(expected.name);
      for (let i = 0; i < 16; i++) expect(node.matrixWorld.elements[i]).toBeCloseTo(expected.matrixWorld.elements[i]!, 12);
    });
  });

  it("moves the pedal and wheels with recorded distance and holds a stopped pose", () => {
    const legs = Array.from({ length: 4 }, () => new THREE.Bone());
    const wheels = Array.from({ length: 2 }, () => ({
      object: new THREE.Group(), original: new THREE.Quaternion(),
    }));
    const crank = { object: new THREE.Group(), original: new THREE.Quaternion() };
    const rig: CyclistRig = { legs, wheels, crank };
    const poses = Array.from({ length: 16 }, (_, index) => legs.map(() =>
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), index / 16)));

    animateCyclist(rig, poses, 0);
    const stoppedWheel = wheels[0]!.object.quaternion.clone();
    animateCyclist(rig, poses, 1);
    expect(wheels[0]!.object.quaternion.equals(stoppedWheel)).toBe(false);
    expect(crank.object.quaternion.equals(new THREE.Quaternion())).toBe(false);
    const movingWheel = wheels[0]!.object.quaternion.clone();
    const movingLeg = legs[0]!.quaternion.clone();
    animateCyclist(rig, poses, 1);
    expect(wheels[0]!.object.quaternion.equals(movingWheel)).toBe(true);
    expect(legs[0]!.quaternion.equals(movingLeg)).toBe(true);
  });
});
