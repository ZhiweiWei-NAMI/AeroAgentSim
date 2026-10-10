// One-shot cross-language fixture generator: executes the REAL frontend pad
// functions and captures their output into frontend_pads_fixture.json.
//
// Run from the repository frontend/ directory with the read-only node_modules
// link present (see FACILITY_GEOMETRY_REPORT.txt for the exact command):
//
//   cd frontend
//   ./node_modules/.bin/vitest run ../tests/tasks/fixtures/logistics_facility_geometry/generate_facility_pads_fixture.test.ts --root .. --environment node
//
// The produced JSON is the independently captured frontend ground truth used by
// tests/tasks/test_logistics_facility_geometry.py.  World coordinates are read
// from the ACTUAL three.js matrixWorld of the ACTUAL frontend visual factory
// (createFacilityVisual), which applies the exact position/rotation transform
// that the selected-city dispatch route estimator's padFor uses.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { describe, expect, it, vi } from "vitest";
import {
  createFacilityVisual, facilityLandingPads, loadFacilityVisualAssets, toFacilityVisualSpec,
  type FacilityVisualSource,
} from "../../../../frontend/src/city-facility-models";

const REPO_ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const MODEL_SRC = fileURLToPath(new URL("../../../../frontend/src/city-facility-models.ts", import.meta.url));
const ROUTE_SRC = fileURLToPath(new URL("../../../../frontend/src/city-selected-dispatch-route.ts", import.meta.url));
const FIXTURE_PATH = fileURLToPath(new URL("./frontend_pads_fixture.json", import.meta.url));

/** World transform for a local pad, read from the actual three.js matrixWorld. */
function worldPad(
  matrixElements: ArrayLike<number>,
  pad: { x: number; y: number; z: number },
  facility: FacilityVisualSource,
  index: number,
): Record<string, number | string> {
  // three.js Matrix4 elements are column-major:
  // [m11,m21,m31,m41, m12,m22,m32,m42, m13,m23,m33,m43, m14,m24,m34,m44]
  const [m11, m21, m31, , m12, m22, m32, , m13, m23, m33, , m14, m24, m34] = matrixElements;
  return {
    facility_id: facility.id,
    pad_index: index,
    x: m11 * pad.x + m12 * pad.y + m13 * pad.z + m14,
    y: m21 * pad.x + m22 * pad.y + m23 * pad.z + m24,
    z: m31 * pad.x + m32 * pad.y + m33 * pad.z + m34,
    width_m: pad.widthM,
    depth_m: pad.depthM,
    rotation_deg: facility.rotationDeg,
    frame: "scene_east_south_m",
  };
}

/** padFor replica used ONLY as a generator self-check against matrixWorld. */
function padForFormula(
  facility: FacilityVisualSource,
  pad: { x: number; y: number; z: number },
): { x: number; y: number; z: number } {
  const angle = facility.rotationDeg * Math.PI / 180;
  const cos = Math.cos(angle), sin = Math.sin(angle);
  return {
    x: facility.position.x + cos * pad.x + sin * pad.z,
    y: pad.y + (facility.supportHeightM ?? 0),
    z: facility.position.z - sin * pad.x + cos * pad.z,
  };
}

