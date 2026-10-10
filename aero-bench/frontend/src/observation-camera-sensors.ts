import * as THREE from "three";
import type { PublicScenario, ResolvedPose, ResolvedSensor } from "./generated/aero-bench-contracts";
import type { CameraMount, QuaternionXyzw } from "./observation-camera";

export const DECLARED_SENSOR_DISPLAY_NEAR_M = 0.05;
export const DECLARED_SENSOR_DISPLAY_FAR_M = 14_000;

export interface DeclaredSensorMount {
  readonly sensorId: string;
  readonly providerId: string;
  readonly mount: CameraMount;
  readonly source: "declared-sensor-display";
  readonly sourceLabel: "Simulated RGB · declared sensor pose";
  readonly projectionLabel: string;
  readonly resolution: { readonly widthPx: number; readonly heightPx: number };
  readonly declaredFov: { readonly horizontalDegrees: number; readonly verticalDegrees: number };
}

const UNIT_QUATERNION_TOLERANCE = 1e-6;
const FORMAL_OPTICAL_FROM_THREE = new THREE.Quaternion()
  .setFromAxisAngle(new THREE.Vector3(0, 1, 0), -Math.PI / 2);

function assertFinite(value: number, label: string): void {
  if (!Number.isFinite(value)) throw new RangeError(`${label} must be finite`);
}

function rendererPosition(pose: ResolvedPose, label: string): THREE.Vector3 {
  const source = pose.position.enu;
  assertFinite(source.east_m, `${label}.east_m`);
  assertFinite(source.north_m, `${label}.north_m`);
  assertFinite(source.up_m, `${label}.up_m`);
  return new THREE.Vector3(source.east_m, source.up_m, -source.north_m);
}

function rendererQuaternion(pose: ResolvedPose, label: string): THREE.Quaternion {
  const source = pose.orientation_enu;
  assertFinite(source.qx, `${label}.qx`);
  assertFinite(source.qy, `${label}.qy`);
  assertFinite(source.qz, `${label}.qz`);
  assertFinite(source.qw, `${label}.qw`);
  const quaternion = new THREE.Quaternion(source.qx, source.qz, -source.qy, source.qw);
  if (Math.abs(quaternion.length() - 1) > UNIT_QUATERNION_TOLERANCE) {
    throw new RangeError(`${label} must be normalized`);
  }
  return quaternion;
}

function validateSensorProjection(sensor: ResolvedSensor, displayAspect: number): void {
  assertFinite(displayAspect, "displayAspect");
  if (displayAspect <= 0) throw new RangeError("displayAspect must be greater than 0");
  assertFinite(sensor.horizontal_fov_deg, `${sensor.sensor_id}.horizontal_fov_deg`);
  assertFinite(sensor.vertical_fov_deg, `${sensor.sensor_id}.vertical_fov_deg`);
  if (sensor.horizontal_fov_deg <= 0 || sensor.horizontal_fov_deg >= 180) {
    throw new RangeError(`${sensor.sensor_id}.horizontal_fov_deg must be between 0 and 180`);
  }
  if (sensor.vertical_fov_deg <= 0 || sensor.vertical_fov_deg >= 180) {
    throw new RangeError(`${sensor.sensor_id}.vertical_fov_deg must be between 0 and 180`);
  }
  if (!Number.isInteger(sensor.resolution_width_px) || sensor.resolution_width_px <= 0) {
    throw new RangeError(`${sensor.sensor_id}.resolution_width_px must be a positive integer`);
  }
  if (!Number.isInteger(sensor.resolution_height_px) || sensor.resolution_height_px <= 0) {
    throw new RangeError(`${sensor.sensor_id}.resolution_height_px must be a positive integer`);
  }
}

function displayProjection(sensor: ResolvedSensor, displayAspect: number): {
  verticalFovDegrees: number;
  horizontalFovDegrees: number;
} {
  const declaredHorizontalTangent = Math.tan(THREE.MathUtils.degToRad(sensor.horizontal_fov_deg) / 2);
  const declaredVerticalTangent = Math.tan(THREE.MathUtils.degToRad(sensor.vertical_fov_deg) / 2);
  // Use the largest centered rectangle that remains within both declared FOVs.
  const displayVerticalTangent = Math.min(declaredVerticalTangent, declaredHorizontalTangent / displayAspect);
  return {
    verticalFovDegrees: THREE.MathUtils.radToDeg(2 * Math.atan(displayVerticalTangent)),
    horizontalFovDegrees: THREE.MathUtils.radToDeg(2 * Math.atan(displayVerticalTangent * displayAspect)),
  };
}

