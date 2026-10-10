import * as THREE from "three";
import { clone as cloneSkeleton } from "three/addons/utils/SkeletonUtils.js";
import { fitCityCharacter } from "./city-character";
import { cacheStaticTransforms } from "./city-rendering";

const LEG_NAMES = ["Bip01_L_Thigh", "Bip01_L_Calf", "Bip01_R_Thigh", "Bip01_R_Calf"] as const;
const CYCLE_FRAMES = 16;
const METRES_PER_PEDAL_TURN = 3.2;
const WHEEL_RADIUS = 0.33;
const AXLE = new THREE.Vector3(1, 0, 0);
const FORWARD = new THREE.Vector3(0, 0, 1);

export type CyclistPoseFrames = readonly (readonly THREE.Quaternion[])[];
interface SpinningPart { readonly object: THREE.Object3D; readonly original: THREE.Quaternion; }
export interface CyclistRig {
  readonly legs: readonly THREE.Bone[];
  readonly wheels: readonly SpinningPart[];
  readonly crank: SpinningPart;
}

function requiredObject(root: THREE.Object3D, name: string): THREE.Object3D {
  const object = root.getObjectByName(name);
  if (object === undefined) throw new Error(`Cyclist source is missing ${name}`);
  return object;
}

function requiredBone(root: THREE.Object3D, name: string): THREE.Bone {
  const object = requiredObject(root, name);
  if (!(object instanceof THREE.Bone)) throw new Error(`Cyclist source has no bone ${name}`);
  return object;
}

function rotateBoneWorld(root: THREE.Object3D, bone: THREE.Bone, rotation: THREE.Quaternion): void {
  const current = bone.getWorldQuaternion(new THREE.Quaternion());
  const parent = bone.parent?.getWorldQuaternion(new THREE.Quaternion());
  if (parent === undefined) throw new Error(`Cyclist bone has no parent: ${bone.name}`);
  bone.quaternion.copy(parent.invert().multiply(rotation).multiply(current));
  root.updateMatrixWorld(true);
}

function reach(root: THREE.Object3D, upper: THREE.Bone, lower: THREE.Bone,
               handOrFoot: THREE.Bone, target: THREE.Vector3): void {
  for (let attempt = 0; attempt < 12; attempt++) {
    for (const joint of [lower, upper]) {
      const pivot = joint.getWorldPosition(new THREE.Vector3());
      const current = handOrFoot.getWorldPosition(new THREE.Vector3()).sub(pivot).normalize();
      const desired = target.clone().sub(pivot).normalize();
      rotateBoneWorld(root, joint, new THREE.Quaternion().setFromUnitVectors(current, desired));
    }
  }
}

/** Solve the knee on the bicycle's forward side of the hip-to-pedal line. */
export function cyclistKneePosition(hip: THREE.Vector3, target: THREE.Vector3,
                                    upperLength: number, lowerLength: number,
                                    forward: THREE.Vector3): THREE.Vector3 {
  const direction = target.clone().sub(hip);
  const distance = direction.length();
  if (distance < 1e-6 || upperLength <= 0 || lowerLength <= 0) {
    throw new Error("Cyclist leg has invalid joint geometry");
  }
  direction.divideScalar(distance);
  const pole = forward.clone().addScaledVector(direction, -forward.dot(direction));
  if (pole.lengthSq() < 1e-8) throw new Error("Cyclist knee pole is parallel to the leg");
  pole.normalize();
  const reachable = THREE.MathUtils.clamp(distance,
    Math.abs(upperLength - lowerLength) + 1e-5, upperLength + lowerLength - 1e-5);
  const along = (upperLength * upperLength - lowerLength * lowerLength + reachable * reachable)
    / (2 * reachable);
  const bend = Math.sqrt(Math.max(0, upperLength * upperLength - along * along));
  return hip.clone().addScaledVector(direction, along).addScaledVector(pole, bend);
}

function reachPedal(root: THREE.Object3D, thigh: THREE.Bone, calf: THREE.Bone,
                    foot: THREE.Bone, target: THREE.Vector3): void {
  const hip = thigh.getWorldPosition(new THREE.Vector3());
  const knee = calf.getWorldPosition(new THREE.Vector3());
  const ankle = foot.getWorldPosition(new THREE.Vector3());
  const forward = FORWARD.clone().applyQuaternion(root.getWorldQuaternion(new THREE.Quaternion()));
  const desiredKnee = cyclistKneePosition(hip, target, hip.distanceTo(knee),
    knee.distanceTo(ankle), forward);
  rotateBoneWorld(root, thigh, new THREE.Quaternion().setFromUnitVectors(
    knee.sub(hip).normalize(), desiredKnee.clone().sub(hip).normalize()));
  const rotatedKnee = calf.getWorldPosition(new THREE.Vector3());
  const rotatedAnkle = foot.getWorldPosition(new THREE.Vector3());
  rotateBoneWorld(root, calf, new THREE.Quaternion().setFromUnitVectors(
    rotatedAnkle.sub(rotatedKnee).normalize(), target.clone().sub(rotatedKnee).normalize()));
}

