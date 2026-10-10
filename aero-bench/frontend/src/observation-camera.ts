import * as THREE from "three";

/** Quaternion components in Three.js order. */
export type QuaternionXyzw = readonly [x: number, y: number, z: number, w: number];

/**
 * A camera mount expressed in renderer body coordinates.
 *
 * Renderer world axes are +X east, +Y up and +Z south, in metres. Vehicle body
 * axes are +X forward, +Y up and +Z right. Three.js optical axes are +X right,
 * +Y up and -Z forward. Rotations use local-to-parent xyzw quaternions.
 */
export interface CameraMount {
  readonly kind: "rigid" | "gimbal";
  readonly offsetBodyM: readonly [forwardM: number, upM: number, rightM: number];
  readonly rotationBodyFromMountXyzw: QuaternionXyzw;
  /** Mirror camera-local X when the declared optical image axes are left-handed. */
  readonly horizontalMirror: boolean;
  readonly verticalFovDegrees: number;
  readonly aspect: number;
  readonly nearM: number;
  readonly farM: number;
}

/** Current optical orientation relative to a declared gimbal mount. */
export interface GimbalOrientation {
  readonly rotationMountFromOpticalXyzw: QuaternionXyzw;
}

export interface MountedCameraPose {
  readonly position: THREE.Vector3;
  readonly quaternion: THREE.Quaternion;
}

export type CameraFootprintRayStatus = "hit" | "parallel" | "behind" | "clipped";
export type CameraFootprintSampleName = "center" | "top-left" | "top-right" | "bottom-right" | "bottom-left";

export interface CameraFootprintRay {
  readonly name: CameraFootprintSampleName;
  readonly ndc: readonly [x: number, y: number];
  readonly status: CameraFootprintRayStatus;
  /** Intersection point for a visible, in-range hit. Null for every other status. */
  readonly point: THREE.Vector3 | null;
  /** Signed optical-axis depth. Null only when the ray is parallel to the plane. */
  readonly opticalDepthM: number | null;
}

export interface CameraFootprint {
  readonly planeUpM: number;
  readonly center: CameraFootprintRay;
  /** Clockwise NDC order: top-left, top-right, bottom-right, bottom-left. */
  readonly corners: readonly [CameraFootprintRay, CameraFootprintRay, CameraFootprintRay, CameraFootprintRay];
  /** Present only when every corner produces a visible, in-range hit. */
  readonly polygon: readonly [THREE.Vector3, THREE.Vector3, THREE.Vector3, THREE.Vector3] | null;
}

const QUATERNION_UNIT_TOLERANCE = 1e-6;
const PARALLEL_DIRECTION_TOLERANCE = 1e-10;

function assertFinite(value: number, label: string): void {
  if (!Number.isFinite(value)) throw new RangeError(`${label} must be finite`);
}

function assertFiniteVector(vector: THREE.Vector3, label: string): void {
  assertFinite(vector.x, `${label}.x`);
  assertFinite(vector.y, `${label}.y`);
  assertFinite(vector.z, `${label}.z`);
}

function assertTuple(tuple: readonly number[], length: number, label: string): void {
  if (tuple.length !== length) throw new RangeError(`${label} must contain ${length} values`);
  tuple.forEach((value, index) => assertFinite(value, `${label}[${index}]`));
}

function tupleQuaternion(tuple: QuaternionXyzw, label: string): THREE.Quaternion {
  assertTuple(tuple, 4, label);
  const quaternion = new THREE.Quaternion(tuple[0], tuple[1], tuple[2], tuple[3]);
  assertUnitQuaternion(quaternion, label);
  return quaternion;
}

function assertUnitQuaternion(quaternion: THREE.Quaternion, label: string): void {
  assertFinite(quaternion.x, `${label}.x`);
  assertFinite(quaternion.y, `${label}.y`);
  assertFinite(quaternion.z, `${label}.z`);
  assertFinite(quaternion.w, `${label}.w`);
  const length = quaternion.length();
  if (Math.abs(length - 1) > QUATERNION_UNIT_TOLERANCE) {
    throw new RangeError(`${label} must be normalized`);
  }
}

function validateMount(mount: CameraMount): void {
  assertTuple(mount.offsetBodyM, 3, "mount.offsetBodyM");
  tupleQuaternion(mount.rotationBodyFromMountXyzw, "mount.rotationBodyFromMountXyzw");
  if (typeof mount.horizontalMirror !== "boolean") {
    throw new TypeError("mount.horizontalMirror must be boolean");
  }
  assertFinite(mount.verticalFovDegrees, "mount.verticalFovDegrees");
  if (mount.verticalFovDegrees <= 0 || mount.verticalFovDegrees >= 180) {
    throw new RangeError("mount.verticalFovDegrees must be greater than 0 and less than 180");
  }
  assertFinite(mount.aspect, "mount.aspect");
  if (mount.aspect <= 0) throw new RangeError("mount.aspect must be greater than 0");
  assertFinite(mount.nearM, "mount.nearM");
  if (mount.nearM <= 0) throw new RangeError("mount.nearM must be greater than 0");
  assertFinite(mount.farM, "mount.farM");
  if (mount.farM <= mount.nearM) throw new RangeError("mount.farM must be greater than mount.nearM");
}

