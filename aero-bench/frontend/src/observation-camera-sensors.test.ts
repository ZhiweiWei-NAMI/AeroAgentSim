// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { PublicScenario, ResolvedPose } from "./generated/aero-bench-contracts";
import { cameraFootprintOnPlane, mountedCameraPose, type QuaternionXyzw } from "./observation-camera";
import { DECLARED_SENSOR_DISPLAY_FAR_M, DECLARED_SENSOR_DISPLAY_NEAR_M,
  declaredSensorMount } from "./observation-camera-sensors";

function xyzw(quaternion: THREE.Quaternion): QuaternionXyzw {
  return [quaternion.x, quaternion.y, quaternion.z, quaternion.w];
}

function pose(
  eastM: number,
  northM: number,
  upM: number,
  orientation: QuaternionXyzw = [0, 0, 0, 1],
): ResolvedPose {
  return {
    position: { enu: { east_m: eastM, north_m: northM, up_m: upM } },
    orientation_enu: { qx: orientation[0], qy: orientation[1], qz: orientation[2], qw: orientation[3] },
  } as ResolvedPose;
}

function scenarioWithCamera(vehiclePose: ResolvedPose, sensorPose: ResolvedPose): PublicScenario {
  return {
    entities: [{ entity_id: "uav.1", kind: "uav", initial_pose: vehiclePose }],
    sensors: [{
      sensor_id: "camera.1",
      provider_id: "flight",
      parent_entity_id: "uav.1",
      kind: "camera",
      initial_pose: sensorPose,
      horizontal_fov_deg: 90,
      vertical_fov_deg: 60,
      resolution_width_px: 1920,
      resolution_height_px: 1080,
    }],
  } as unknown as PublicScenario;
}

function expectVector(actual: THREE.Vector3, expected: readonly [number, number, number]): void {
  expect(actual.x).toBeCloseTo(expected[0], 10);
  expect(actual.y).toBeCloseTo(expected[1], 10);
  expect(actual.z).toBeCloseTo(expected[2], 10);
}

function signedAreaXz(points: readonly THREE.Vector3[]): number {
  return points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]!;
    return sum + point.x * next.z - point.z * next.x;
  }, 0) / 2;
}

describe("declaredSensorMount", () => {
  it("derives the fixed mount from parent and sensor world poses", () => {
    const yaw = xyzw(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2));
    const scenario = scenarioWithCamera(pose(10, 20, 5, yaw), pose(10, 22, 5, yaw));
    const declared = declaredSensorMount(scenario, "uav.1", 1)!;
    expect(declared.sensorId).toBe("camera.1");
    expect(declared.providerId).toBe("flight");
    expect(declared.source).toBe("declared-sensor-display");
    expect(declared.mount.kind).toBe("rigid");
    expect(declared.mount.horizontalMirror).toBe(true);
    expect(declared.mount.offsetBodyM[0]).toBeCloseTo(2);
    expect(declared.mount.offsetBodyM[1]).toBeCloseTo(0);
    expect(declared.mount.offsetBodyM[2]).toBeCloseTo(0);
    expect(declared.mount.nearM).toBe(DECLARED_SENSOR_DISPLAY_NEAR_M);
    expect(declared.mount.farM).toBe(DECLARED_SENSOR_DISPLAY_FAR_M);
    expect(declared.projectionLabel).toContain("no provider frame");
  });

  it("center-crops to the display aspect without exceeding either declared FOV", () => {
    const declared = declaredSensorMount(scenarioWithCamera(pose(0, 0, 0), pose(0, 0, 0)), "uav.1", 2)!;
    expect(declared.mount.aspect).toBe(2);
    expect(declared.mount.verticalFovDegrees).toBeCloseTo(53.130102, 5);
    expect(declared.projectionLabel).toContain("display crop 90.0° H × 53.1° V");
    expect(declared.resolution).toEqual({ widthPx: 1920, heightPx: 1080 });
    expect(declared.declaredFov).toEqual({ horizontalDegrees: 90, verticalDegrees: 60 });
  });

  it("maps formal forward, image-right and image-up into a mirrored Three camera", () => {
    const pitchDown = xyzw(new THREE.Quaternion()
      .setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2));
    const declared = declaredSensorMount(
      scenarioWithCamera(pose(0, 0, 0), pose(0, 0, 10, pitchDown)), "uav.1", 1,
    )!;
    const cameraPose = mountedCameraPose(new THREE.Vector3(), new THREE.Quaternion(), declared.mount);
    expectVector(new THREE.Vector3(0, 0, -1).applyQuaternion(cameraPose.quaternion), [0, -1, 0]);
    expectVector(new THREE.Vector3(0, 1, 0).applyQuaternion(cameraPose.quaternion), [1, 0, 0]);

    const footprint = cameraFootprintOnPlane(cameraPose, declared.mount, 0);
    expect(footprint.polygon).not.toBeNull();
    // Positive screen X is formal image-right (+Y), which is renderer north (-Z).
    expect(footprint.polygon![1].z).toBeLessThan(0);
    expect(footprint.polygon![0].z).toBeGreaterThan(0);
    expect(signedAreaXz(footprint.polygon!)).toBeLessThan(0);
  });

  it("returns unavailable for an undeclared sensor and rejects ambiguous declarations", () => {
    const noSensor = scenarioWithCamera(pose(0, 0, 0), pose(0, 0, 0));
    noSensor.sensors = [];
    expect(declaredSensorMount(noSensor, "uav.1", 1)).toBeNull();

    const ambiguous = scenarioWithCamera(pose(0, 0, 0), pose(0, 0, 0));
    ambiguous.sensors.push({ ...ambiguous.sensors[0]!, sensor_id: "camera.2" });
    expect(() => declaredSensorMount(ambiguous, "uav.1", 1)).toThrow(/multiple declared cameras/);
  });

  it("rejects invalid display and pose data without substituting values", () => {
    const scenario = scenarioWithCamera(pose(0, 0, 0), pose(0, 0, 0));
    expect(() => declaredSensorMount(scenario, "missing", 1)).toThrow(/no entity/);
    expect(() => declaredSensorMount(scenario, "uav.1", 0)).toThrow(/displayAspect/);
    scenario.sensors[0]!.initial_pose.orientation_enu.qw = 2;
    expect(() => declaredSensorMount(scenario, "uav.1", 1)).toThrow(/must be normalized/);
  });
});
