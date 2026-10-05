// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { applyStaticFixtureClearance, loadStaticFixtureClearance, signalFixtureIsOmitted,
  validateStaticFixtureClearance, type StaticFixtureClearance } from "./city-static-fixture-clearance";
import { disposePresentation } from "./city-presentation";

function data(): StaticFixtureClearance {
  const signals = [{ id: "signal.0", tls: "tls.0", link: 0, x: 0, z: 0, heading: 10 },
    { id: "signal.1", tls: "tls.0", link: 1, x: 10, z: 0, heading: 0 }];
  const lamps = [0, 1, 2, 3].map(x => ({ x, z: 10, rotation_deg: 0 }));
  const hit = [{ building_id: "building.way.1.component.0", overlap_m2: .02 }];
  return {
    schema_version: "aero-bench.city-static-fixture-clearance/v1",
    source_kind: "canonical-building-footprints-and-transformed-fixture-triangles",
    policy: "omit-conflicting-render-fixtures-preserve-source-traffic",
    sources: { objects_json_sha256: "a".repeat(64), building_render_manifest_sha256: "b".repeat(64),
      road_preview_sha256: "c".repeat(64), traffic_preview_sha256: "d".repeat(64),
      signal_model_sha256: "e".repeat(64), street_lamp_model_sha256: "f".repeat(64),
      source_network_sha256: "1".repeat(64) },
    source_inventories: { signals, street_lamps: lamps },
    omitted_signals: [{ source_index: 0, source_location: signals[0]!,
      reason: "fixture_intersects_source_building", hits: hit }],
    omitted_street_lamps: [{ source_index: 1, source_location: lamps[1]!,
      reason: "fixture_intersects_source_building", hits: hit }],
    statistics: { source_buildings: 1, source_signals: 2, source_street_lamps: 4,
      omitted_source_signals: 1, omitted_source_street_lamps: 1 },
  };
}

function groups(value: StaticFixtureClearance): { roads: THREE.Group; signals: THREE.Group } {
  const roads = new THREE.Group(), signals = new THREE.Group();
  roads.userData.streetLampLocations = value.source_inventories.street_lamps;
  for (const name of ["street_light_8 SG1", "street_light_8 SG2", "Warm street lamp ground illumination"]) {
    const node = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial(), 4);
    node.name = name;
    for (let index = 0; index < 4; index++) node.setMatrixAt(index, new THREE.Matrix4().makeTranslation(index, 0, 10));
    roads.add(node);
  }
  for (const source of value.source_inventories.signals) {
    const node = new THREE.Group();
    node.position.set(source.x, 0, source.z); node.rotation.y = -source.heading * Math.PI / 180;
    node.userData.target = { kind: "traffic_signal", id: source.id };
    node.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial()));
    signals.add(node);
  }
  return { roads, signals };
}

