// @vitest-environment node
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { CityOperationsPreview } from "./city-operations-preview";
import { createDefaultCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";

function model(): THREE.Group {
  const root = new THREE.Group();
  root.add(new THREE.Mesh(new THREE.BoxGeometry(3.1, 1.585831, 3.1), new THREE.MeshBasicMaterial()));
  return root;
}

function makePreview(scene: THREE.Scene,
                     loadModel: (url: string) => Promise<THREE.Group>): CityOperationsPreview {
  return new CityOperationsPreview(scene, loadModel, async () => {
    const passengerShelter = new THREE.Group();
    passengerShelter.add(new THREE.Mesh(new THREE.BoxGeometry(4, 3, 2.23)));
    return { passengerShelter };
  });
}

function facility(overrides: Partial<CityWorkspaceConfig["facilities"][number]> = {}): CityWorkspaceConfig["facilities"][number] {
  return {
    id: "port.a", name: "A 港", kind: "vertiport", position: { x: 120, z: -80 },
    rotationDeg: 30, widthM: 20, depthM: 20, heightM: 8, capacity: 4, chargingPowerW: 0,
    ...overrides,
  };
}

function config(): CityWorkspaceConfig {
  const result = createDefaultCityWorkspaceConfig();
  result.facilities = [facility()];
  result.fleet = [{ ...result.fleet[0]!, id: "squad", count: 2, homeFacilityId: "port.a" }];
  return result;
}

describe("city operations authoring preview", () => {
  it("places every aircraft on separate declared pad space and samples its vertical cycle", async () => {
    const scene = new THREE.Scene();
    let loads = 0;
    const preview = makePreview(scene, async () => { loads++; return model(); });
    const issues = await preview.setConfig(config(), { obstacles: [] });
    expect(issues).toEqual([]);
    expect(loads).toBe(2);
    expect(preview.renderEstimate()).toEqual({ drawCalls: 31, triangles: 139_515 });
    const a = preview.entityPosition("squad.01")!;
    const b = preview.entityPosition("squad.02")!;
    expect(a.distanceTo(b)).toBeGreaterThan(1.4);
    expect(a.y).toBeCloseTo(0.72);
    expect(b.y).toBeCloseTo(0.72);
    expect(preview.group.children.filter(child => child.userData.entityKind === "uav")).toHaveLength(2);
    const body = preview.group.children.find(child => child.userData.target?.id === "squad.01")!;
    expect(new THREE.Box3().setFromObject(body).min.y).toBeCloseTo(0.72);
    preview.update(5);
    expect(preview.entityPosition("squad.01")!.y).toBeCloseTo(6.72);
    preview.update(12);
    expect(preview.entityPosition("squad.01")!.y).toBeCloseTo(0.72);
    expect(preview.entityChoices().map(choice => choice.id)).toEqual(["port.a", "squad.01", "squad.02"]);
    preview.dispose();
    expect(scene.children).not.toContain(preview.group);
  });

  it("reports missing homes, excess capacity, and insufficient landing geometry without placing aircraft", async () => {
    const scene = new THREE.Scene();
    let loads = 0;
    const preview = makePreview(scene, async () => { loads++; return model(); });
    const missing = config();
    missing.fleet[0]!.homeFacilityId = null;
    expect((await preview.setConfig(missing, { obstacles: [] })).map(issue => issue.code)).toEqual(["missing_home"]);
    expect(preview.entityChoices()).toEqual([{ id: "port.a", label: "A 港" }]);

    const overCapacity = config();
    overCapacity.facilities[0]!.capacity = 1;
    expect((await preview.setConfig(overCapacity, { obstacles: [] })).map(issue => issue.code)).toEqual(["capacity_exceeded"]);

    const crowded = config();
    crowded.facilities[0]!.capacity = 10;
    crowded.fleet[0]!.count = 10;
    expect((await preview.setConfig(crowded, { obstacles: [] })).map(issue => issue.code)).toEqual(["insufficient_landing_space"]);
    expect(loads).toBe(0);
    preview.dispose();
  });

  it("uses a shared occupancy table for fleets at one facility", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = config();
    draft.fleet = [
      { ...draft.fleet[0]!, id: "alpha", count: 1 },
      { ...draft.fleet[0]!, id: "beta", count: 1 },
    ];
    expect(await preview.setConfig(draft, { obstacles: [] })).toEqual([]);
    expect(preview.entityPosition("alpha")!.distanceTo(preview.entityPosition("beta")!)).toBeGreaterThan(1.4);
    preview.dispose();
  });

  it("shows the configured no-fly volume only during its time window", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = createDefaultCityWorkspaceConfig();
    draft.fleet = [];
    draft.airspace = [{ id: "zone.a", name: "限飞", polygon: [
      { x: 10, z: 20 }, { x: 20, z: 20 }, { x: 20, z: 30 }, { x: 10, z: 30 },
    ], floorM: 15, ceilingM: 45, startsAtS: 5, endsAtS: 10,
    source: { kind: "manual", label: "作者" } }];
    expect(await preview.setConfig(draft)).toEqual([]);
    const zone = preview.group.children.find(child => child.userData.target?.id === "zone.a")!;
    expect(zone.visible).toBe(false);
    preview.update(6);
    expect(zone.visible).toBe(true);
    const bounds = new THREE.Box3().setFromObject(zone);
    expect(bounds.min.x).toBeCloseTo(10);
    expect(bounds.min.y).toBeCloseTo(15);
    expect(bounds.min.z).toBeCloseTo(20);
    expect(bounds.max.x).toBeCloseTo(20);
    expect(bounds.max.y).toBeCloseTo(45);
    expect(bounds.max.z).toBeCloseTo(30);
    preview.update(11);
    expect(zone.visible).toBe(false);
    preview.dispose();
  });

  it("clamps manual keyframe endpoints and interpolates authored positions linearly", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = config();
    draft.fleet[0]!.count = 1;
    draft.stateKeyframes = [
      { id: "frame.a", atS: 10, entityId: "squad", position: { x: 1, y: 10, z: 2 }, label: "A" },
      { id: "frame.b", atS: 20, entityId: "squad", position: { x: 11, y: 20, z: 12 }, label: "B" },
    ];
    expect(await preview.setConfig(draft, { obstacles: [] })).toEqual([]);
    expect(preview.entityPosition("squad")!.toArray()).toEqual([1, 10, 2]);
    expect(preview.entityLabel("squad")).toBe("A");
    preview.update(15);
    expect(preview.entityPosition("squad")!.toArray()).toEqual([6, 15, 7]);
    expect(preview.entityLabel("squad")).toBe("A");
    preview.update(25);
    expect(preview.entityPosition("squad")!.toArray()).toEqual([11, 20, 12]);
    expect(preview.entityLabel("squad")).toBe("B");
    preview.dispose();
  });

  it("checks entire manual segments and future cycles against timed no-fly areas", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = config();
    draft.fleet[0]!.count = 1;
    draft.airspace = [{ id: "blocked", name: "航路禁飞", polygon: [
      { x: -1, z: -1 }, { x: 1, z: -1 }, { x: 1, z: 1 }, { x: -1, z: 1 },
    ], floorM: 5, ceilingM: 15, startsAtS: 1, endsAtS: 2,
    source: { kind: "manual", label: "作者" } }];
    draft.stateKeyframes = [
      { id: "left", atS: 1, entityId: "squad", position: { x: -10, y: 10, z: 0 }, label: "起点" },
      { id: "right", atS: 2, entityId: "squad", position: { x: 10, y: 10, z: 0 }, label: "终点" },
    ];
    expect((await preview.setConfig(draft, { obstacles: [] })).map(issue => issue.code))
      .toContain("airspace_incursion");
    expect(preview.flightSegments().find(segment => segment.startsAtS === 1 && segment.endsAtS === 2)?.source)
      .toBe("manual");

    draft.stateKeyframes = [];
    draft.airspace[0] = { ...draft.airspace[0]!, polygon: [
      { x: 117, z: -81 }, { x: 119, z: -81 }, { x: 119, z: -79 }, { x: 117, z: -79 },
    ], startsAtS: 101, endsAtS: 107 };
    expect((await preview.setConfig(draft, { obstacles: [] })).map(issue => issue.code))
      .toContain("airspace_incursion");
    preview.dispose();
  });

  it("rejects flight collisions with supplied building geometry and requires that context", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = config();
    draft.fleet[0]!.count = 1;
    draft.facilities[0]!.rotationDeg = 0;
    const noContext = await preview.setConfig(draft);
    expect(noContext.map(issue => issue.code)).toContain("missing_collision_context");
    const origin = preview.entityPosition("squad")!;
    const obstacles = [{ id: "building.a", x: origin.x, z: origin.z,
      widthM: 1, depthM: 1, heightM: 10, rotationDeg: 0 }];
    expect((await preview.setConfig(draft, { obstacles })).map(issue => issue.code))
      .toContain("building_collision");
    expect(preview.entityPosition("squad")).toBeNull();
    preview.dispose();
  });

  it("rejects two manually authored aircraft paths that cross at the same time", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = config();
    draft.fleet = [
      { ...draft.fleet[0]!, id: "alpha", count: 1 },
      { ...draft.fleet[0]!, id: "beta", count: 1 },
    ];
    draft.stateKeyframes = [
      { id: "a1", entityId: "alpha", atS: 1, position: { x: -10, y: 10, z: 0 }, label: "左" },
      { id: "a2", entityId: "alpha", atS: 3, position: { x: 10, y: 10, z: 0 }, label: "右" },
      { id: "b1", entityId: "beta", atS: 1, position: { x: 10, y: 10, z: 0 }, label: "右" },
      { id: "b2", entityId: "beta", atS: 3, position: { x: -10, y: 10, z: 0 }, label: "左" },
    ];
    const issues = await preview.setConfig(draft, { obstacles: [] });
    const collision = issues.find(issue => issue.code === "aircraft_collision");
    expect(collision?.entityIds).toEqual(["alpha", "beta"]);
    expect(collision?.atS).toBeCloseTo(2);
    expect(preview.entityPosition("alpha")).toBeNull();
    expect(preview.entityPosition("beta")).toBeNull();
    expect(preview.group.children.filter(child => child.userData.entityKind === "uav")).toHaveLength(0);
    preview.dispose();
  });

  it("checks a manual hold against another aircraft's automatic takeoff", async () => {
    const preview = makePreview(new THREE.Scene(), async () => model());
    const draft = config();
    draft.fleet = [
      { ...draft.fleet[0]!, id: "alpha", count: 1 },
      { ...draft.fleet[0]!, id: "beta", count: 1 },
    ];
    expect(await preview.setConfig(draft, { obstacles: [] })).toEqual([]);
    const origin = preview.entityPosition("alpha")!;
    draft.stateKeyframes = [{ id: "hold", entityId: "beta", atS: 0,
      position: { x: origin.x, y: origin.y + 6, z: origin.z }, label: "悬停" }];
    const issues = await preview.setConfig(draft, { obstacles: [] });
    expect(issues.some(issue => issue.code === "aircraft_collision"
      && issue.entityIds?.includes("alpha") && issue.entityIds?.includes("beta"))).toBe(true);
    expect(preview.entityPosition("alpha")).toBeNull();
    expect(preview.entityPosition("beta")).toBeNull();
    preview.dispose();
  });

  it("rejects complete home groups when the scene render budget is exceeded", async () => {
    let loads = 0;
    const preview = makePreview(new THREE.Scene(), async () => { loads++; return model(); });
    const draft = config();
    draft.facilities = Array.from({ length: 4 }, (_, index) => facility({
      id: `port.${index}`, name: `港 ${index}`, position: { x: 120 + index * 100, z: -80 }, capacity: 4,
    }));
    draft.fleet = draft.facilities.map((home, index) => ({
      ...draft.fleet[0]!, id: `squad.${index}`, count: 4, homeFacilityId: home.id,
    }));
    expect((await preview.setConfig(draft, { obstacles: [] })).map(issue => issue.code))
      .toContain("preview_render_budget");
    const visible = preview.group.children.filter(child => child.userData.entityKind === "uav");
    expect(visible.length).toBeLessThan(16);
    for (const fleet of draft.fleet) {
      const count = visible.filter(child => String(child.userData.target?.id).startsWith(`${fleet.id}.`)).length;
      expect([0, 4]).toContain(count);
    }
    expect(loads).toBe(2);
    preview.dispose();
  });

  it("does not attach a stale model after a newer config supersedes its load", async () => {
    let resolveModel!: (source: THREE.Group) => void;
    const pending = new Promise<THREE.Group>(resolve => { resolveModel = resolve; });
    const preview = makePreview(new THREE.Scene(), async () => pending);
    const first = preview.setConfig(config(), { obstacles: [] });
    const second = config();
    second.fleet = [];
    expect(await preview.setConfig(second, { obstacles: [] })).toEqual([]);
    resolveModel(model());
    expect(await first).toEqual([]);
    expect(preview.entityChoices()).toEqual([{ id: "port.a", label: "A 港" }]);
    preview.dispose();
  });
});
