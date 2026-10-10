import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { footChain, pedestrianYaw, recordedBicycleDistances, updateChainWorld, recordedPedestrianDistances,
  resolveVerifiedFbxUrl, setCityBuildingLighting, CityTrafficPreview,
  parseCityTrafficData } from "./city-presentation";
import { CITY_LIGHTING } from "./city-lighting";
import type { StaticSignalFixtureOmission } from "./city-static-fixture-clearance";

function validTrafficData(): Record<string, unknown> {
  const signals = Array.from({ length: 6 }, (_, link) => ({
    id: `signal.${link}`, tls: "tls.1", link, x: link * 2, z: -link, heading: link * 15,
  }));
  const frame = (second: number) => ({ second,
    vehicles: [["vehicle.1", second, 2, 90, "sedan", 0.075]],
    persons: [["person.1", -second, 3, 180, 0.225]], tls: { "tls.1": "GryGry" } });
  return {
    schema_version: "aero-bench.city-sumo-preview/v2",
    source_kind: "offline-sumo-engineering-preview",
    source_network_sha256: "a".repeat(64), mesh_pack_source_sha256: "b".repeat(64),
    duration_seconds: 1, step_seconds: 0.5, signals,
    frames: [frame(0), frame(0.5), frame(1)],
    demand: { observed: { vehicles: 1, sedan: 1, persons: 1 } },
  };
}

function clonedTrafficData(): Record<string, any> {
  return JSON.parse(JSON.stringify(validTrafficData())) as Record<string, any>;
}

describe("traffic artifact validation", () => {
  it("accepts the shipped measured v2 traffic artifact", () => {
    const path = resolve("public/city-presentation/huangpu-canonical-traffic-v2.json");
    const parsed = parseCityTrafficData(JSON.parse(readFileSync(path, "utf8")));
    expect(parsed.frames).toHaveLength(parsed.duration_seconds / parsed.step_seconds + 1);
    expect(parsed.signals.length).toBeGreaterThan(0);
  });

  it("accepts a structurally complete six-signal artifact without a city-specific count floor", () => {
    const value = validTrafficData();
    const parsed = parseCityTrafficData(value);
    expect(parsed).toBe(value);
    expect(parsed.signals).toHaveLength(6);
  });

  it("accepts an empty signal inventory only when every TLS frame is also empty", () => {
    const empty = clonedTrafficData();
    empty.signals = [];
    for (const frame of empty.frames) frame.tls = {};
    expect(parseCityTrafficData(empty).signals).toEqual([]);
    empty.frames[1].tls = { "undeclared.tls": "G" };
    expect(() => parseCityTrafficData(empty)).toThrow(/TLS identities/);
  });

  it("rejects broken frame grids, signal identities, TLS links, and actor type drift", () => {
    const offGrid = clonedTrafficData(); offGrid.frames[1].second = 0.6;
    expect(() => parseCityTrafficData(offGrid)).toThrow(/frame 1/);

    const repeatedSignal = clonedTrafficData(); repeatedSignal.signals[1].id = repeatedSignal.signals[0].id;
    expect(() => parseCityTrafficData(repeatedSignal)).toThrow(/duplicate signal identity/);

    const missingLink = clonedTrafficData(); missingLink.frames[1].tls["tls.1"] = "G";
    expect(() => parseCityTrafficData(missingLink)).toThrow(/invalid state for TLS/);

    const changedType = clonedTrafficData(); changedType.frames[2].vehicles[0][4] = "taxi";
    expect(() => parseCityTrafficData(changedType)).toThrow(/changes type/);

    const repeatedActor = clonedTrafficData(); repeatedActor.frames[0].persons[0][0] = "vehicle.1";
    expect(() => parseCityTrafficData(repeatedActor)).toThrow(/repeats actor/);
  });
});