function quaternionTuple(quaternion: THREE.Quaternion): QuaternionXyzw {
  return [quaternion.x, quaternion.y, quaternion.z, quaternion.w];
}

/**
 * Resolve the sole declared fixed camera for a vehicle into the viewer's mount
 * contract. The output describes a client render at a declared sensor pose; it
 * does not assert that a provider video frame exists.
 */
export function declaredSensorMount(
  scenario: PublicScenario,
  vehicleId: string,
  displayAspect: number,
): DeclaredSensorMount | null {
  const vehicles = scenario.entities.filter(entity => entity.entity_id === vehicleId);
  if (vehicles.length === 0) throw new Error(`Scenario has no entity ${vehicleId}`);
  if (vehicles.length > 1) throw new Error(`Scenario has duplicate entity ${vehicleId}`);

  const sensors = scenario.sensors.filter(sensor => sensor.parent_entity_id === vehicleId && sensor.kind === "camera");
  if (sensors.length === 0) return null;
  if (sensors.length > 1) throw new Error(`Vehicle ${vehicleId} has multiple declared cameras`);
  const vehicle = vehicles[0]!;
  const sensor = sensors[0]!;
  validateSensorProjection(sensor, displayAspect);

  const vehiclePosition = rendererPosition(vehicle.initial_pose, `${vehicleId}.initial_pose.position.enu`);
  const sensorPosition = rendererPosition(sensor.initial_pose, `${sensor.sensor_id}.initial_pose.position.enu`);
  const vehicleRotation = rendererQuaternion(vehicle.initial_pose, `${vehicleId}.initial_pose.orientation_enu`);
  const sensorRotation = rendererQuaternion(sensor.initial_pose, `${sensor.sensor_id}.initial_pose.orientation_enu`);
  const worldFromBodyInverse = vehicleRotation.clone().invert();
  const offsetBody = sensorPosition.sub(vehiclePosition).applyQuaternion(worldFromBodyInverse);
  const bodyFromFormalSensor = worldFromBodyInverse.multiply(sensorRotation);
  const bodyFromThreeOptical = bodyFromFormalSensor.multiply(FORMAL_OPTICAL_FROM_THREE);
  const display = displayProjection(sensor, displayAspect);

  const mount: CameraMount = {
    kind: "rigid",
    offsetBodyM: [offsetBody.x, offsetBody.y, offsetBody.z],
    rotationBodyFromMountXyzw: quaternionTuple(bodyFromThreeOptical),
    horizontalMirror: true,
    verticalFovDegrees: display.verticalFovDegrees,
    aspect: displayAspect,
    nearM: DECLARED_SENSOR_DISPLAY_NEAR_M,
    farM: DECLARED_SENSOR_DISPLAY_FAR_M,
  };
  const projectionLabel = `Declared ${sensor.horizontal_fov_deg.toFixed(1)}° H × `
    + `${sensor.vertical_fov_deg.toFixed(1)}° V, ${sensor.resolution_width_px}×${sensor.resolution_height_px}px; `
    + `display crop ${display.horizontalFovDegrees.toFixed(1)}° H × ${display.verticalFovDegrees.toFixed(1)}° V; `
    + `display clip ${DECLARED_SENSOR_DISPLAY_NEAR_M}–${DECLARED_SENSOR_DISPLAY_FAR_M} m; no provider frame`;
  return {
    sensorId: sensor.sensor_id,
    providerId: sensor.provider_id,
    mount,
    source: "declared-sensor-display",
    sourceLabel: "Simulated RGB · declared sensor pose",
    projectionLabel,
    resolution: { widthPx: sensor.resolution_width_px, heightPx: sensor.resolution_height_px },
    declaredFov: {
      horizontalDegrees: sensor.horizontal_fov_deg,
      verticalDegrees: sensor.vertical_fov_deg,
    },
  };
}
