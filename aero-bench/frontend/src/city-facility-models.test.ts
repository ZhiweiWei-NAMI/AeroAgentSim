import { readFileSync } from "node:fs";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { beforeAll, describe, expect, it, vi } from "vitest";
import {
  FACILITY_MIN_DIMENSIONS, createFacilityVisual, disposeFacilityVisual, facilityFootprint,
  facilityLandingPads, loadFacilityVisualAssets, maxLandingParkingSlots,
  selectedFacilityLandingPads,
  type FacilityKind, type FacilityLandingPad, type FacilityVisualAssets,
  type FacilityVisualSpec,
} from "./city-facility-models";

function sample(kind: FacilityKind): FacilityVisualSpec {
  const size = {
    vertiport: { widthM: 14, depthM: 10, heightM: 4.5 },
    hub: { widthM: 18, depthM: 14, heightM: 5.2 },
    charger: { widthM: 10, depthM: 8, heightM: 3.2 },
  }[kind];
  return {
    id: `facility-${kind}`, name: `Test ${kind}`, kind,
    position: { x: 125, z: -63 }, rotationDeg: 35,
    ...size,
    capacity: 3, chargingPowerW: 120_000,
  };
}

function meshes(group: THREE.Object3D): THREE.Mesh[] {
  const result: THREE.Mesh[] = [];
  group.traverse(node => { if (node instanceof THREE.Mesh) result.push(node); });
  return result;
}

/** Selected-city spec with `count` accepted pads centred on the footprint. */
function selectedSpec(kind: "vertiport" | "hub", count: number, rotationDeg: number): FacilityVisualSpec {
  const widthM = 20, depthM = 20;
  const pads = selectedFacilityLandingPads({
    id: `selected-${kind}-${count}`, name: `Selected ${kind} ${count}`, kind,
    position: { x: 0, z: 0 }, rotationDeg,
    widthM, depthM, heightM: 5,
    landing: { parkingSlots: count, movementsPerHour: 30 },
  });
  return {
    id: `selected-${kind}-${count}`, name: `Selected ${kind} ${count}`, kind,
    position: { x: 0, z: 0 }, rotationDeg,
    widthM, depthM, heightM: 5,
    capacity: count, chargingPowerW: 0,
    pads,
  };
}

function worldBounds(mesh: THREE.Mesh): THREE.Box3 {
  return new THREE.Box3().setFromObject(mesh);
}

function padRect(pad: FacilityLandingPad): { minX: number; maxX: number; minZ: number; maxZ: number } {
  return {
    minX: pad.x - pad.widthM / 2, maxX: pad.x + pad.widthM / 2,
    minZ: pad.z - pad.depthM / 2, maxZ: pad.z + pad.depthM / 2,
  };
}

function padPrism(pad: FacilityLandingPad): { minX: number; maxX: number; minZ: number; maxZ: number; minY: number; maxY: number } {
  const rect = padRect(pad);
  return { ...rect, minY: pad.y - 0.05, maxY: pad.y + 3 };
}

function overlapsRectPrism(prism: ReturnType<typeof padPrism>, bounds: THREE.Box3): boolean {
  return bounds.min.x < prism.maxX && bounds.max.x > prism.minX
    && bounds.min.z < prism.maxZ && bounds.max.z > prism.minZ
    && bounds.min.y < prism.maxY && bounds.max.y > prism.minY;
}

//: Mesh names that are the required per-pad visible markings (never obstacles)
//: and the load-bearing carrying surfaces (verified separately).
const PAD_MARKING_MESHES = new Set([
  "H landing mark", "roof H mark", "landing clearance boundary",
  "roof landing guide", "landing circle",
]);
const CARRYING_SURFACES = new Set([
  "raised landing deck", "landing deck support", "sorting hall", "sorting hall roof",
]);