function opticalQuaternion(mount: CameraMount, gimbal?: GimbalOrientation): THREE.Quaternion {
  const fixed = tupleQuaternion(mount.rotationBodyFromMountXyzw, "mount.rotationBodyFromMountXyzw");
  if (mount.kind === "rigid") {
    if (gimbal !== undefined) throw new Error("A rigid camera mount cannot accept a gimbal orientation");
    return fixed;
  }
  if (gimbal === undefined) throw new Error("A gimbal camera mount requires its current optical orientation");
  return fixed.multiply(tupleQuaternion(gimbal.rotationMountFromOpticalXyzw,
    "gimbal.rotationMountFromOpticalXyzw"));
}

/**
 * Resolve a camera pose as vehicle-world * body-mount * mount-optical.
 * Inputs remain unchanged; returned vectors and quaternions are new objects.
 */
export function mountedCameraPose(
  vehiclePosition: THREE.Vector3,
  vehicleQuaternion: THREE.Quaternion,
  mount: CameraMount,
  gimbal?: GimbalOrientation,
): MountedCameraPose {
  assertFiniteVector(vehiclePosition, "vehiclePosition");
  assertUnitQuaternion(vehicleQuaternion, "vehicleQuaternion");
  validateMount(mount);

  const offset = new THREE.Vector3(...mount.offsetBodyM).applyQuaternion(vehicleQuaternion);
  const quaternion = vehicleQuaternion.clone().multiply(opticalQuaternion(mount, gimbal));
  return { position: vehiclePosition.clone().add(offset), quaternion };
}

/** Apply a mounted pose and its declared perspective projection to a Three.js camera. */
export function applyMountedCamera(
  camera: THREE.PerspectiveCamera,
  vehiclePosition: THREE.Vector3,
  vehicleQuaternion: THREE.Quaternion,
  mount: CameraMount,
  gimbal?: GimbalOrientation,
): MountedCameraPose {
  const pose = mountedCameraPose(vehiclePosition, vehicleQuaternion, mount, gimbal);
  camera.position.copy(pose.position);
  camera.quaternion.copy(pose.quaternion);
  // A negative camera scale reverses triangle winding, while Three.js culling
  // compensates only for negative object transforms. Keep the rigid camera
  // proper and let the render surface mirror its completed image.
  camera.scale.set(1, 1, 1);
  camera.userData.observationHorizontalMirror = mount.horizontalMirror;
  camera.fov = mount.verticalFovDegrees;
  camera.aspect = mount.aspect;
  camera.near = mount.nearM;
  camera.far = mount.farM;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return pose;
}

function footprintRay(
  name: CameraFootprintSampleName,
  ndc: readonly [number, number],
  pose: MountedCameraPose,
  mount: CameraMount,
  planeUpM: number,
): CameraFootprintRay {
  const tanHalfVertical = Math.tan(THREE.MathUtils.degToRad(mount.verticalFovDegrees) / 2);
  // Keeping local Z at -1 makes the ray parameter equal to optical-axis depth,
  // which is the quantity constrained by Three.js near and far clipping planes.
  const direction = new THREE.Vector3(
    ndc[0] * mount.aspect * tanHalfVertical * (mount.horizontalMirror ? -1 : 1),
    ndc[1] * tanHalfVertical,
    -1,
  ).applyQuaternion(pose.quaternion);
  if (Math.abs(direction.y) <= PARALLEL_DIRECTION_TOLERANCE * direction.length()) {
    return { name, ndc, status: "parallel", point: null, opticalDepthM: null };
  }

  const opticalDepthM = (planeUpM - pose.position.y) / direction.y;
  if (opticalDepthM < 0) return { name, ndc, status: "behind", point: null, opticalDepthM };
  if (opticalDepthM < mount.nearM || opticalDepthM > mount.farM) {
    return { name, ndc, status: "clipped", point: null, opticalDepthM };
  }
  return {
    name,
    ndc,
    status: "hit",
    point: pose.position.clone().addScaledVector(direction, opticalDepthM),
    opticalDepthM,
  };
}

/**
 * Intersect the optical center and four frustum-corner rays with a flat world-up
 * plane. Missing intersections retain their cause; no horizon points are invented.
 */
export function cameraFootprintOnPlane(
  pose: MountedCameraPose,
  mount: CameraMount,
  planeUpM: number,
): CameraFootprint {
  assertFiniteVector(pose.position, "pose.position");
  assertUnitQuaternion(pose.quaternion, "pose.quaternion");
  validateMount(mount);
  assertFinite(planeUpM, "planeUpM");

  const center = footprintRay("center", [0, 0], pose, mount, planeUpM);
  const topLeft = footprintRay("top-left", [-1, 1], pose, mount, planeUpM);
  const topRight = footprintRay("top-right", [1, 1], pose, mount, planeUpM);
  const bottomRight = footprintRay("bottom-right", [1, -1], pose, mount, planeUpM);
  const bottomLeft = footprintRay("bottom-left", [-1, -1], pose, mount, planeUpM);
  const corners: CameraFootprint["corners"] = [topLeft, topRight, bottomRight, bottomLeft];
  const polygon = topLeft.point !== null && topRight.point !== null
      && bottomRight.point !== null && bottomLeft.point !== null
    ? [topLeft.point, topRight.point, bottomRight.point, bottomLeft.point] as const
    : null;
  return { planeUpM, center, corners, polygon };
}