describe("verified signal fixture binding", () => {
  it("rejects a missing verification context before any traffic, model or flight load", async () => {
    const fetch = vi.fn();
    const gltf = vi.spyOn(GLTFLoader.prototype, "loadAsync"), fbx = vi.spyOn(FBXLoader.prototype, "loadAsync");
    vi.stubGlobal("fetch", fetch);
    try {
      // @ts-expect-error Exercise missing required input from JavaScript callers.
      await expect(CityTrafficPreview.load("/verified-traffic", "/verified-flight"))
        .rejects.toThrow(/requires verified visual assets/);
      expect(fetch).not.toHaveBeenCalled();
      expect(gltf).not.toHaveBeenCalled(); expect(fbx).not.toHaveBeenCalled();
    } finally { vi.unstubAllGlobals(); gltf.mockRestore(); fbx.mockRestore(); }
  });

  it("exposes failed verification before starting the traffic models or flight loads", async () => {
    const traffic = readFileSync(resolve("public/city-presentation/huangpu-ground-sumo-preview-v1.json"));
    const fetch = vi.fn(async () => new Response(traffic));
    const url = vi.fn(async () => { throw new Error("signal fixture digest mismatch"); });
    const gltf = vi.spyOn(GLTFLoader.prototype, "loadAsync");
    vi.stubGlobal("fetch", fetch);
    try {
      await expect(CityTrafficPreview.load("/verified-traffic", "/verified-flight", { url }, "all-source-signals"))
        .rejects.toThrow(/signal fixture digest mismatch/);
      expect(url).toHaveBeenCalledExactlyOnceWith("/models/incoming/furniture/glb/traffic_light_4.glb");
      expect(fetch).toHaveBeenCalledExactlyOnceWith("/verified-traffic");
      expect(gltf).not.toHaveBeenCalled();
    } finally { vi.unstubAllGlobals(); gltf.mockRestore(); }
  });

  it("rejects a false flight source claim before loading any presentation model", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(validTrafficData())))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        schema_version: "aero-bench.city-planned-flight-preview/v2",
        source_kind: "planned-visual-flight", physical_simulation: true,
      })));
    const url = vi.fn(async () => "blob:https://viewer.example/verified-signal");
    const gltf = vi.spyOn(GLTFLoader.prototype, "loadAsync"), fbx = vi.spyOn(FBXLoader.prototype, "loadAsync");
    vi.stubGlobal("fetch", fetch);
    try {
      await expect(CityTrafficPreview.load("/verified-traffic", "/verified-flight", { url }, "all-source-signals"))
        .rejects.toThrow(/physical-simulation claim/);
      expect(fetch).toHaveBeenNthCalledWith(1, "/verified-traffic");
      expect(fetch).toHaveBeenNthCalledWith(2, "/verified-flight");
      expect(gltf).not.toHaveBeenCalled(); expect(fbx).not.toHaveBeenCalled();
    } finally { vi.unstubAllGlobals(); gltf.mockRestore(); fbx.mockRestore(); }
  });
});

describe("omitted source signal fixture visibility", () => {
  it("retains the omission when every frame reapplies distance visibility", () => {
    const omitted = new THREE.Group(), shown = new THREE.Group(), far = new THREE.Group();
    const omission: StaticSignalFixtureOmission = {
      source_index: 0, reason: "fixture_intersects_source_building",
      source_location: { id: "source-signal", tls: "source-tls", link: 0, x: 0, z: 0, heading: 0 },
      hits: [{ building_id: "source-building", overlap_m2: 0.5 }],
    };
    omitted.userData.staticFixtureOmission = omission;
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    Reflect.set(preview, "lamps", [
      { object: omitted, location: { x: 0, z: 0 } },
      { object: shown, location: { x: 5, z: 0 } },
      { object: far, location: { x: 250, z: 0 } },
    ]);
    const camera = new THREE.PerspectiveCamera(); camera.position.set(0, 5, 0);
    expect(preview.setSignalVisibility(camera, true)).toBe(1);
    expect(omitted.visible).toBe(false);
    expect(shown.visible).toBe(true);
    expect(far.visible).toBe(false);
    expect(preview.setSignalVisibility(camera, false)).toBe(0);
    expect(preview.setSignalVisibility(camera, true)).toBe(1);
    expect(omitted.visible).toBe(false);
    expect(omitted.userData.staticFixtureOmission).toBe(omission);
  });
});