describe("source-bound static fixture omissions", () => {
  it.each(Object.keys(data().sources))("rejects drift in %s", key => {
    const value = data();
    const expected = { ...value.sources, [key]: "0".repeat(64) };
    expect(() => validateStaticFixtureClearance(value, expected)).toThrow(/source digest/);
  });

  it("rejects invented locations and empty building contact receipts", () => {
    const value = data();
    // A JSON roundtrip detaches omission locations from the source inventory.
    const detached = JSON.parse(JSON.stringify(value)) as StaticFixtureClearance;
    (detached.omitted_signals[0]!.source_location as { x: number }).x = 999;
    expect(() => validateStaticFixtureClearance(detached, value.sources)).toThrow(/source inventory/);
    const unmeasured = { ...value, omitted_signals: [{ ...value.omitted_signals[0]!, hits: [] }] };
    expect(() => validateStaticFixtureClearance(unmeasured, value.sources)).toThrow(/measured contact/);
  });

  it("omits fixture meshes and compacts every lamp part without changing source traffic", () => {
    const value = data(), { roads, signals } = groups(value);
    const tls = { "tls.0": "rG" }, before = JSON.stringify({ inventory: value.source_inventories, tls });
    const result = applyStaticFixtureClearance(value, roads, signals,
      value.source_inventories.signals, value.source_inventories.street_lamps);
    expect(result).toMatchObject({ sourceSignalCount: 2, signalFixtureCount: 1, sourceLampCount: 4,
      previousLampCount: 4, lampFixtureCount: 3, omittedDisplayedLampSourceIndices: [1] });
    expect(signals.children[0]!.children).toHaveLength(1);
    expect(signals.children[0]!.visible).toBe(false);
    expect(signalFixtureIsOmitted(signals.children[0]!)).toBe(true);
    expect(signalFixtureIsOmitted(signals.children[1]!)).toBe(false);
    for (const node of roads.children as THREE.InstancedMesh[]) {
      expect(node.count).toBe(3);
      const matrix = new THREE.Matrix4();
      node.getMatrixAt(1, matrix); expect(matrix.elements[12]).toBe(2);
    }
    expect(roads.userData.streetLampLocations).toEqual([0, 2, 3].map(index => value.source_inventories.street_lamps[index]));
    expect(JSON.stringify({ inventory: value.source_inventories, tls })).toBe(before);
  });

  it("rejects a shifted loaded signal before altering any rendered lamp", () => {
    const value = data(), { roads, signals } = groups(value);
    signals.children[0]!.position.x += 5;
    expect(() => applyStaticFixtureClearance(value, roads, signals,
      value.source_inventories.signals, value.source_inventories.street_lamps)).toThrow(/source pose/);
    expect((roads.children[0] as THREE.InstancedMesh).count).toBe(4);
    expect(signals.children[0]!.children).toHaveLength(1);
  });

  it("rejects source omissions from another road inventory", () => {
    const value = data(), { roads, signals } = groups(value);
    expect(() => applyStaticFixtureClearance(value, roads, signals, value.source_inventories.signals,
      value.source_inventories.street_lamps.slice(1))).toThrow(/source inventory/);
  });

  it("keeps omitted clone resources owned by the scene until ordinary disposal", () => {
    const value = data(), { roads, signals } = groups(value);
    const omittedMesh = signals.children[0]!.children[0] as THREE.Mesh;
    const visibleMesh = signals.children[1]!.children[0] as THREE.Mesh;
    const sharedGeometry = omittedMesh.geometry;
    visibleMesh.geometry.dispose();
    visibleMesh.geometry = sharedGeometry;
    const omittedMaterial = omittedMesh.material as THREE.Material;
    signals.children[0]!.add(new THREE.Mesh(sharedGeometry, omittedMaterial));
    const geometryDisposal = vi.spyOn(sharedGeometry, "dispose");
    const omittedMaterialDisposal = vi.spyOn(omittedMaterial, "dispose");
    const visibleMaterialDisposal = vi.spyOn(visibleMesh.material as THREE.Material, "dispose");
    applyStaticFixtureClearance(value, roads, signals,
      value.source_inventories.signals, value.source_inventories.street_lamps);
    expect(signals.children[0]!.children).toHaveLength(2);
    expect(signals.children[1]!.visible).toBe(true);
    expect(geometryDisposal).not.toHaveBeenCalled();
    expect(omittedMaterialDisposal).not.toHaveBeenCalled();
    disposePresentation(signals);
    expect(geometryDisposal).toHaveBeenCalledTimes(1);
    expect(omittedMaterialDisposal).toHaveBeenCalledTimes(1);
    expect(visibleMaterialDisposal).toHaveBeenCalledTimes(1);
    expect(signals.children).toHaveLength(0);
  });

  it("aborts a pending fixture fetch with the scene request", async () => {
    const value = data(), controller = new AbortController();
    const fetchFixture = vi.fn((_url: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
      options.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchFixture);
    try {
      const pending = loadStaticFixtureClearance({ url: "/fixture.json", sha256: "0".repeat(64), size_bytes: 1 },
        value.sources, controller.signal);
      controller.abort();
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(fetchFixture).toHaveBeenCalledWith("/fixture.json", { redirect: "error", signal: controller.signal });
    } finally { vi.unstubAllGlobals(); }
  });

  it("cancels a pending response body when fixture loading is aborted", async () => {
    const value = data(), controller = new AbortController(), cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel: cancelled });
    vi.stubGlobal("fetch", async () => new Response(body));
    try {
      const pending = loadStaticFixtureClearance({ url: "/fixture.json", sha256: "0".repeat(64), size_bytes: 1 },
        value.sources, controller.signal);
      await Promise.resolve();
      controller.abort();
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(cancelled).toHaveBeenCalledTimes(1);
    } finally { vi.unstubAllGlobals(); }
  });

  it("verifies the real generated omission bytes and rejects a byte change", async () => {
    const bytes = readFileSync(resolve("public/city-presentation/huangpu-ground-static-fixture-clearance-v1.json"));
    const value = JSON.parse(bytes.toString()) as StaticFixtureClearance;
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    const sha256 = [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("");
    vi.stubGlobal("fetch", async () => new Response(bytes));
    const ref = { url: "/fixture-clearance.json", sha256, size_bytes: bytes.byteLength };
    await expect(loadStaticFixtureClearance(ref, value.sources)).resolves.toMatchObject({
      statistics: { source_buildings: 414, source_signals: 170, omitted_source_signals: 2,
        source_street_lamps: 467, omitted_source_street_lamps: 8 },
    });
    const changed = Uint8Array.from(bytes); changed[changed.length - 1] = 32;
    vi.stubGlobal("fetch", async () => new Response(changed));
    await expect(loadStaticFixtureClearance(ref, value.sources)).rejects.toThrow(/declared digest/);
    vi.unstubAllGlobals();
  });
});
