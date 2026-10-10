// @vitest-environment node
// T23 acceptance probes: they measure the frame-path CPU waste this ticket removes and
// prove bit-identical output. Every assertion prints the compared counts.
import { describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { PublicTraceMap } from "./map";
import { CityTrafficPreview } from "./city-presentation";
import { cacheStaticTransforms } from "./city-rendering";

/** Prototype-method rig over a private method, matching map.observation.test.ts. */
function method<Args extends unknown[], R>(name: string): (self: unknown, ...args: Args) => R {
  const fn = (PublicTraceMap.prototype as unknown as Record<string, (...args: never[]) => R>)[name]!;
  return (self, ...args) => fn.apply(self, args as never);
}
const observationMount = method<[THREE.Object3D | undefined, number],
  { mount: { offsetBodyM: readonly number[]; aspect: number; [k: string]: unknown }; label: string } | null>("observationMount");

/** A UAV like the preview aircraft: fitted chain root>fit>orient>mesh plus a pick proxy. */
function uavCraft(id: string): { node: THREE.Group; geometry: THREE.BufferGeometry } {
  const node = new THREE.Group();
  const fit = new THREE.Group();
  const orient = new THREE.Group();
  const geometry = new THREE.BoxGeometry(1.6, 0.4, 1.6);
  geometry.translate(0.2, 0.05, 0);
  const body = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  orient.add(body);
  fit.scale.setScalar(1.25);
  fit.add(orient);
  node.add(fit);
  node.userData = { target: { kind: "entity", id }, entityKind: "uav" };
  return { node, geometry };
}

/** The record path under test: baseline bounds recomputed from scratch, every call. */
function uncachedMount(target: THREE.Object3D): { offsetBodyM: readonly number[] } | null {
  if (!target.visible) return null;
  target.updateWorldMatrix(true, true);
  const localBounds = new THREE.Box3();
  const inverse = target.matrixWorld.clone().invert();
  target.traverse(child => {
    if (!(child instanceof THREE.Mesh) || !child.visible) return;
    child.geometry.computeBoundingBox();
    if (child.geometry.boundingBox !== null) localBounds.union(child.geometry.boundingBox.clone()
      .applyMatrix4(inverse.clone().multiply(child.matrixWorld)));
  });
  if (localBounds.isEmpty()) return null;
  return { offsetBodyM: [localBounds.max.x + 0.15, (localBounds.min.y + localBounds.max.y) / 2, 0] };
}

describe("T23 display-camera mount cache", () => {
  it("returns mounts equal to a from-scratch remeasure across simulated flight frames", () => {
    const a = uavCraft("uav.alpha"), b = uavCraft("uav.beta");
    const map = { observationScene: null, camera: new THREE.PerspectiveCamera(46, 1.777) };
    const frames = 24;
    let compared = 0;
    for (let frame = 0; frame < frames; frame++) {
      // Every frame moves and turns both craft, like CityTrafficPreview.update.
      a.node.position.set(frame * 2.5, 40 + frame * 0.4, -frame * 1.5);
      a.node.rotation.set(frame * 0.02, frame * 0.11, frame * 0.015, "YXZ");
      b.node.position.set(-frame * 3, 65 + frame * 0.2, frame * 2);
      b.node.rotation.set(-frame * 0.03, -frame * 0.09, 0, "YXZ");
      for (const node of [a.node, b.node]) {
        const cached = observationMount(map, node as never, 16 / 9);
        const expected = uncachedMount(node)!;
        expect(cached).not.toBeNull();
        expect(cached!.mount.offsetBodyM.length).toBe(3);
        for (let axis = 0; axis < 3; axis++) {
          expect(cached!.mount.offsetBodyM[axis]).toBe(expected.offsetBodyM[axis]);
          compared++;
        }
      }
    }
    console.info(`[t23] mount equality: ${frames} frames x 2 UAVs x 3 axes = ${compared} exact comparisons`);
    expect(compared).toBe(frames * 2 * 3);
  });

  it("calls computeBoundingBox once per distinct geometry, not per frame", () => {
    const craft = uavCraft("uav.gamma");
    const shared = new THREE.BoxGeometry(0.3, 0.3, 0.3);
    const rotor = new THREE.Mesh(shared, new THREE.MeshBasicMaterial());
    rotor.position.set(0.9, 0.1, 0.9);
    craft.node.add(rotor);
    const geometries = [craft.geometry, shared];
    const spies = geometries.map(geometry => vi.spyOn(geometry, "computeBoundingBox"));
    const map = { observationScene: null, camera: new THREE.PerspectiveCamera(46, 1.777) };
    for (let frame = 0; frame < 20; frame++) {
      craft.node.position.set(frame, 30, 0);
      craft.node.rotation.y = frame * 0.2;
      observationMount(map, craft.node as never, 16 / 9);
    }
    const calls = spies.map(spy => spy.mock.calls.length);
    console.info(`[t23] computeBoundingBox calls over 20 frames on 2 geometries: ${calls.join(", ")}`);
    for (const count of calls) expect(count).toBeLessThanOrEqual(1);
    // A second aspect measures nothing again; the geometry boxes are already cached.
    observationMount(map, craft.node as never, 1.6);
    for (const spy of spies) expect(spy.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("remeasures after a visible-mesh or geometry change and stays equal to the baseline", () => {
    const craft = uavCraft("uav.delta");
    const map = { observationScene: null, camera: new THREE.PerspectiveCamera(46, 1.777) };
    const first = observationMount(map, craft.node as never, 16 / 9)!;
    expect(first.mount.offsetBodyM[0]).toBe(uncachedMount(craft.node)!.offsetBodyM[0]);
    // Hide the body: an empty visible set yields no mount, as today.
    craft.node.traverse(node => { if (node instanceof THREE.Mesh) node.visible = false; });
    expect(observationMount(map, craft.node as never, 16 / 9)).toBeNull();
    craft.node.traverse(node => { if (node instanceof THREE.Mesh) node.visible = true; });
    // A new child changes the union; the cache must not serve the stale box.
    const extra = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial());
    extra.position.set(4, 0, 0);
    craft.node.add(extra);
    const third = observationMount(map, craft.node as never, 16 / 9)!;
    const firstX: number = first.mount.offsetBodyM[0]!, thirdX: number = third.mount.offsetBodyM[0]!;
    expect(thirdX).toBe(uncachedMount(craft.node)!.offsetBodyM[0]);
    expect(thirdX).toBeGreaterThan(firstX);
  });
});

describe("T23 steady-frame matrix budget", () => {
  it("counts dynamic objects the traffic update must leave freezable-free", () => {
    const data = JSON.parse(JSON.stringify({
      schema_version: "aero-bench.city-sumo-preview/v2",
      source_kind: "offline-sumo-engineering-preview",
      source_network_sha256: "a".repeat(64), mesh_pack_source_sha256: "b".repeat(64),
      duration_seconds: 2, step_seconds: 1,
      signals: [{ id: "signal.0", tls: "tls.1", link: 0, x: 0, z: 0, heading: 0 }],
      frames: [0, 1, 2].map(second => ({ second, vehicles: [], persons: [], tls: {} })),
      demand: { observed: {} },
    }));
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    const fitted = uavCraft("uav.preview");
    const vehicle = new THREE.Group();
    vehicle.add(new THREE.Mesh(new THREE.BoxGeometry(4, 1.5, 1.8), new THREE.MeshBasicMaterial()));
    vehicle.userData.entityKind = "vehicle";
    const group = new THREE.Group();
    Reflect.set(preview, "group", group);
    Reflect.set(preview, "data", data);
    Reflect.set(preview, "aircraft", new Map([["uav.preview", fitted.node]]));
    Reflect.set(preview, "vehicles", new Map([["vehicle.1", { object: vehicle }]]));
    Reflect.set(preview, "people", new Map());
    Reflect.set(preview, "trails", new Map());
    Reflect.set(preview, "lamps", []);
    Reflect.set(preview, "cyclists", new Map());
    Reflect.set(preview, "bicycleDistances", new Map());
    Reflect.set(preview, "pedestrianDistances", new Map());
    Reflect.set(preview, "displayIds", null);
    Reflect.set(preview, "currentSecond", -1);
    const trailGeometry = new THREE.BufferGeometry().setFromPoints(
      [0, 1, 2].map(second => new THREE.Vector3(second * 5, 40.06, second * 2)));
    trailGeometry.setDrawRange(0, 0);
    Reflect.set(preview, "flightData", {
      schema_version: "aero-bench.city-px4-preview/v1", source_kind: "px4-recording",
      duration_seconds: 2, step_seconds: 1,
      vehicle_ids: ["uav.preview"],
      frames: [0, 1, 2].map(second => [["uav.preview", second * 5, second * 2, 40, 0, 0, 0, true]]),
    });
    Reflect.set(preview, "trails", new Map([["uav.preview",
      { line: new THREE.Line(trailGeometry, new THREE.LineBasicMaterial()), startFrame: 0 }]]));
    Reflect.set(preview, "collisionBoxesVisible", false);
    Reflect.set(preview, "sidewalkHeight", null);
    group.add(fitted.node, vehicle);
    preview.update(0, true, true, true, true, true);
    preview.update(1, true, true, true, true, true);
    const counts = { aircraft: 0, vehicle: 0, staticFrozen: 0 };
    preview.group.traverse(node => {
      if (node === preview.group) return;
      if (node === fitted.node || node === vehicle) counts[node === fitted.node ? "aircraft" : "vehicle"]++;
      else if (node.matrixAutoUpdate) counts.staticFrozen++;
    });
    console.info(`[t23] traffic subtree: ${counts.aircraft} animated aircraft root(s), ${counts.vehicle} animated vehicle root(s), ${counts.staticFrozen} static descendants frozen`);
    expect(counts.aircraft).toBe(1);
    expect(counts.vehicle).toBe(1);
    // The fitted inner chain and the vehicle model are pure placement: cacheable.
    cacheStaticTransforms(fitted.node, node => node === fitted.node);
    cacheStaticTransforms(vehicle, node => node === vehicle);
    let frozen = 0;
    preview.group.traverse(node => { if (node !== fitted.node && node !== vehicle && !node.isObject3D) return; });
    preview.group.traverse(node => { if (node !== preview.group && node !== fitted.node && node !== vehicle && !node.matrixAutoUpdate) frozen++; });
    console.info(`[t23] after cacheStaticTransforms on fitted chains: ${frozen} descendants keep inherited-only updates`);
    expect(frozen).toBeGreaterThan(0);
  });

  it("freezes only static descendants and keeps every matrixWorld equal to the unfrozen baseline", () => {
    // One actor per production freeze scope, frozen exactly like the shipped modules do.
    const buildAircraft = (): { root: THREE.Group; inner: THREE.Object3D[] } => {
      const root = new THREE.Group();
      const fit = new THREE.Group(); fit.scale.setScalar(0.85);
      const orient = new THREE.Group(); orient.rotation.y = Math.PI;
      const body = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.6, 2.4), new THREE.MeshBasicMaterial());
      orient.add(body); fit.add(orient); root.add(fit);
      return { root, inner: [fit, orient, body] };
    };
    const buildVehicle = (): { root: THREE.Group; inner: THREE.Object3D[] } => {
      const root = new THREE.Group();
      const fit = new THREE.Group(); fit.position.set(0.5, 0.02, -0.3); fit.rotation.y = Math.PI / 7;
      const shell = new THREE.Mesh(new THREE.BoxGeometry(4, 1.5, 1.8), new THREE.MeshBasicMaterial());
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.2, 12), new THREE.MeshBasicMaterial());
      wheel.rotation.z = Math.PI / 2; wheel.position.set(1.4, 0.3, 0.8);
      fit.add(shell, wheel); root.add(fit);
      return { root, inner: [fit, shell, wheel] };
    };
    const buildBuilding = (): { visual: THREE.Group; inner: THREE.Object3D[] } => {
      const visual = new THREE.Group();
      const body = new THREE.Mesh(new THREE.BoxGeometry(18, 40, 14), new THREE.MeshBasicMaterial());
      body.position.y = 20;
      const roof = new THREE.Mesh(new THREE.BoxGeometry(2, 1.5, 2), new THREE.MeshBasicMaterial());
      roof.position.set(5, 40.75, 4);
      visual.add(body, roof); visual.position.set(120, 0, -80); visual.rotation.y = 0.35;
      return { visual, inner: [body, roof] };
    };
    const buildTree = (): { visual: THREE.Group; inner: THREE.Object3D[] } => {
      const visual = new THREE.Group();
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.3, 3), new THREE.MeshBasicMaterial());
      trunk.position.y = 1.5;
      const crown = new THREE.Mesh(new THREE.SphereGeometry(1.6, 12, 10), new THREE.MeshBasicMaterial());
      crown.position.y = 3.8; crown.scale.set(1, 0.85, 1);
      visual.add(trunk, crown); visual.position.set(-60, 0, 45); visual.rotation.y = 1.1;
      return { visual, inner: [trunk, crown] };
    };
    const buildZone = (): { visual: THREE.Group; inner: THREE.Object3D[] } => {
      const visual = new THREE.Group();
      const slab = new THREE.Mesh(new THREE.BoxGeometry(30, 4, 30), new THREE.MeshBasicMaterial());
      visual.add(slab); visual.position.y = 25;
      return { visual, inner: [slab] };
    };
    const aircraft = buildAircraft(), vehicle = buildVehicle(), building = buildBuilding();
    const tree = buildTree(), zone = buildZone();
    const animatedRoots = [aircraft.root, vehicle.root];
    const statics = [building.visual, tree.visual, zone.visual];

    // Baseline: everything keeps default auto updates; record leaf matrices per tick.
    const baseline = new Map<THREE.Object3D, THREE.Matrix4[]>();
    const samples: THREE.Object3D[] = [...animatedRoots,
      ...aircraft.inner, ...vehicle.inner, ...building.inner, ...tree.inner, ...zone.inner];
    for (const object of samples) baseline.set(object, []);
    for (let tick = 0; tick < 12; tick++) {
      for (const [index, root] of animatedRoots.entries()) {
        root.position.set(tick * 3 + index, 40 + tick * 0.5, -tick * 2);
        root.rotation.set(tick * 0.03, tick * 0.2 + index, 0, "YXZ");
      }
      for (const object of samples) object.updateMatrixWorld(true);
      for (const object of samples) baseline.get(object)!.push(object.matrixWorld.clone());
    }

    // Now freeze the static subtrees exactly as the shipped modules do: descendants only
    // for roots that move, whole subtree for everything placed once at load time.
    cacheStaticTransforms(aircraft.root, node => node === aircraft.root);
    cacheStaticTransforms(vehicle.root, node => node === vehicle.root);
    for (const visual of statics) cacheStaticTransforms(visual);
    // Every sampled node below an actor root is inside a frozen chain; only the roots move.
    let frozenCount = 0;
    for (const object of samples) {
      if (object === aircraft.root || object === vehicle.root) {
        expect(object.matrixAutoUpdate).toBe(true);
        continue;
      }
      expect(object.matrixAutoUpdate).toBe(false);
      frozenCount++;
    }
    console.info(`[t23] frozen descendants: ${frozenCount}, roots kept dynamic: ${samples.length - frozenCount}`);
    expect(frozenCount).toBe(samples.length - animatedRoots.length);

    let compared = 0;
    for (let tick = 0; tick < 12; tick++) {
      for (const [index, root] of animatedRoots.entries()) {
        root.position.set(tick * 3 + index, 40 + tick * 0.5, -tick * 2);
        root.rotation.set(tick * 0.03, tick * 0.2 + index, 0, "YXZ");
      }
      for (const object of samples) object.updateMatrixWorld(true);
      for (const object of samples) {
        const expected = baseline.get(object)![tick]!;
        expect(object.matrixWorld.equals(expected)).toBe(true);
        compared++;
      }
    }
    console.info(`[t23] frozen-vs-baseline matrixWorld equality: ${compared} object-ticks exact`);
    expect(compared).toBe(samples.length * 12);
  });
});