describe("city building light transitions", () => {
  it("retains day and twilight output while giving night separate window and reflection levels", () => {
    const group = new THREE.Group();
    const glass = new THREE.MeshStandardMaterial({ roughness: 0.35 });
    glass.userData.glassFacade = true;
    const wall = new THREE.MeshStandardMaterial({ roughness: 0.9 });
    const storefront = new THREE.InstancedMesh(new THREE.PlaneGeometry(), wall, 1);
    group.userData.windowMaterials = [glass, wall];
    group.userData.storefrontLights = storefront;
    const day = new THREE.Texture(), twilight = new THREE.Texture(), night = new THREE.Texture();
    setCityBuildingLighting(group, "twilight", twilight);
    expect(glass.emissiveIntensity).toBe(1.2);
    expect(wall.emissiveIntensity).toBe(0);
    expect(storefront.visible).toBe(true);
    setCityBuildingLighting(group, "night", night);
    expect(glass.emissiveIntensity).toBeCloseTo(1.8, 6);
    expect(glass.envMapIntensity).toBeCloseTo(0.34, 6);
    expect(wall.envMapIntensity).toBeCloseTo(0.28, 6);
    expect(storefront.visible).toBe(true);
    setCityBuildingLighting(group, "day", day);
    expect(glass.envMap).toBe(day);
    expect(wall.envMap).toBe(day);
    expect(glass.emissiveIntensity).toBe(0);
    expect(storefront.visible).toBe(false);
    expect(glass.roughness).toBe(0.35);
    expect(wall.roughness).toBe(0.9);
    expect(glass.envMapIntensity).toBe(CITY_LIGHTING.day.facadeEnvironmentIntensity);
    expect(wall.envMapIntensity).toBe(CITY_LIGHTING.day.environmentIntensity);
    const version = glass.version;
    setCityBuildingLighting(group, "day", day);
    expect(glass.version).toBe(version);
    glass.dispose(); wall.dispose(); day.dispose(); twilight.dispose(); night.dispose(); storefront.geometry.dispose();
  });
});

describe("vehicle light transitions", () => {
  it("retains twilight output and increases local-source contrast at night", () => {
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    const front = new THREE.MeshStandardMaterial(), rear = new THREE.MeshStandardMaterial();
    const light = new THREE.SpotLight(), target = new THREE.Object3D();
    const vehicle = new THREE.Group(); vehicle.visible = true; vehicle.position.set(1, 0, 0);
    Reflect.set(preview, "vehicleLamps", [{ material: front, rear: false }, { material: rear, rear: true }]);
    Reflect.set(preview, "headlightBeams", [{ light, target }]);
    Reflect.set(preview, "vehicleLightingTimeOfDay", null);
    Reflect.set(preview, "vehicles", new Map([["vehicle.001", { object: vehicle, type: "sedan", cyclist: null }]]));
    const camera = new THREE.PerspectiveCamera();
    expect(preview.setVehicleLighting(camera, "twilight")).toBe(1);
    expect(front.emissiveIntensity).toBe(1.8); expect(rear.emissiveIntensity).toBe(1.1);
    expect(light.intensity).toBe(110);
    expect(preview.setVehicleLighting(camera, "night")).toBe(1);
    expect(front.emissiveIntensity).toBe(2.4); expect(rear.emissiveIntensity).toBe(1.5);
    expect(light.intensity).toBe(165);
    expect(preview.setVehicleLighting(camera, "day")).toBe(0);
    expect(front.emissiveIntensity).toBe(0); expect(rear.emissiveIntensity).toBe(0);
    expect(light.intensity).toBe(0);
    front.dispose(); rear.dispose(); light.dispose();
  });
});