describe("city facility models", () => {
  let assets: FacilityVisualAssets;
  beforeAll(async () => {
    const bytes = readFileSync("public/models/incoming/furniture/glb/bus_stop_4.glb");
    const data = new ArrayBuffer(bytes.byteLength);
    new Uint8Array(data).set(bytes);
    const gltf = await new GLTFLoader().parseAsync(data, "");
    const loader = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockResolvedValue(gltf);
    assets = await loadFacilityVisualAssets();
    expect(await loadFacilityVisualAssets()).toBe(assets);
    expect(loader).toHaveBeenCalledTimes(1);
    loader.mockRestore();
  });

  it("batches the existing passenger shelter into four material draws", () => {
    expect(meshes(assets.passengerShelter)).toHaveLength(4);
    const size = new THREE.Box3().setFromObject(assets.passengerShelter).getSize(new THREE.Vector3());
    expect(size.x).toBeCloseTo(4, 1);
    expect(size.y).toBeCloseTo(3, 1);
    expect(size.z).toBeCloseTo(2.23, 1);
  });

  it.each<FacilityKind>(["vertiport", "hub", "charger"])(
    "%s remains inside its declared collision footprint", kind => {
      const spec = sample(kind);
      const group = createFacilityVisual(spec, assets);
      expect(group.position.toArray()).toEqual([125, 0, -63]);
      expect(group.rotation.y).toBeCloseTo(THREE.MathUtils.degToRad(35));
      expect(group.userData.facilityFootprint).toEqual(facilityFootprint(spec));
      expect(group.userData.target).toEqual({ kind: "entity", id: spec.id });
      expect(group.userData.landingPads).toEqual(facilityLandingPads(spec));
      expect(meshes(group).length).toBeGreaterThan(15);
      const lights: THREE.Light[] = [];
      group.traverse(node => { if (node instanceof THREE.Light) lights.push(node); });
      expect(lights).toHaveLength(0);

      // The model's local geometry is bounded before the editor places or rotates it.
      group.position.set(0, 0, 0);
      group.rotation.y = 0;
      group.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(group);
      const tolerance = 1e-5;
      expect(bounds.min.x).toBeGreaterThanOrEqual(-spec.widthM / 2 - tolerance);
      expect(bounds.max.x).toBeLessThanOrEqual(spec.widthM / 2 + tolerance);
      expect(bounds.min.z).toBeGreaterThanOrEqual(-spec.depthM / 2 - tolerance);
      expect(bounds.max.z).toBeLessThanOrEqual(spec.depthM / 2 + tolerance);
      expect(bounds.min.y).toBeGreaterThanOrEqual(-tolerance);
      expect(bounds.max.y).toBeLessThanOrEqual(spec.heightM + tolerance);
      disposeFacilityVisual(group);
    },
  );

  it("keeps the vertiport H clear of the passenger pavilion", () => {
    const spec = sample("vertiport");
    const group = createFacilityVisual(spec, assets);
    const pad = facilityLandingPads(spec)[0]!;
    expect(pad).toEqual({ x: -1.44, z: 0.08, y: 0.72, widthM: 4.2, depthM: 4.2 });
    expect(group.userData.landingSurfaceY).toBeCloseTo(pad.y);
    expect(meshes(group).filter(mesh => mesh.name === "H landing mark")).toHaveLength(3);
    expect(meshes(group).filter(mesh => mesh.name === "landing clearance boundary")).toHaveLength(4);

    group.position.set(0, 0, 0);
    group.rotation.y = 0;
    group.updateMatrixWorld(true);
    const pavilion = group.getObjectByName("glass passenger pavilion")!;
    const pavilionBounds = new THREE.Box3().setFromObject(pavilion);
    expect(pavilionBounds.min.x).toBeGreaterThan(pad.x + pad.widthM / 2);
    expect(pavilionBounds.min.y).toBeCloseTo(0.08);
    expect(pavilionBounds.max.y).toBeCloseTo(3.08, 1);
    const circle = new THREE.Box3().setFromObject(group.getObjectByName("landing circle")!);
    expect(circle.max.x - circle.min.x).toBeCloseTo(6, 1);
    expect(circle.max.z - circle.min.z).toBeCloseTo(6, 1);
    expect(Math.hypot(pad.widthM / 2, pad.depthM / 2)).toBeLessThan(3);
    disposeFacilityVisual(group);
  });

  it("keeps the logistics roof pad clear and the drone charging pads open to the sky", () => {
    const hubSpec = sample("hub");
    expect(hubSpec.pads).toBeUndefined();
    const hub = createFacilityVisual(hubSpec);
    const pad = facilityLandingPads(hubSpec)[0]!;
    expect(pad).toEqual({ x: -3.08, z: -1.155, y: 3.3625, widthM: 4.76, depthM: 4.4 });
    expect(hub.userData.landingSurfaceY).toBeCloseTo(pad.y);
    hub.position.set(0, 0, 0);
    hub.rotation.y = 0;
    hub.updateMatrixWorld(true);
    expect(meshes(hub).filter(mesh => mesh.name.startsWith("parcel locker"))).toHaveLength(3);
    // The single pad-aware hub renderer paints the accepted single pad (there
    // is no legacy fixed-building visual with a detached roof mark).
    const roofMarks = meshes(hub).filter(mesh => mesh.name === "roof H mark");
    expect(roofMarks).toHaveLength(3);
    const roofH = new THREE.Box3();
    for (const mark of roofMarks) roofH.union(worldBounds(mark));
    const roofHCentre = roofH.getCenter(new THREE.Vector3());
    expect(roofHCentre.x).toBeCloseTo(pad.x, 5);
    expect(roofHCentre.z).toBeCloseTo(pad.z, 5);
    const hubRoof = new THREE.Box3().setFromObject(hub.getObjectByName("sorting hall roof")!);
    expect(hubRoof.min.x).toBeLessThanOrEqual(pad.x - pad.widthM / 2 - 1e-6);
    expect(hubRoof.max.x).toBeGreaterThanOrEqual(pad.x + pad.widthM / 2 + 1e-6);
    expect(hubRoof.min.x).toBeGreaterThanOrEqual(-hubSpec.widthM / 2 - 1e-5);
    expect(hubRoof.max.x).toBeLessThanOrEqual(hubSpec.widthM / 2 + 1e-5);
    disposeFacilityVisual(hub);

    const charger = createFacilityVisual(sample("charger"));
    const chargingPads = facilityLandingPads(sample("charger"));
    expect(chargingPads).toHaveLength(3);
    expect(chargingPads.map(pad => pad.x)).toEqual([-3, 0, 3]);
    expect(chargingPads.every(pad => pad.y === 0.12 && pad.widthM === 2.2 && pad.depthM === 2.2)).toBe(true);
    expect(charger.userData.landingSurfaceY).toBe(0.12);
    expect(meshes(charger).filter(mesh => mesh.name.startsWith("drone charging pad"))).toHaveLength(3);
    charger.position.set(0, 0, 0);
    charger.rotation.y = 0;
    charger.updateMatrixWorld(true);
    const roof = new THREE.Box3().setFromObject(charger.getObjectByName("solar equipment canopy")!);
    for (const chargingPad of chargingPads) {
      expect(roof.max.z).toBeLessThan(chargingPad.z - chargingPad.depthM / 2);
    }
    disposeFacilityVisual(charger);
  });

  it.each(["vertiport", "hub"] as const)(
    "%s with a single pad and no explicit pads stays valid and paints that returned pad", kind => {
      const spec = sample(kind);
      expect(spec.pads).toBeUndefined();
      const group = createFacilityVisual(spec, kind === "vertiport" ? assets : undefined);
      const pads = facilityLandingPads(spec);
      expect(pads).toHaveLength(1);
      const pad = pads[0]!;
      expect(group.userData.landingPads).toEqual(pads);
      group.position.set(0, 0, 0);
      group.rotation.y = 0;
      group.updateMatrixWorld(true);
      // The continuous carrying platform spans the returned pad geometry and
      // never leaves the declared footprint.
      const platformName = kind === "vertiport" ? "raised landing deck" : "sorting hall roof";
      const platform = worldBounds(group.getObjectByName(platformName) as THREE.Mesh);
      const rect = padRect(pad);
      expect(platform.min.x).toBeLessThanOrEqual(rect.minX - 1e-6);
      expect(platform.max.x).toBeGreaterThanOrEqual(rect.maxX + 1e-6);
      expect(platform.min.z).toBeLessThanOrEqual(rect.minZ - 1e-6);
      expect(platform.max.z).toBeGreaterThanOrEqual(rect.maxZ + 1e-6);
      expect(platform.min.x).toBeGreaterThanOrEqual(-spec.widthM / 2 - 1e-5);
      expect(platform.max.x).toBeLessThanOrEqual(spec.widthM / 2 + 1e-5);
      expect(platform.min.z).toBeGreaterThanOrEqual(-spec.depthM / 2 - 1e-5);
      expect(platform.max.z).toBeLessThanOrEqual(spec.depthM / 2 + 1e-5);
      // The per-pad visible marks sit exactly on the returned pad.
      const markName = kind === "vertiport" ? "H landing mark" : "roof H mark";
      const marks = meshes(group).filter(mesh => mesh.name === markName);
      expect(marks).toHaveLength(3);
      const marksBounds = new THREE.Box3();
      for (const mark of marks) marksBounds.union(worldBounds(mark));
      const markCentre = marksBounds.getCenter(new THREE.Vector3());
      expect(markCentre.x).toBeCloseTo(pad.x, 5);
      expect(markCentre.z).toBeCloseTo(pad.z, 5);
      // The two H legs are symmetric about the pad centre.
      const legs = marks.filter(mark => {
        const size = worldBounds(mark).getSize(new THREE.Vector3());
        return size.x < size.z;
      });
      expect(legs).toHaveLength(2);
      const legXs = legs.map(leg => worldBounds(leg).getCenter(new THREE.Vector3()).x).sort((a, b) => a - b);
      expect((legXs[0]! + legXs[1]!) / 2).toBeCloseTo(pad.x, 5);
      disposeFacilityVisual(group);
    },
  );

  it("places local landing coordinates with the facility rotation", () => {
    const spec = { ...sample("vertiport"), rotationDeg: 90 };
    const group = createFacilityVisual(spec, assets);
    const pad = facilityLandingPads(spec)[0]!;
    group.updateMatrixWorld(true);
    const center = group.localToWorld(new THREE.Vector3(pad.x, pad.y, pad.z));
    expect(center.x).toBeCloseTo(spec.position.x + pad.z);
    expect(center.y).toBeCloseTo(pad.y);
    expect(center.z).toBeCloseTo(spec.position.z - pad.x);
    disposeFacilityVisual(group);
  });

  it("disposes owned geometry and per-instance shared materials exactly once", () => {
    const group = createFacilityVisual(sample("charger"));
    const parent = new THREE.Group();
    parent.add(group);
    const geometry = new Set(meshes(group).map(mesh => mesh.geometry));
    const material = new Set(meshes(group).flatMap(mesh =>
      Array.isArray(mesh.material) ? mesh.material : [mesh.material]));
    expect(material.size).toBeLessThan(meshes(group).length);
    const spies = [...geometry, ...material].map(resource => vi.spyOn(resource, "dispose"));
    disposeFacilityVisual(group);
    expect(group.parent).toBeNull();
    expect(group.children).toHaveLength(0);
    for (const spy of spies) expect(spy).toHaveBeenCalledTimes(1);
    disposeFacilityVisual(group);
    for (const spy of spies) expect(spy).toHaveBeenCalledTimes(1);
  });

  it("keeps human-scale fixtures unchanged when the site grows", () => {
    for (const [kind, fixture] of [
      ["vertiport", "glass passenger pavilion"],
      ["hub", "loading bay 1"],
      ["charger", "solar equipment canopy"],
    ] as const) {
      const compact = createFacilityVisual(sample(kind), assets);
      const large = createFacilityVisual({ ...sample(kind), widthM: 40, depthM: 30, heightM: 12 }, assets);
      for (const group of [compact, large]) {
        group.position.set(0, 0, 0);
        group.rotation.y = 0;
        group.updateMatrixWorld(true);
      }
      const compactSize = new THREE.Box3().setFromObject(compact.getObjectByName(fixture)!)
        .getSize(new THREE.Vector3());
      const largeSize = new THREE.Box3().setFromObject(large.getObjectByName(fixture)!)
        .getSize(new THREE.Vector3());
      expect(largeSize.toArray()).toEqual(compactSize.toArray());
      disposeFacilityVisual(compact);
      disposeFacilityVisual(large);
    }
  });

  it.each<FacilityKind>(["vertiport", "hub", "charger"])(
    "%s fits its minimum metre envelope", kind => {
      const spec = { ...sample(kind), ...FACILITY_MIN_DIMENSIONS[kind] };
      const group = createFacilityVisual(spec, assets);
      group.position.set(0, 0, 0);
      group.rotation.y = 0;
      group.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(group);
      expect(bounds.min.x).toBeGreaterThanOrEqual(-spec.widthM / 2 - 1e-5);
      expect(bounds.max.x).toBeLessThanOrEqual(spec.widthM / 2 + 1e-5);
      expect(bounds.min.z).toBeGreaterThanOrEqual(-spec.depthM / 2 - 1e-5);
      expect(bounds.max.z).toBeLessThanOrEqual(spec.depthM / 2 + 1e-5);
      expect(bounds.max.y).toBeLessThanOrEqual(spec.heightM + 1e-5);
      disposeFacilityVisual(group);
    },
  );

  it("preserves batched shelter resources across facility disposal", () => {
    const shelterMeshes = meshes(assets.passengerShelter);
    const spies = shelterMeshes.flatMap(mesh => [
      vi.spyOn(mesh.geometry, "dispose"),
      vi.spyOn(mesh.material as THREE.Material, "dispose"),
    ]);
    const first = createFacilityVisual(sample("vertiport"), assets);
    const second = createFacilityVisual({ ...sample("vertiport"), id: "second" }, assets);
    disposeFacilityVisual(first);
    disposeFacilityVisual(second);
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
      spy.mockRestore();
    }
  });

  it("rejects dimensions and capacity that cannot describe a footprint", () => {
    expect(() => createFacilityVisual({ ...sample("hub"), widthM: 0 })).toThrow(/dimensions/);
    expect(() => createFacilityVisual({ ...sample("charger"), capacity: 0 })).toThrow(/capacity/);
    expect(() => facilityFootprint({ ...sample("vertiport"), rotationDeg: Infinity })).toThrow(/finite/);
    expect(() => createFacilityVisual({ ...sample("charger"), heightM: 2.9 })).toThrow(/at least/);
    expect(() => createFacilityVisual(sample("vertiport"))).toThrow(/passenger shelter/);
    expect(FACILITY_MIN_DIMENSIONS.vertiport).toEqual({ widthM: 12, depthM: 8, heightM: 4 });
  });

  it.each([
    ["vertiport", 1, 0] as const, ["vertiport", 2, 90] as const, ["vertiport", 3, 35] as const,
    ["hub", 1, 0] as const, ["hub", 2, 90] as const, ["hub", 3, 35] as const,
  ])("%s with %i pads at %i deg surfaces every pad footprint on one continuous platform", (kind, count, rotationDeg) => {
    const facilityKind = kind as "vertiport" | "hub";
    const spec = selectedSpec(facilityKind, count, rotationDeg);
    const group = createFacilityVisual(spec, facilityKind === "vertiport" ? assets : undefined);
    group.position.set(0, 0, 0);
    group.rotation.y = 0;
    group.updateMatrixWorld(true);
    const platformName = facilityKind === "vertiport" ? "raised landing deck" : "sorting hall roof";
    const platform = group.getObjectByName(platformName) as THREE.Mesh;
    const deck = worldBounds(platform);
    // The continuous platform spans the whole accepted pad row while staying
    // inside the declared footprint (full-surface coverage, no overhang).
    for (const pad of spec.pads!) {
      const rect = padRect(pad);
      expect(deck.min.x).toBeLessThanOrEqual(rect.minX - 1e-6);
      expect(deck.max.x).toBeGreaterThanOrEqual(rect.maxX + 1e-6);
      expect(deck.min.z).toBeLessThanOrEqual(rect.minZ - 1e-6);
      expect(deck.max.z).toBeGreaterThanOrEqual(rect.maxZ + 1e-6);
      // Strategic location: the pad centre projects onto the carrying deck.
      const centre = group.localToWorld(new THREE.Vector3(pad.x, pad.y, pad.z));
      expect(centre.y).toBeGreaterThan(deck.min.y);
      expect(centre.y).toBeLessThan(deck.max.y + 1e-6);
    }
    expect(deck.min.x).toBeGreaterThanOrEqual(-spec.widthM / 2 - 1e-5);
    expect(deck.max.x).toBeLessThanOrEqual(spec.widthM / 2 + 1e-5);
    expect(deck.min.z).toBeGreaterThanOrEqual(-spec.depthM / 2 - 1e-5);
    expect(deck.max.z).toBeLessThanOrEqual(spec.depthM / 2 + 1e-5);
    // Structural support coverage: exactly four columns under every accepted pad.
    if (facilityKind === "vertiport") {
      const supports = meshes(group).filter(mesh => mesh.name === "landing deck support");
      expect(supports).toHaveLength(4 * count);
    }
    disposeFacilityVisual(group);
  });

  it.each(["vertiport", "hub"] as const)(
    "max-fitting %s parking row keeps the whole platform inside the footprint", kind => {
      const widthM = 20;
      const count = maxLandingParkingSlots(kind, widthM);
      expect(count).toBe(3);
      const spec = selectedSpec(kind, count, 0);
      const group = createFacilityVisual(spec, kind === "vertiport" ? assets : undefined);
      group.position.set(0, 0, 0);
      group.rotation.y = 0;
      group.updateMatrixWorld(true);
      const platformName = kind === "vertiport" ? "raised landing deck" : "sorting hall roof";
      const deck = worldBounds(group.getObjectByName(platformName) as THREE.Mesh);
      // The lip is clamped on a max-fit row: no platform overhang.
      expect(deck.min.x).toBeGreaterThanOrEqual(-spec.widthM / 2 - 1e-5);
      expect(deck.max.x).toBeLessThanOrEqual(spec.widthM / 2 + 1e-5);
      for (const pad of spec.pads!) {
        const rect = padRect(pad);
        expect(deck.min.x).toBeLessThanOrEqual(rect.minX - 1e-6);
        expect(deck.max.x).toBeGreaterThanOrEqual(rect.maxX + 1e-6);
      }
      disposeFacilityVisual(group);
    },
  );

  it.each(["vertiport", "hub"] as const)(
    "%s paints a full H for every pad and leaves no stale lone H", kind => {
      for (const count of [1, 3]) {
        const spec = selectedSpec(kind, count, 0);
        const group = createFacilityVisual(spec, kind === "vertiport" ? assets : undefined);
        group.position.set(0, 0, 0);
        group.rotation.y = 0;
        group.updateMatrixWorld(true);
        const markName = kind === "vertiport" ? "H landing mark" : "roof H mark";
        const marks = meshes(group).filter(mesh => mesh.name === markName);
        expect(marks).toHaveLength(3 * count);
        // Every H box belongs to exactly one pad (three bars per pad) and no mark
        // drifts away from its accepted pad rectangle.
        const perPad = new Map<number, number>();
        for (const mark of marks) {
          const centre = worldBounds(mark).getCenter(new THREE.Vector3());
          const owner = spec.pads!.findIndex(pad => {
            const rect = padRect(pad);
            return centre.x >= rect.minX - 0.2 && centre.x <= rect.maxX + 0.2
              && centre.z >= rect.minZ - 0.2 && centre.z <= rect.maxZ + 0.2;
          });
          expect(owner, `${mark.name} floats away from every pad`).toBeGreaterThanOrEqual(0);
          perPad.set(owner, (perPad.get(owner) ?? 0) + 1);
        }
        for (let index = 0; index < count; index++) {
          expect(perPad.get(index)).toBe(3);
        }
        disposeFacilityVisual(group);
      }
    },
  );

  it.each(["vertiport", "hub"] as const)(
    "%s keeps non-marking decorative geometry out of every usable landing prism", kind => {
      const spec = selectedSpec(kind, 3, 35);
      const group = createFacilityVisual(spec, kind === "vertiport" ? assets : undefined);
      group.position.set(0, 0, 0);
      group.rotation.y = 0;
      group.updateMatrixWorld(true);
      const prisms = spec.pads!.map(padPrism);
      for (const mesh of meshes(group)) {
        if (PAD_MARKING_MESHES.has(mesh.name) || CARRYING_SURFACES.has(mesh.name)) continue;
        const bounds = worldBounds(mesh);
        for (const prism of prisms) {
          expect(overlapsRectPrism(prism, bounds), `${mesh.name} intrudes a pad prism`).toBe(false);
        }
      }
      // The passenger pavilion (vertiport) and the glass dispatch office (hub)
      // individually stay clear of the pad prisms.
      const pavilion = kind === "vertiport" ? group.getObjectByName("glass passenger pavilion") : null;
      if (pavilion !== null && pavilion !== undefined) {
        const pavilionBounds = new THREE.Box3().setFromObject(pavilion);
        for (const prism of prisms) {
          expect(overlapsRectPrism(prism, pavilionBounds), "pavilion intrudes a pad prism").toBe(false);
        }
      }
      disposeFacilityVisual(group);
    },
  );
});