/** Attach the supplied Citizens skin to the supplied bicycle and precompute leg poses. */
export function createCyclistTemplate(bicycle: THREE.Group, person: THREE.Group,
                                      walkClip: THREE.AnimationClip):
    { readonly model: THREE.Group; readonly poses: CyclistPoseFrames } {
  const rider = fitCityCharacter(cloneSkeleton(person), 1.72);
  rider.name = "Citizens cyclist";
  rider.position.set(0, 0.03, -0.15);
  bicycle.add(rider);
  const walk = new THREE.AnimationMixer(rider);
  walk.clipAction(walkClip).play();
  walk.setTime(0.2);
  bicycle.updateMatrixWorld(true);

  rotateBoneWorld(bicycle, requiredBone(rider, "Bip01_Spine1"),
    new THREE.Quaternion().setFromAxisAngle(AXLE, 0.55));
  for (const side of ["L", "R"] as const) {
    const sign = side === "L" ? 1 : -1;
    reach(bicycle, requiredBone(rider, `Bip01_${side}_UpperArm`),
      requiredBone(rider, `Bip01_${side}_Forearm`), requiredBone(rider, `Bip01_${side}_Hand`),
      new THREE.Vector3(sign * 0.29, 1, 0.53));
  }

  const pivot = requiredObject(bicycle, "Pedaly").getWorldPosition(new THREE.Vector3());
  const pedals = (["Pedal_Left", "Pedal_right"] as const).map(name =>
    requiredObject(bicycle, name).getWorldPosition(new THREE.Vector3()).sub(pivot));
  const legs = LEG_NAMES.map(name => requiredBone(rider, name));
  const standingPose = legs.map(bone => bone.quaternion.clone());
  const poses: THREE.Quaternion[][] = [];
  for (let frame = 0; frame < CYCLE_FRAMES; frame++) {
    for (let index = 0; index < legs.length; index++) legs[index]!.quaternion.copy(standingPose[index]!);
    bicycle.updateMatrixWorld(true);
    for (const [index, side] of (["L", "R"] as const).entries()) {
      const ankle = pivot.clone().add(pedals[index]!.clone().applyAxisAngle(AXLE,
        frame / CYCLE_FRAMES * Math.PI * 2));
      ankle.y += 0.04;
      reachPedal(bicycle, requiredBone(rider, `Bip01_${side}_Thigh`),
        requiredBone(rider, `Bip01_${side}_Calf`), requiredBone(rider, `Bip01_${side}_Foot`), ankle);
    }
    poses.push(legs.map(bone => bone.quaternion.clone()));
  }
  for (let index = 0; index < legs.length; index++) legs[index]!.quaternion.copy(poses[0]![index]!);
  bicycle.updateMatrixWorld(true);
  return { model: bicycle, poses };
}

export function bindCyclist(model: THREE.Group): CyclistRig {
  const rider = requiredObject(model, "Citizens cyclist");
  const spinning = (name: string): SpinningPart => {
    const object = requiredObject(model, name);
    return { object, original: object.quaternion.clone() };
  };
  return {
    legs: LEG_NAMES.map(name => requiredBone(rider, name)),
    wheels: [spinning("Wheel_back"), spinning("Wheel_front")],
    crank: spinning("Pedaly"),
  };
}

/** animateCyclist writes only leg, wheel and crank quaternions; clones inherit the frozen rest of the rig. */
export function cacheCyclistStaticTransforms(model: THREE.Group): void {
  const rig = bindCyclist(model);
  const animated = new Set<THREE.Object3D>([...rig.legs, ...rig.wheels.map(wheel => wheel.object), rig.crank.object]);
  cacheStaticTransforms(model, node => node === model || animated.has(node));
}

export function animateCyclist(rig: CyclistRig, poses: CyclistPoseFrames, travelledMetres: number): void {
  if (!Number.isFinite(travelledMetres) || travelledMetres < 0 || poses.length !== CYCLE_FRAMES) {
    throw new Error("Cyclist motion has invalid distance or pose data");
  }
  const cycles = travelledMetres / METRES_PER_PEDAL_TURN;
  const position = THREE.MathUtils.euclideanModulo(cycles, 1) * poses.length;
  const before = Math.floor(position), after = (before + 1) % poses.length;
  for (let index = 0; index < rig.legs.length; index++) {
    rig.legs[index]!.quaternion.slerpQuaternions(
      poses[before]![index]!, poses[after]![index]!, position - before);
  }
  const wheelSpin = new THREE.Quaternion().setFromAxisAngle(AXLE, travelledMetres / WHEEL_RADIUS);
  for (const wheel of rig.wheels) wheel.object.quaternion.copy(wheel.original).multiply(wheelSpin);
  rig.crank.object.quaternion.copy(rig.crank.original).multiply(
    new THREE.Quaternion().setFromAxisAngle(AXLE, cycles * Math.PI * 2));
}