describe("selected city verified FBX dependencies", () => {
  it("allows only the verified model and alias blobs, rejecting an undeclared blob texture", () => {
    const model = "blob:https://viewer.example/verified-model";
    const texture = "blob:https://viewer.example/verified-texture";
    const alias = "/models/bigcity/images/verified.webp";
    const urls = new Map([["/models/bigcity/fbx/model.fbx", model], [alias, texture]]);
    const aliases = { "buildings modern.png": alias };
    expect(resolveVerifiedFbxUrl(model, model, aliases, urls)).toBe(model);
    expect(resolveVerifiedFbxUrl("buildings modern.png", model, aliases, urls)).toBe(texture);
    expect(resolveVerifiedFbxUrl(texture, model, aliases, urls)).toBe(texture);
    expect(() => resolveVerifiedFbxUrl("blob:https://viewer.example/unverified-texture", model, aliases, urls))
      .toThrow(/未声明或未验证/);
  });
});

describe("independent city replay clocks", () => {
  it("wraps the flight on its declared duration rather than the SUMO duration", () => {
    const preview = Object.create(CityTrafficPreview.prototype) as CityTrafficPreview;
    const trafficFrame = (second: number) => ({ second, vehicles: [], persons: [], tls: {} });
    Reflect.set(preview, "data", { duration_seconds: 2, step_seconds: 1,
      frames: [trafficFrame(0), trafficFrame(1), trafficFrame(2)], signals: [] });
    const flightFrame = (x: number) => [["uav.01", x, 0, 10, 0, 0, 0, true] as const];
    Reflect.set(preview, "flightData", { duration_seconds: 3, step_seconds: 1,
      frames: [flightFrame(0), flightFrame(10), flightFrame(20), flightFrame(30)] });
    Reflect.set(preview, "displayIds", null); Reflect.set(preview, "currentSecond", -1);
    Reflect.set(preview, "vehicles", new Map()); Reflect.set(preview, "people", new Map());
    Reflect.set(preview, "lamps", []);
    const aircraft = new THREE.Group();
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(), new THREE.Vector3(10, 0, 0), new THREE.Vector3(20, 0, 0),
      new THREE.Vector3(30, 0, 0),
    ]), new THREE.LineBasicMaterial());
    Reflect.set(preview, "aircraft", new Map([["uav.01", aircraft]]));
    Reflect.set(preview, "trails", new Map([["uav.01", { line, startFrame: 0 }]]));

    preview.update(2.5, false, false, false, true, true);

    expect(aircraft.position.x).toBeCloseTo(25, 6);
    expect(line.geometry.drawRange.count).toBe(3);
    line.geometry.dispose(); (line.material as THREE.Material).dispose();
  });
});

describe("recorded bicycle motion", () => {
  it("stops wheel travel during a halt and after a missing trajectory segment", () => {
    const frame = (second: number, x?: number) => ({
      second, persons: [], tls: {},
      vehicles: x === undefined ? [] : [["bicycle.001", x, 0, 0, "bicycle"] as const],
    });
    const distances = recordedBicycleDistances([
      frame(0, 0), frame(0.25, 1), frame(0.5, 1), frame(0.75), frame(1, 10),
    ]);
    expect(Array.from(distances.get("bicycle.001")!)).toEqual([0, 1, 1, 1, 1]);
  });
});

