// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { applyMountedCamera, cameraFootprintOnPlane, mountedCameraPose,
  type CameraMount, type QuaternionXyzw } from "./observation-camera";

function xyzw(quaternion: THREE.Quaternion): QuaternionXyzw {
  return [quaternion.x, quaternion.y, quaternion.z, quaternion.w];
}

function rotation(axis: THREE.Vector3, radians: number): QuaternionXyzw {
  return xyzw(new THREE.Quaternion().setFromAxisAngle(axis, radians));
}

const forwardRigidMount: CameraMount = {
  kind: "rigid",
  offsetBodyM: [1, 0.5, -0.25],
  rotationBodyFromMountXyzw: rotation(new THREE.Vector3(0, 1, 0), -Math.PI / 2),
  horizontalMirror: false,
  verticalFovDegrees: 60,
  aspect: 16 / 9,
  nearM: 0.1,
  farM: 1_000,
};

function expectVector(actual: THREE.Vector3, expected: readonly [number, number, number]): void {
  expect(actual.x).toBeCloseTo(expected[0], 10);
  expect(actual.y).toBeCloseTo(expected[1], 10);
  expect(actual.z).toBeCloseTo(expected[2], 10);
}

describe("mountedCameraPose", () => {
  it("applies a body-local mount offset and xyzw optical rotation", () => {
    const pose = mountedCameraPose(
      new THREE.Vector3(10, 20, 30),
      new THREE.Quaternion(),
      forwardRigidMount,
    );
    expectVector(pose.position, [11, 20.5, 29.75]);
    expectVector(new THREE.Vector3(0, 0, -1).applyQuaternion(pose.quaternion), [1, 0, 0]);
    expectVector(new THREE.Vector3(0, 1, 0).applyQuaternion(pose.quaternion), [0, 1, 0]);
  });

  it("rotates both the mount offset and optical axes with a 90-degree vehicle yaw", () => {
    const yaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);
    const pose = mountedCameraPose(new THREE.Vector3(4, 5, 6), yaw, forwardRigidMount);
    expectVector(pose.position, [3.75, 5.5, 5]);
    expectVector(new THREE.Vector3(0, 0, -1).applyQuaternion(pose.quaternion), [0, 0, -1]);
  });

  it("retains vehicle roll instead of silently stabilizing the camera", () => {
    const roll = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
    const pose = mountedCameraPose(new THREE.Vector3(), roll, forwardRigidMount);
    expectVector(new THREE.Vector3(0, 0, -1).applyQuaternion(pose.quaternion), [1, 0, 0]);
    expectVector(new THREE.Vector3(0, 1, 0).applyQuaternion(pose.quaternion), [0, 0, 1]);
  });

  it("composes vehicle, fixed mount and declared gimbal rotations in that order", () => {
    const vehicleRoll = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
    const mount: CameraMount = { ...forwardRigidMount, kind: "gimbal", offsetBodyM: [0, 0, 0] };
    const gimbalYaw = rotation(new THREE.Vector3(0, 1, 0), Math.PI / 2);
    const pose = mountedCameraPose(new THREE.Vector3(), vehicleRoll, mount,
      { rotationMountFromOpticalXyzw: gimbalYaw });
    expectVector(new THREE.Vector3(0, 0, -1).applyQuaternion(pose.quaternion), [0, 1, 0]);
    expectVector(new THREE.Vector3(0, 1, 0).applyQuaternion(pose.quaternion), [0, 0, 1]);
  });

  it("requires explicit gimbal state and rejects it for a rigid sensor", () => {
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(),
      { ...forwardRigidMount, kind: "gimbal" })).toThrow(/requires its current optical orientation/);
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(), forwardRigidMount,
      { rotationMountFromOpticalXyzw: [0, 0, 0, 1] })).toThrow(/rigid camera mount/);
  });

  it("rejects invalid rotations and projection values rather than normalizing or substituting", () => {
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(0, 0, 0, 2),
      forwardRigidMount)).toThrow(/vehicleQuaternion must be normalized/);
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(),
      { ...forwardRigidMount, rotationBodyFromMountXyzw: [0, 0, 0, 2] })).toThrow(/must be normalized/);
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(),
      { ...forwardRigidMount, verticalFovDegrees: 180 })).toThrow(/verticalFovDegrees/);
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(),
      { ...forwardRigidMount, aspect: 0 })).toThrow(/aspect/);
    expect(() => mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(),
      { ...forwardRigidMount, nearM: 10, farM: 10 })).toThrow(/farM/);
  });
});