function sha256File(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

async function loadPassengerShelter() {
  const assetPath = fileURLToPath(new URL("../../../../frontend/public/models/incoming/furniture/glb/bus_stop_4.glb", import.meta.url));
  const bytes = readFileSync(assetPath);
  const data = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(data).set(bytes);
  const gltf = await new GLTFLoader().parseAsync(data, "");
  const spy = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockResolvedValue(gltf as never);
  try {
    return await loadFacilityVisualAssets();
  } finally {
    spy.mockRestore();
  }
}

/** Full v3 authoring facility shape (placement/building/cargo carried here). */
type AuthoringFacility = FacilityVisualSource & {
  placement: "ground" | "rooftop";
  buildingId: string | null;
  cargo?: { storageCapacityKg: number; throughputPerHourKg: number } | null;
};

const CASES: ReadonlyArray<{
  case_id: string;
  facility: AuthoringFacility;
}> = [
  {
    case_id: "vp_ground_3pads_rot35",
    facility: {
      id: "vp-ground-3", name: "Ground Vertiport 3", kind: "vertiport",
      placement: "ground", buildingId: null, supportHeightM: null,
      position: { x: 125, z: -63 }, rotationDeg: 35,
      widthM: 20, depthM: 20, heightM: 5,
      landing: { parkingSlots: 3, movementsPerHour: 30 },
    },
  },
  {
    case_id: "vp_ground_1pad_rot90",
    facility: {
      id: "vp-ground-1", name: "Ground Vertiport 1", kind: "vertiport",
      placement: "ground", buildingId: null, supportHeightM: null,
      position: { x: 12, z: -28 }, rotationDeg: 90,
      widthM: 14, depthM: 10, heightM: 4.5,
      landing: { parkingSlots: 1, movementsPerHour: 60 },
    },
  },
  {
    case_id: "vp_rooftop_2pads_support150_rot90",
    facility: {
      id: "vp-rooftop-2", name: "Rooftop Vertiport 2", kind: "vertiport",
      placement: "rooftop", buildingId: "building.42", supportHeightM: 150,
      position: { x: 10, z: -20 }, rotationDeg: 90,
      widthM: 20, depthM: 20, heightM: 5,
      landing: { parkingSlots: 2, movementsPerHour: 15 },
    },
  },
  {
    case_id: "vp_rooftop_1pad_support150_rot30",
    facility: {
      id: "vp-rooftop-1", name: "Rooftop Vertiport 1", kind: "vertiport",
      placement: "rooftop", buildingId: "building.7", supportHeightM: 150,
      position: { x: 204, z: 118 }, rotationDeg: 30,
      widthM: 14, depthM: 10, heightM: 4.5,
      landing: { parkingSlots: 1, movementsPerHour: 45 },
    },
  },
  {
    case_id: "hub_ground_2pads_rot30",
    facility: {
      id: "hub-ground-2", name: "Cargo Hub 2", kind: "hub",
      placement: "ground", buildingId: null, supportHeightM: null,
      position: { x: 30, z: -40 }, rotationDeg: 30,
      widthM: 18, depthM: 14, heightM: 5.2,
      landing: { parkingSlots: 2, movementsPerHour: 20 },
      cargo: { storageCapacityKg: 10000, throughputPerHourKg: 10000 },
    },
  },
  {
    case_id: "hub_ground_1pad_rot0",
    facility: {
      id: "hub-ground-1", name: "Cargo Hub 1", kind: "hub",
      placement: "ground", buildingId: null, supportHeightM: null,
      position: { x: -8, z: 66 }, rotationDeg: 0,
      widthM: 18, depthM: 14, heightM: 5.2,
      landing: { parkingSlots: 1, movementsPerHour: 40 },
      cargo: { storageCapacityKg: 10000, throughputPerHourKg: 10000 },
    },
  },
  {
    case_id: "ch_ground_3pads_rot30",
    facility: {
      id: "ch-ground-3", name: "Charger 3", kind: "charger",
      placement: "ground", buildingId: null, supportHeightM: null,
      position: { x: 50, z: -60 }, rotationDeg: 30,
      widthM: 10, depthM: 8, heightM: 3.2,
      charging: { slots: 3, powerW: 5000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
    },
  },
  {
    case_id: "ch_ground_1pad_rot90",
    facility: {
      id: "ch-ground-1", name: "Charger 1", kind: "charger",
      placement: "ground", buildingId: null, supportHeightM: null,
      position: { x: -40, z: 5 }, rotationDeg: 90,
      widthM: 10, depthM: 8, heightM: 3.2,
      charging: { slots: 1, powerW: 7500, priceAmount: 0.8, priceCurrency: "USD", priceUnit: "kWh" },
    },
  },
];

describe("frontend facility pad fixture generation", () => {
  it("executes the real frontend pad functions and writes the fixture JSON", async () => {
    const assets = await loadPassengerShelter();
    const gitCommit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: REPO_ROOT })
      .toString().trim();
    const cases = CASES.map(captureCase);
    const fixture = {
      schema: "frontend_facility_landing_pads_v1",
      frame: "scene_east_south_m",
      generated_at: "2026-09-29",
      frontend_commit: gitCommit,
      model_source_sha256: sha256File(MODEL_SRC),
      route_source_sha256: sha256File(ROUTE_SRC),
      node_version: process.version,
      three_version: "0.185.1",
      cases,
    };
    writeFileSync(FIXTURE_PATH, `${JSON.stringify(fixture, null, 2)}\n`);
    expect(fixture.cases.length).toBeGreaterThanOrEqual(8);

    function captureCase(entry: (typeof CASES)[number]) {
      const facility = entry.facility;
      const spec = toFacilityVisualSpec(facility);
      const localPads = facilityLandingPads(spec);
      const visual = createFacilityVisual(spec, facility.kind === "vertiport" ? assets : undefined);
      visual.updateMatrixWorld(true);
      const elements = visual.matrixWorld.elements;
      const worldPads = localPads.map((pad, index) => worldPad(elements, pad, facility, index));
      // Self-check: the real three.js world matrix must equal the route
      // estimator's padFor transform for every pad on every case.
      localPads.forEach((pad, index) => {
        const expected = padForFormula(facility, pad);
        expect(worldPads[index]!.x).toBeCloseTo(expected.x, 9);
        expect(worldPads[index]!.y).toBeCloseTo(expected.y, 9);
        expect(worldPads[index]!.z).toBeCloseTo(expected.z, 9);
      });
      return {
        case_id: entry.case_id,
        facility: {
          id: facility.id, name: facility.name, kind: facility.kind,
          placement: facility.placement, buildingId: facility.buildingId,
          supportHeightM: facility.supportHeightM,
          position: facility.position, rotationDeg: facility.rotationDeg,
          widthM: facility.widthM, depthM: facility.depthM, heightM: facility.heightM,
          landing: facility.landing ?? null,
          cargo: facility.cargo ?? null,
          charging: facility.charging ?? null,
        },
        local_pads: localPads.map(pad => ({ x: pad.x, y: pad.y, z: pad.z, widthM: pad.widthM, depthM: pad.depthM })),
        pads: worldPads,
      };
    }
  });
});