describe("recorded pedestrian motion", () => {
  it("updates only the root-to-foot chain to the matrices a forced subtree update gives", () => {
    const rig = () => {
      const root = new THREE.Group(), fitted = new THREE.Object3D(), hips = new THREE.Bone();
      fitted.scale.setScalar(0.01); fitted.updateMatrix(); fitted.matrixAutoUpdate = false; // static fit wrapper
      root.add(fitted); fitted.add(hips);
      const leg = (side: number) => {
        const thigh = new THREE.Bone(), shin = new THREE.Bone(), foot = new THREE.Bone();
        thigh.position.set(side * 10, -5, 0); shin.position.set(0, -45, 0); foot.position.set(0, -42, 3);
        hips.add(thigh); thigh.add(shin); shin.add(foot); return [thigh, shin, foot] as const;
      };
      const left = leg(1), right = leg(-1), arm = new THREE.Bone(); hips.add(arm);
      return { root, hips, left, right, arm };
    };
    const pose = (r: ReturnType<typeof rig>, t: number) => {
      r.root.position.set(3, 0.2, -4); r.root.rotation.y = 0.7; r.hips.position.y = 95 + t;
      r.left[0].rotation.x = 0.4 * t; r.left[1].rotation.x = -0.3; r.right[0].rotation.x = -0.4 * t; r.arm.rotation.z = t;
    };
    const parent = new THREE.Group(); parent.position.set(1, 2, 3); parent.updateMatrixWorld(true);
    const actual = rig(), expected = rig();
    parent.add(actual.root); parent.add(expected.root);
    const chain = footChain(actual.root, [actual.left[2], actual.right[2]]);
    expect(chain).toEqual([actual.root, actual.root.children[0], actual.hips, ...actual.left, ...actual.right]);
    actual.root.updateMatrixWorld(true);
    for (const t of [0.25, 1]) {
      pose(actual, t); pose(expected, t);
      const arm = vi.spyOn(actual.arm.matrixWorld, "multiplyMatrices");
      updateChainWorld(chain); expected.root.updateMatrixWorld(true);
      expect(arm).not.toHaveBeenCalled(); arm.mockRestore();
      for (const [a, b] of [[actual.left[2], expected.left[2]], [actual.right[2], expected.right[2]]] as const) {
        expect(a.matrixWorld.elements).toEqual(b.matrixWorld.elements);
      }
      // The render's scene update still recomputes the whole moved rig.
      expect(actual.root.matrixWorldNeedsUpdate).toBe(true);
    }
    expect(() => footChain(actual.root, [expected.left[2]])).toThrow(/is not below/);
  });

  it("faces the supplied Citizens model along the recorded SUMO walking direction", () => {
    const path = resolve("public/city-presentation/huangpu-night-sumo-preview-v1.json");
    const { frames } = JSON.parse(readFileSync(path, "utf8")) as {
      frames: { persons: [string, number, number, number][] }[];
    };
    let checked = 0;
    for (let index = 0; index < frames.length - 1; index += 20) {
      const next = new Map(frames[index + 1]!.persons.map(person => [person[0], person]));
      for (const sample of frames[index]!.persons) {
        const after = next.get(sample[0]);
        if (after === undefined) continue;
        const travel = new THREE.Vector3(after[1] - sample[1], 0, after[2] - sample[2]);
        if (travel.length() < 0.03) continue;
        const modelForward = new THREE.Vector3(0, 0, 1).applyAxisAngle(
          new THREE.Vector3(0, 1, 0), pedestrianYaw(sample[3]));
        expect(modelForward.dot(travel.normalize())).toBeGreaterThan(0.9);
        checked++;
      }
    }
    expect(checked).toBeGreaterThan(300);
  });

  it("advances gait by distance and does not invent travel across a trajectory gap", () => {
    const frame = (second: number, x?: number) => ({
      second, vehicles: [], tls: {},
      persons: x === undefined ? [] : [["person.001", x, 0, 0] as const],
    });
    const distances = recordedPedestrianDistances([
      frame(0, 0), frame(0.25, 0.375), frame(0.5, 0.375), frame(0.75), frame(1, 10), frame(1.25, 10.5),
    ]);
    expect(Array.from(distances.get("person.001")!)).toEqual([0, 0.375, 0.375, 0.375, 0.375, 0.875]);
  });
});
