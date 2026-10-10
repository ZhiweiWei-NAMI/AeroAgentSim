// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { PublicTraceMap } from "./map";

const methods = PublicTraceMap.prototype as unknown as Record<"render" | "observationFocus" | "setCameraMode" | "updateCamera" | "observationTarget" | "observationMount" | "navigateObservation" | "observationTime", (...args: any[]) => any>;
function rig() {
  const camera = new THREE.PerspectiveCamera(46, 16 / 9, 0.4, 14000);
  camera.position.set(60, 40, 30);
  const target = new THREE.Group();
  target.userData = { target: { kind: "entity", id: "uav.one" }, entityKind: "uav" };
  target.add(new THREE.Mesh(new THREE.BoxGeometry(2, 1, 3), new THREE.MeshBasicMaterial()));
  target.position.set(10, 20, 30);
  const map = {
    camera, mode: "free", root: { dataset: {} as Record<string, string> },
    controls: { target: new THREE.Vector3(0, 0, 0), enabled: true, update: vi.fn() },
    dynamic: new Map([["entity:uav.one", target]]), observationScene: null,
    observationSelection: { kind: "entity", id: "uav.one" }, observationView: null,
    follow: null, workspaceFollowId: null, previewFollowId: null,
    operationsPreview: null, trafficPreview: null, overviewCamera: null,
    overviewTarget: new THREE.Vector3(), updatingPreviewCamera: false,
    renderObservation: vi.fn(), clearWorkspaceFollow: vi.fn(),
    setCameraMode: methods.setCameraMode, updateCamera: methods.updateCamera,
    observationTarget: methods.observationTarget, observationMount: methods.observationMount,
  };
  return { map, camera, target };
}

describe("linked observation integration", () => {
  it("requires a selected aircraft instead of silently choosing the first UAV", () => {
    const { map, camera } = rig();
    map.observationSelection = null as any;
    const before = camera.position.clone();
    methods.setCameraMode.call(map, "cockpit");
    expect(map.mode).toBe("free");
    expect(map.root.dataset.observationCameraState).toBe("unavailable");
    expect(camera.position.equals(before)).toBe(true);
  });

  it("attaches the authored display mount in the moving body frame", () => {
    const { map, camera, target } = rig();
    methods.setCameraMode.call(map, "cockpit");
    const offset = camera.position.clone().sub(target.position);
    expect(offset.x).toBeCloseTo(1.15);
    expect(offset.y).toBeCloseTo(0);
    target.position.add(new THREE.Vector3(4, 7, 8));
    target.quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);
    methods.updateCamera.call(map);
    expect(camera.position.clone().sub(target.position).distanceTo(offset.applyQuaternion(target.quaternion))).toBeLessThan(1e-8);
    expect(camera.fov).toBe(60);
    expect(camera.near).toBe(0.05);
    expect(map.root.dataset.observationCameraSource).toContain("作者");
  });

  it("restores overview projection and retains selection when leaving onboard", () => {
    const { map, camera } = rig();
    const before = camera.position.clone(), selection = map.observationSelection;
    methods.setCameraMode.call(map, "cockpit");
    methods.setCameraMode.call(map, "free");
    expect(camera.position.distanceTo(before)).toBeLessThan(1e-8);
    expect(camera.fov).toBe(46);
    expect(camera.near).toBe(0.4);
    expect(camera.userData.observationHorizontalMirror).toBe(false);
    expect(map.observationSelection).toBe(selection);
  });

  it("map navigation leaves onboard visibly and moves only the observation camera", () => {
    const { map, camera, target } = rig();
    const vehiclePosition = target.position.clone(), selection = map.observationSelection;
    methods.setCameraMode.call(map, "cockpit");
    methods.navigateObservation.call(map, { x: 200, z: -100 });
    expect(map.mode).toBe("free");
    expect(map.controls.target.x).toBe(200);
    expect(map.controls.target.z).toBe(-100);
    expect(camera.position.x).toBe(260);
    expect(target.position.equals(vehiclePosition)).toBe(true);
    expect(map.observationSelection).toBe(selection);
    expect(map.renderObservation).toHaveBeenCalledWith(true);
  });

  it("uses the exact recorded scene clock while playback is paused or seeking backwards", () => {
    const map = { observationScene: { sceneState: { at: { tick: 20, sim_time_ns: 3_000_000_000 } } }, previewSeconds: 99 };
    expect(methods.observationTime.call(map)).toBe(3);
    map.observationScene.sceneState.at = { tick: 2, sim_time_ns: 500_000_000 };
    expect(methods.observationTime.call(map)).toBe(0.5);
  });
  it("keeps the authored preview clock separate from an unconnected live control context", () => {
    const map = { observationScene: { scenario: null, sceneState: null,
      operationContext: { sourceKind: "live", timeSeconds: 0 } }, previewSeconds: 30.25 };
    expect(methods.observationTime.call(map)).toBe(30.25);
  });

  it("leaves onboard and clears sensor identity when its target disappears", () => {
    const { map, target } = rig();
    methods.setCameraMode.call(map, "cockpit");
    target.visible = false;
    methods.updateCamera.call(map);
    expect(map.mode).toBe("free");
    expect(map.root.dataset.observationCameraState).toBe("unavailable");
    expect(map.root.dataset.observationSensorId).toBeUndefined();
    expect(map.root.dataset.observationCameraSource).toBeUndefined();
  });

  it("retains authoritative selection when a new source reuses the same selection object", () => {
    const selected = { kind: "entity", id: "uav.one" };
    const map = { operationsMonitor: {}, observationSource: "run.before", observationSelection: selected,
      observationView: { selected }, mode: "free", destroyed: true };
    methods.render.call(map, { operationContext: { runId: "run.after" } }, { selected });
    expect(map.observationSelection).toBe(selected);
    expect(map.observationSource).toBe("run.after");
  });

  it("locates the onboard and external observer at its current camera instead of a saved orbit target", () => {
    const { map, camera } = rig();
    expect(methods.observationFocus.call(map)).toBe(map.controls.target);
    methods.setCameraMode.call(map, "cockpit");
    expect(methods.observationFocus.call(map)).toBe(camera.position);
    camera.position.set(200, 35, -100);
    map.mode = "chase";
    expect(methods.observationFocus.call(map).toArray()).toEqual([200, 35, -100]);
  });

});