describe("applyMountedCamera", () => {
  it("applies the rigid pose, FOV, aspect and clipping planes", () => {
    const camera = new THREE.PerspectiveCamera();
    const pose = applyMountedCamera(camera, new THREE.Vector3(1, 2, 3),
      new THREE.Quaternion(), forwardRigidMount);
    expectVector(camera.position, [2, 2.5, 2.75]);
    expect(camera.quaternion.angleTo(pose.quaternion)).toBeCloseTo(0, 10);
    expect(camera.fov).toBe(60);
    expect(camera.aspect).toBe(16 / 9);
    expect(camera.near).toBe(0.1);
    expect(camera.far).toBe(1_000);
  });
});

describe("cameraFootprintOnPlane", () => {
  const downwardMount: CameraMount = {
    kind: "rigid",
    offsetBodyM: [0, 0, 0],
    rotationBodyFromMountXyzw: rotation(new THREE.Vector3(1, 0, 0), -Math.PI / 2),
    horizontalMirror: false,
    verticalFovDegrees: 90,
    aspect: 1,
    nearM: 0.1,
    farM: 100,
  };

  it("returns the complete clipped-frustum footprint for a downward camera", () => {
    const pose = mountedCameraPose(new THREE.Vector3(0, 10, 0), new THREE.Quaternion(), downwardMount);
    const footprint = cameraFootprintOnPlane(pose, downwardMount, 0);
    expect(footprint.center.status).toBe("hit");
    expectVector(footprint.center.point!, [0, 0, 0]);
    expect(footprint.center.opticalDepthM).toBeCloseTo(10);
    expect(footprint.polygon).not.toBeNull();
    expectVector(footprint.polygon![0], [-10, 0, -10]);
    expectVector(footprint.polygon![1], [10, 0, -10]);
    expectVector(footprint.polygon![2], [10, 0, 10]);
    expectVector(footprint.polygon![3], [-10, 0, 10]);
  });

  it("preserves horizon, behind-camera and partial-footprint results", () => {
    const horizontalMount: CameraMount = {
      ...downwardMount,
      rotationBodyFromMountXyzw: [0, 0, 0, 1],
    };
    const pose = mountedCameraPose(new THREE.Vector3(0, 10, 0), new THREE.Quaternion(), horizontalMount);
    const footprint = cameraFootprintOnPlane(pose, horizontalMount, 0);
    expect(footprint.center.status).toBe("parallel");
    expect(footprint.center.opticalDepthM).toBeNull();
    expect(footprint.corners.map(ray => ray.status)).toEqual(["behind", "behind", "hit", "hit"]);
    expect(footprint.polygon).toBeNull();
  });

  it("reports intersections beyond the declared far plane as clipped", () => {
    const shortRangeMount: CameraMount = { ...downwardMount, farM: 5 };
    const pose = mountedCameraPose(new THREE.Vector3(0, 10, 0), new THREE.Quaternion(), shortRangeMount);
    const footprint = cameraFootprintOnPlane(pose, shortRangeMount, 0);
    expect(footprint.center.status).toBe("clipped");
    expect(footprint.center.opticalDepthM).toBeCloseTo(10);
    expect(footprint.corners.every(ray => ray.status === "clipped")).toBe(true);
    expect(footprint.polygon).toBeNull();
  });

  it("reports a plane behind an upward camera without inventing points", () => {
    const upwardMount: CameraMount = {
      ...downwardMount,
      rotationBodyFromMountXyzw: rotation(new THREE.Vector3(1, 0, 0), Math.PI / 2),
    };
    const pose = mountedCameraPose(new THREE.Vector3(0, 10, 0), new THREE.Quaternion(), upwardMount);
    const footprint = cameraFootprintOnPlane(pose, upwardMount, 0);
    expect(footprint.center.status).toBe("behind");
    expect(footprint.corners.every(ray => ray.status === "behind")).toBe(true);
    expect(footprint.polygon).toBeNull();
  });

  it("mirrors the display-right ray and exposes a safe post-render mirror", () => {
    const pose = mountedCameraPose(new THREE.Vector3(0, 10, 0), new THREE.Quaternion(), downwardMount);
    const ordinary = cameraFootprintOnPlane(pose, downwardMount, 0);
    const mirroredMount: CameraMount = { ...downwardMount, horizontalMirror: true };
    const mirrored = cameraFootprintOnPlane(pose, mirroredMount, 0);
    expect(ordinary.polygon![1].x).toBeCloseTo(10);
    expect(mirrored.polygon![1].x).toBeCloseTo(-10);

    const camera = new THREE.PerspectiveCamera();
    applyMountedCamera(camera, new THREE.Vector3(), new THREE.Quaternion(), mirroredMount);
    expect(camera.scale.x).toBe(1);
    expect(camera.userData.observationHorizontalMirror).toBe(true);
    applyMountedCamera(camera, new THREE.Vector3(), new THREE.Quaternion(), downwardMount);
    expect(camera.scale.x).toBe(1);
    expect(camera.userData.observationHorizontalMirror).toBe(false);
  });
});
