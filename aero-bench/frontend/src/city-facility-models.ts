import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { cacheStaticTransforms } from "./city-rendering";
import type { VerifiedVisualAssets } from "./city-authoring-api";

export type FacilityKind = "vertiport" | "hub" | "charger";
/** Ground site or a rooftop site supported by a verified selected building. */
export type FacilityPlacement = "ground" | "rooftop";

/** Explicit landing-pad capability.
 *
 * `parkingSlots` is the physical concurrent pad count: exactly that many distinct
 * 4.2 × 4.2 m (vertiport) / 4.76 × 4.4 m (hub) landing rectangles are laid out
 * inside the footprint (`facilityLandingPads`), and the dispatch planner reserves
 * one pad per unit on the ground. `movementsPerHour` is a separate declared
 * per-pad planning throughput rate and does not imply more pads than
 * `parkingSlots`. Intermediate handling reserves 3600 / movementsPerHour
 * seconds on that pad; multiple pads may operate concurrently. This is a
 * declared handling assumption, not a measured facility-wide flight rate. */
export interface FacilityLanding {
  readonly parkingSlots: number;
  readonly movementsPerHour: number;
}
/** Explicit hub cargo capability with SI units. */
export interface FacilityCargo {
  readonly storageCapacityKg: number;
  readonly throughputPerHourKg: number;
}
/** Explicit charging capability. Price carries its own currency and unit. */
export interface FacilityCharging {
  readonly slots: number;
  readonly powerW: number;
  readonly priceAmount: number;
  readonly priceCurrency: string;
  readonly priceUnit: "kWh";
}

/** Horizontal pitch between standalone charger stations in the model. */
export const CHARGER_SLOT_PITCH_M = 3;

/** Landing pad layout for each landing-capable kind. Pads are laid out along the
 * footprint's local X axis at `pitchM` spacing, centred on the facility origin,
 * in the same root-local metres that `facilityLandingPads` returns. */
export const LANDING_PAD_LAYOUTS: Record<"vertiport" | "hub", {
  readonly widthM: number; readonly depthM: number; readonly pitchM: number;
  readonly z: number; readonly y: number;
}> = {
  vertiport: { widthM: 4.2, depthM: 4.2, pitchM: 5.4, z: 0.08, y: 0.72 },
  hub: { widthM: 4.76, depthM: 4.4, pitchM: 5.96, z: -1.155, y: 3.3625 },
};

/** Largest concurrent pad count whose physical layout fits `(parkingSlots-1) *
 * pitch + padWidth <= widthM` (single row along the footprint width). */
export function maxLandingParkingSlots(kind: "vertiport" | "hub", widthM: number): number {
  const layout = LANDING_PAD_LAYOUTS[kind];
  if (!Number.isFinite(widthM) || widthM <= 0) return 0;
  return Math.floor((widthM - layout.widthM + layout.pitchM) / layout.pitchM);
}

export const FACILITY_MIN_DIMENSIONS: Record<FacilityKind, {
  readonly widthM: number; readonly depthM: number; readonly heightM: number;
}> = {
  vertiport: { widthM: 12, depthM: 8, heightM: 4 },
  hub: { widthM: 14, depthM: 11, heightM: 5 },
  charger: { widthM: 10, depthM: 8, heightM: 3.2 },
};

export type FacilityVisualAssets = { readonly passengerShelter: THREE.Group };

let assetsPromise: Promise<FacilityVisualAssets> | null = null;
const verifiedAssetsPromises = new WeakMap<VerifiedVisualAssets, Promise<FacilityVisualAssets>>();
const borrowedResources = new WeakMap<THREE.Group, Set<THREE.BufferGeometry | THREE.Material>>();

function mergeShelter(source: THREE.Group): THREE.Group {
  source.updateMatrixWorld(true);
  const byMaterial = new Map<THREE.Material, THREE.BufferGeometry[]>();
  const originalGeometry = new Set<THREE.BufferGeometry>();
  source.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    if (Array.isArray(node.material)) throw new Error("Passenger shelter has an unsupported multi-material mesh");
    const geometry = node.geometry.clone();
    geometry.applyMatrix4(node.matrixWorld);
    const batch = byMaterial.get(node.material) ?? [];
    batch.push(geometry);
    byMaterial.set(node.material, batch);
    originalGeometry.add(node.geometry);
  });
  if (byMaterial.size === 0) throw new Error("Passenger shelter has no visible meshes");
  const merged = new THREE.Group();
  merged.name = "merged passenger shelter";
  for (const [material, fragments] of byMaterial) {
    const geometry = mergeGeometries(fragments, false);
    for (const fragment of fragments) fragment.dispose();
    if (geometry === null) throw new Error("Passenger shelter geometry cannot be batched by material");
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    merged.add(mesh);
  }
  for (const geometry of originalGeometry) geometry.dispose();
  return merged;
}

/** Load the existing 4 × 2.23 × 3 m bus shelter. Selected cities resolve sealed bytes. */
export function loadFacilityVisualAssets(visualAssets?: VerifiedVisualAssets): Promise<FacilityVisualAssets> {
  const load = (url: Promise<string>): Promise<FacilityVisualAssets> => url.then(path => new GLTFLoader().loadAsync(path))
    .then(gltf => {
      const shelter = gltf.scene;
      const bounds = new THREE.Box3().setFromObject(shelter);
      const size = bounds.getSize(new THREE.Vector3());
      if (bounds.isEmpty() || Math.abs(size.x - 4) > 0.35 || Math.abs(size.y - 3) > 0.35
          || Math.abs(size.z - 2.23) > 0.35) {
        throw new Error("Passenger shelter asset has unexpected metre dimensions");
      }
      return { passengerShelter: mergeShelter(shelter) };
    });
  if (visualAssets !== undefined) {
    let verified = verifiedAssetsPromises.get(visualAssets);
    if (verified === undefined) {
      verified = load(visualAssets.url("/models/incoming/furniture/glb/bus_stop_4.glb"));
      verifiedAssetsPromises.set(visualAssets, verified);
    }
    return verified;
  }
  assetsPromise ??= load(Promise.resolve("/models/incoming/furniture/glb/bus_stop_4.glb"));
  return assetsPromise;
}

export type FacilityVisualSpec = {
  id: string;
  name: string;
  kind: FacilityKind;
  position: { x: number; z: number };
  rotationDeg: number;
  widthM: number;
  depthM: number;
  heightM: number;
  /** Declared charger station count for the standalone charger model. */
  capacity: number;
  chargingPowerW: number;
  /** Vertical offset of the site contact surface; non-zero only for a verified rooftop site. */
  baseY?: number;
  /** Physical landing-pad rectangles for selected-city authoring. When present,
   * `facilityLandingPads` returns exactly this list; legacy workspace specs leave
   * it absent and keep their historical single-pad charging/landing layout. */
  readonly pads?: readonly FacilityLandingPad[];
};

/** Minimal structural source for a selected-city facility visual. */
export interface FacilityVisualSource {
  readonly id: string;
  readonly name: string;
  readonly kind: FacilityKind;
  readonly position: { readonly x: number; readonly z: number };
  readonly rotationDeg: number;
  readonly widthM: number;
  readonly depthM: number;
  readonly heightM: number;
  readonly supportHeightM?: number | null;
  readonly landing?: { readonly parkingSlots: number; readonly movementsPerHour: number } | null;
  readonly charging?: { readonly slots: number; readonly powerW: number } | null;
}

/** Physical landing-pad rectangles for one selected-city facility, derived only
 * from its declared capabilities (never from a model GLB scale). A vertiport/hub
 * gets `parkingSlots` pads laid out inside its footprint; a standalone charger
 * gets one 2.2 × 2.2 m pad per charging slot (max 3). */
export function selectedFacilityLandingPads(source: FacilityVisualSource): FacilityLandingPad[] {
  if (source.kind === "charger") {
    const stations = Math.max(1, Math.min(3, source.charging?.slots ?? 1));
    return Array.from({ length: stations }, (_, index) => ({
      x: (index - (stations - 1) / 2) * 3, z: 1, y: 0.12, widthM: 2.2, depthM: 2.2,
    }));
  }
  const layout = LANDING_PAD_LAYOUTS[source.kind];
  const slots = Math.max(1, source.landing?.parkingSlots ?? 1);
  return Array.from({ length: slots }, (_, index) => ({
    x: (index - (slots - 1) / 2) * layout.pitchM, z: layout.z, y: layout.y,
    widthM: layout.widthM, depthM: layout.depthM,
  }));
}

/** Convert one selected-city facility into the shared visual spec without inventing
 * any capability: a charger's stations equal its declared charging slots, a
 * vertiport/hub's pad count equals its declared landing parking slots, and the
 * physical pads come from `selectedFacilityLandingPads`. */
export function toFacilityVisualSpec(source: FacilityVisualSource): FacilityVisualSpec {
  const capacity = source.kind === "charger"
    ? Math.max(1, Math.min(3, source.charging?.slots ?? 0))
    : Math.max(1, source.landing?.parkingSlots ?? 1);
  const spec: FacilityVisualSpec = {
    id: source.id, name: source.name, kind: source.kind,
    position: { x: source.position.x, z: source.position.z }, rotationDeg: source.rotationDeg,
    widthM: source.widthM, depthM: source.depthM, heightM: source.heightM,
    capacity,
    chargingPowerW: source.charging?.powerW ?? 0,
    baseY: source.supportHeightM ?? 0,
    pads: selectedFacilityLandingPads(source),
  };
  return spec;
}

/** A rotated rectangle in the city X/Z plane, with its base at the site contact surface. */
export type FacilityFootprint = {
  id: string;
  x: number;
  z: number;
  widthM: number;
  depthM: number;
  heightM: number;
  rotationDeg: number;
  baseY: number;
};

/** Clear landing rectangle in root-local metres; y is the contact surface. */
export type FacilityLandingPad = {
  x: number;
  z: number;
  y: number;
  widthM: number;
  depthM: number;
};

type PaletteKey = "paving" | "deck" | "frame" | "roof" | "glass" | "solar"
  | "white" | "cyan" | "amber" | "red";

function palette(): (key: PaletteKey) => THREE.Material {
  const materials = new Map<PaletteKey, THREE.Material>();
  return key => {
    const existing = materials.get(key);
    if (existing !== undefined) return existing;
    const material: THREE.Material = (() => {
      switch (key) {
        case "paving": return new THREE.MeshStandardMaterial({ color: 0x15212c, metalness: 0.24, roughness: 0.8 });
        case "deck": return new THREE.MeshStandardMaterial({ color: 0x405361, metalness: 0.46, roughness: 0.54 });
        case "frame": return new THREE.MeshStandardMaterial({ color: 0x1d3947, metalness: 0.78, roughness: 0.31 });
        case "roof": return new THREE.MeshStandardMaterial({ color: 0xd5e0df, metalness: 0.47, roughness: 0.43 });
        case "glass": return new THREE.MeshStandardMaterial({ color: 0x6db8be, metalness: 0.25, roughness: 0.18,
          transparent: true, opacity: 0.72, depthWrite: false, side: THREE.DoubleSide });
        case "solar": return new THREE.MeshStandardMaterial({ color: 0x183d59, metalness: 0.7, roughness: 0.22 });
        case "white": return new THREE.MeshBasicMaterial({ color: 0xe6f4ed, toneMapped: false, side: THREE.DoubleSide });
        case "cyan": return new THREE.MeshBasicMaterial({ color: 0x54e4e2, toneMapped: false });
        case "amber": return new THREE.MeshBasicMaterial({ color: 0xffbf63, toneMapped: false });
        case "red": return new THREE.MeshBasicMaterial({ color: 0xff776c, toneMapped: false });
      }
    })();
    materials.set(key, material);
    return material;
  };
}

type MaterialFor = ReturnType<typeof palette>;

function box(parent: THREE.Object3D, name: string, material: THREE.Material,
             width: number, height: number, depth: number,
             x: number, y: number, z: number): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material);
  mesh.name = name;
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function cylinder(parent: THREE.Object3D, name: string, material: THREE.Material,
                  radius: number, height: number, x: number, y: number, z: number,
                  segments = 16): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, height, segments), material);
  mesh.name = name;
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  parent.add(mesh);
  return mesh;
}

function perimeter(parent: THREE.Object3D, spec: FacilityVisualSpec, materials: MaterialFor): void {
  box(parent, "paved footprint", materials("paving"), 1, 0.035, 1, 0, 0.0175, 0);
  const insetX = 0.2 / spec.widthM;
  const insetZ = 0.2 / spec.depthM;
  for (const z of [-0.5 + insetZ, 0.5 - insetZ]) {
    box(parent, "edge guide", materials("cyan"), 1 - 2 * insetX, 0.005,
        0.06 / spec.depthM, 0, 0.038, z);
  }
  for (const x of [-0.5 + insetX, 0.5 - insetX]) {
    box(parent, "edge guide", materials("cyan"), 0.06 / spec.widthM, 0.005,
        1 - 2 * insetZ, x, 0.038, 0);
  }
}

/** Vertiport landing-deck geometry covering every declared pad.
 *
 * The deck is one continuous raised platform spanning the whole accepted pad
 * row (never a single design bay), four load-bearing columns sit under each pad,
 * and every pad receives its own landing ring, clearance boundary and painted H.
 * The pad row is the authoritative ``facilityLandingPads(spec)`` row, so the
 * structure follows the pads rather than the pads following a fixed deck.  For
 * the historical single-pad layout (pad at x = -1.44) the deck/support/beacon
 * positions reproduce the previous design exactly; wider rows extend the deck
 * around the same per-pad elements.  A tight footprint that cannot carry the
 * full desired bay edge clamps the deck lip (and therefore the outer supports)
 * so the platform always stays inside the declared footprint. */
function buildVertiport(parent: THREE.Object3D, materials: MaterialFor,
                        spec: FacilityVisualSpec,
                        pads: readonly FacilityLandingPad[]): void {
  const design = FACILITY_MIN_DIMENSIONS.vertiport;
  const padWidthM = 4.2;
  const padDepthM = 4.2;
  const padZM = 0.08;
  // RingGeometry's outer radius is 0.8; these scales make its outer radius 3 m.
  const rx = 3.75 / design.widthM;
  const rz = 3.75 / design.depthM;
  // The attractive deck edge extends 1.92 m beyond the pad row (4.02 m half
  // width for the single 4.2 x 4.2 m pad); the lip is clamped so the whole
  // platform always fits the declared footprint width.
  const deckLipM = 1.92;
  const rowMinXM = Math.min(...pads.map(pad => pad.x - padWidthM / 2));
  const rowMaxXM = Math.max(...pads.map(pad => pad.x + padWidthM / 2));
  const rowExtentM = rowMaxXM - rowMinXM;
  const lipXM = Math.max(0, Math.min(deckLipM, (spec.widthM - rowExtentM) / 2));
  const deckCenterXM = (rowMinXM + rowMaxXM) / 2;

  // One continuous raised deck carries the whole pad row.
  box(parent, "raised landing deck", materials("deck"),
      (rowExtentM + 2 * lipXM) / design.widthM, 0.05, 6.32 / design.depthM,
      deckCenterXM / design.widthM, 0.155, padZM / design.depthM);

  for (const pad of pads) {
    // A support is a 0.42 x 0.42 m column.  It must clear the 4.2 m pad column
    // (pad half width 2.1 + half support 0.21) and stay inside the deck edges;
    // the historical fixed design uses a 3.24 m lateral offset.
    const supportLateral = Math.min(
      3.24,
      (rowMaxXM + lipXM) - pad.x - 0.21,
      pad.x - (rowMinXM - lipXM) - 0.21,
    );
    for (const sx of [-supportLateral, supportLateral]) {
      for (const sz of [-2.56, 2.56]) {
        box(parent, "landing deck support", materials("frame"),
            0.42 / design.widthM, 0.105, 0.28 / design.depthM,
            (pad.x + sx) / design.widthM, 0.0875, (padZM + sz) / design.depthM);
      }
    }

    const ring = new THREE.Mesh(new THREE.RingGeometry(0.76, 0.80, 64), materials("white"));
    ring.name = "landing circle";
    ring.rotation.x = -Math.PI / 2;
    ring.scale.set(rx, rz, 1);
    ring.position.set(pad.x / design.widthM, 0.184, padZM / design.depthM);
    parent.add(ring);

    // The inner 4.2 m square is the usable landing area returned by facilityLandingPads.
    for (const edge of [-1, 1]) {
      box(parent, "landing clearance boundary", materials("amber"), 0.006, 0.002,
          padDepthM / design.depthM, (pad.x + edge * padWidthM / 2) / design.widthM,
          0.184, padZM / design.depthM);
      box(parent, "landing clearance boundary", materials("amber"),
          padWidthM / design.widthM, 0.002, 0.006, pad.x / design.widthM,
          0.184, (padZM + edge * padDepthM / 2) / design.depthM);
    }

    // Paint is lifted a few millimetres above the deck so it stays legible.
    const hHeight = 0.92 * rz;
    const hWidth = 0.75 * rx;
    const stroke = Math.min(rx, rz) * 0.13;
    for (const dx of [-hWidth / 2, hWidth / 2]) {
      box(parent, "H landing mark", materials("white"), stroke, 0.002, hHeight,
          pad.x / design.widthM + dx, 0.183, padZM / design.depthM);
    }
    box(parent, "H landing mark", materials("white"), hWidth, 0.002, stroke,
        pad.x / design.widthM, 0.183, padZM / design.depthM);

    for (const sx of [-supportLateral, supportLateral]) {
      for (const sz of [-2.64, 2.64]) {
        cylinder(parent, "approach beacon", materials("amber"), 0.008, 0.012,
            (pad.x + sx) / design.widthM, 0.191, (padZM + sz) / design.depthM, 12);
      }
    }
  }
}

/** Place the passenger pavilion clear of every declared pad footprint.
 *
 * The 4 x 2.23 x 3 m shelter is a ground-level glass bay.  It may never sit
 * inside a pad's usable landing column, so candidate positions are tried in a
 * deterministic order (the historical bay, then the south/north/east/west
 * gutter around the pad row) and the first candidate that fits the declared
 * footprint and clears every pad rectangle is used.  A tight multi-pad roof
 * with no clear bay omits the decorative pavilion rather than letting it block
 * an accepted pad. */
function addPassengerShelter(root: THREE.Group, assets: FacilityVisualAssets,
                             spec: FacilityVisualSpec,
                             pads: readonly FacilityLandingPad[]): void {
  const pavilion = new THREE.Group();
  pavilion.name = "glass passenger pavilion";
  const shelter = assets.passengerShelter.clone(true);
  shelter.rotation.y = -Math.PI / 2;
  pavilion.add(shelter);
  pavilion.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(shelter);
  const size = bounds.getSize(new THREE.Vector3());
  if (bounds.isEmpty() || size.x > 2.6 || size.y > 3.35 || size.z > 4.35) {
    throw new Error("Passenger shelter does not fit the vertiport bay");
  }
  const center = bounds.getCenter(new THREE.Vector3());
  const halfX = size.x / 2;
  const halfZ = size.z / 2;
  const padBounds = pads.map(pad => ({
    minX: pad.x - pad.widthM / 2, maxX: pad.x + pad.widthM / 2,
    minZ: pad.z - pad.depthM / 2, maxZ: pad.z + pad.depthM / 2,
  }));

  const clearAt = (x: number, z: number): boolean => {
    if (x - halfX < -spec.widthM / 2 || x + halfX > spec.widthM / 2) return false;
    if (z - halfZ < -spec.depthM / 2 || z + halfZ > spec.depthM / 2) return false;
    return !padBounds.some(pad =>
      x - halfX < pad.maxX && x + halfX > pad.minX
      && z - halfZ < pad.maxZ && z + halfZ > pad.minZ);
  };

  const rowMinZ = Math.min(...padBounds.map(pad => pad.minZ));
  const rowMaxZ = Math.max(...padBounds.map(pad => pad.maxZ));
  const rowMinX = Math.min(...padBounds.map(pad => pad.minX));
  const rowMaxX = Math.max(...padBounds.map(pad => pad.maxX));

  // Historical bay first so existing single-pad visuals do not move.
  const candidates: Array<{ x: number; z: number }> = [
    { x: 4, z: -1.2 },
    { x: 4, z: rowMaxZ + halfZ + 0.6 },
    { x: 4, z: rowMinZ - halfZ - 0.6 },
    { x: rowMaxX + halfX + 0.6, z: -1.2 },
    { x: rowMinX - halfX - 0.6, z: -1.2 },
  ];
  const chosen = candidates.find(candidate => clearAt(candidate.x, candidate.z));
  if (chosen === undefined) return; // no clear bay on a tight multi-pad deck

  // bounds/center were captured in the pavilion's local frame (its world
  // translation was still identity): re-centre the shelter so its world centre
  // is the chosen position and its bottom sits on the pavilion ground.
  pavilion.position.set(chosen.x, 0.08, chosen.z);
  shelter.position.x -= center.x;
  shelter.position.y -= bounds.min.y;
  shelter.position.z -= center.z;
  root.add(pavilion);
}

/** Hub model: the single canonical pad-aware renderer.
 *
 * The sorting hall and its roof form one continuous platform that always
 * carries every pad returned by ``facilityLandingPads(spec)``, so a hub spec
 * that declares no explicit ``pads`` still paints its historical single roof
 * pad (local x = -3.08) exactly where that accepted pad is.  The roof lip
 * (3.08 m) and the platform half-width are clamped so the platform never
 * exceeds the declared footprint even when a single-pad row is off-centre
 * within the footprint, and the sorting hall under it keeps the same 0.42 m
 * eave.  Every pad receives its own painted landing guide frame and H mark on
 * the roof.  The rooftop clerestory/cap is not built (it would rise into a pad
 * column), and the ground-level dispatch office stays below the platform so it
 * never enters a usable landing prism. */
function buildHub(parent: THREE.Object3D, materials: MaterialFor,
                  spec: FacilityVisualSpec,
                  pads: readonly FacilityLandingPad[]): void {
  const design = FACILITY_MIN_DIMENSIONS.hub;
  const padWidthM = 4.76;
  const padDepthM = 4.4;
  const padZM = -1.155;
  const rowMinXM = Math.min(...pads.map(pad => pad.x - padWidthM / 2));
  const rowMaxXM = Math.max(...pads.map(pad => pad.x + padWidthM / 2));
  const rowExtentM = rowMaxXM - rowMinXM;
  const rowMidXM = (rowMinXM + rowMaxXM) / 2;
  // Historical roof edge sits 3.08 m beyond the pad row; the lip is clamped to
  // the footprint when a max-fit row leaves less room, and the platform
  // half-width is additionally clamped so both platform edges stay inside the
  // declared footprint when the row is off-centre (e.g. the single hub pad at
  // local x = -3.08 on a narrow workspace hub).  For a centred selected-city
  // row this leaves the roof width unchanged.
  const lipXM = Math.max(0, Math.min(3.08, (spec.widthM - rowExtentM) / 2));
  const roofHalfXM = Math.min(
    rowExtentM / 2 + lipXM,
    rowMidXM + spec.widthM / 2,
    spec.widthM / 2 - rowMidXM,
  );
  const roofWidthM = 2 * roofHalfXM;
  const hallWidthM = roofWidthM - 0.84;

  box(parent, "sorting hall", materials("deck"),
      hallWidthM / design.widthM, 0.58, 6.82 / design.depthM,
      rowMidXM / design.widthM, 0.335, padZM / design.depthM);
  box(parent, "sorting hall roof", materials("roof"),
      roofWidthM / design.widthM, 0.045, 7.37 / design.depthM,
      rowMidXM / design.widthM, 0.65, padZM / design.depthM);
  box(parent, "loading dock", materials("roof"),
      (roofWidthM - 0.7) / design.widthM, 0.055, 2.09 / design.depthM,
      rowMidXM / design.widthM, 0.078, 3.3 / design.depthM);
  box(parent, "loading canopy", materials("frame"),
      (roofWidthM - 0.56) / design.widthM, 0.025, 1.98 / design.depthM,
      rowMidXM / design.widthM, 0.64, 3.41 / design.depthM);
  box(parent, "canopy light strip", materials("cyan"),
      (roofWidthM - 0.42) / design.widthM, 0.009, 0.009,
      rowMidXM / design.widthM, 0.625, 4.455 / design.depthM);

  const doorCount = 3;
  // Loading bays follow the row centre but stay inside the declared footprint
  // (door half width 1.3 m) on a narrow off-centre hub, the same way the
  // platform and lanes do.
  const doorHalfM = 1.3;
  for (let index = 0; index < doorCount; index++) {
    const bayXM = Math.max(-spec.widthM / 2 + doorHalfM,
      Math.min(spec.widthM / 2 - doorHalfM, rowMidXM + (index - (doorCount - 1) / 2) * 3.08));
    const doorWidth = 2.6 / design.widthM;
    box(parent, `loading bay ${index + 1}`, materials("frame"), doorWidth, 0.52,
        0.009, bayXM / design.widthM, 0.365, 2.321 / design.depthM);
    for (let row = 0; row < 4; row++) {
      box(parent, "loading shutter seam", materials("roof"), doorWidth * 0.86,
          0.004, 0.003, bayXM / design.widthM, 0.22 + row * 0.10,
          2.387 / design.depthM);
    }
    box(parent, "bay status lamp", materials(index % 2 === 0 ? "cyan" : "amber"),
        0.012, 0.02, 0.007, bayXM / design.widthM, 0.624, 2.409 / design.depthM);
  }

  // The glass dispatch office stays below the pad platform: it is never inside
  // a usable landing prism even when a pad column passes above the hall.
  const officeHalfWidth = 1.4;
  const officeX = Math.max(1.5, Math.min(5.11, hallWidthM / 2 - officeHalfWidth));
  box(parent, "dispatch office", materials("glass"), 2.8 / design.widthM, 0.31,
      2.64 / design.depthM, officeX / design.widthM, 0.288, -1.76 / design.depthM);
  for (const dx of [-officeHalfWidth, officeHalfWidth]) {
    box(parent, "office mullion", materials("frame"), 0.009, 0.37, 0.26,
        (officeX + dx) / design.widthM, 0.29, -1.76 / design.depthM);
  }
  box(parent, "office roof", materials("roof"), 3.36 / design.widthM, 0.027,
      3.08 / design.depthM, officeX / design.widthM, 0.467, -1.76 / design.depthM);

  const lockerX = Math.max(1.5, Math.min(5.18, hallWidthM / 2 - 1.0));
  for (let index = 0; index < 3; index++) {
    const lz = 0.88 + index * 1.32;
    box(parent, `parcel locker ${index + 1}`, materials("frame"), 1 / design.widthM,
        0.36, 0.6 / design.depthM, lockerX / design.widthM, 0.235, lz / design.depthM);
    box(parent, "locker screen", materials("cyan"), 0.035, 0.041, 0.003,
        lockerX / design.widthM, 0.37, (lz + 0.341) / design.depthM);
    box(parent, "locker door seam", materials("roof"), 0.055, 0.006, 0.003,
        lockerX / design.widthM, 0.19, (lz + 0.341) / design.depthM);
  }
  // Ground lane stripes follow the row centre but are clamped so they never
  // leave the declared footprint on an off-centre single-pad row.
  const stripeMarginM = 0.05;
  for (const dx of [-5.46, -3.5, -1.54, 0.42]) {
    const stripeX = Math.max(-spec.widthM / 2 + stripeMarginM,
      Math.min(spec.widthM / 2 - stripeMarginM, rowMidXM + dx));
    box(parent, "dock lane stripe", materials("white"), 0.007, 0.003, 0.07,
        stripeX / design.widthM, 0.039, 4.84 / design.depthM);
  }

  // Every accepted pad owns an H mark and landing-guide frame on the roof.
  for (const pad of pads) {
    for (const edge of [-1, 1]) {
      box(parent, "roof landing guide", materials("white"), 0.005, 0.003,
          padDepthM / design.depthM, (pad.x + edge * padWidthM / 2) / design.widthM,
          0.675, padZM / design.depthM);
      box(parent, "roof landing guide", materials("white"),
          padWidthM / design.widthM, 0.003, 0.005, pad.x / design.widthM,
          0.675, (padZM + edge * padDepthM / 2) / design.depthM);
    }
    const hHeight = 0.92 * (padDepthM / design.depthM);
    const hWidth = 0.75 * (padWidthM / design.widthM);
    const stroke = Math.min(padWidthM / design.widthM, padDepthM / design.depthM) * 0.13;
    for (const dx of [-hWidth / 2, hWidth / 2]) {
      box(parent, "roof H mark", materials("white"), stroke, 0.003, hHeight,
          pad.x / design.widthM + dx, 0.675, padZM / design.depthM);
    }
    box(parent, "roof H mark", materials("white"), hWidth, 0.003, stroke,
        pad.x / design.widthM, 0.675, padZM / design.depthM);
  }
}

function buildCharger(parent: THREE.Object3D, spec: FacilityVisualSpec, materials: MaterialFor): void {
  const stations = Math.min(3, spec.capacity);
  for (let index = 0; index < stations; index++) {
    const x = (index - (stations - 1) / 2) * 0.30;
    box(parent, `drone charging pad ${index + 1}`, materials("deck"), 0.26, 0.10 / 3.2,
        2.6 / 8, x, 0.07 / 3.2, 1 / 8);
    for (const side of [-1, 1]) {
      box(parent, "charging clearance boundary", materials("white"), 0.006, 0.003,
          2.2 / 8, x + side * 1.1 / 10, 0.128 / 3.2, 1 / 8);
      box(parent, "charging clearance boundary", materials("white"), 2.2 / 10, 0.003,
          0.006, x, 0.128 / 3.2, (1 + side * 1.1) / 8);
    }
    box(parent, "charging contact bar", materials("cyan"), 0.10, 0.003,
        0.014, x, 0.128 / 3.2, 1 / 8);
    box(parent, "charging contact bar", materials("cyan"), 0.014, 0.003,
        0.10, x, 0.128 / 3.2, 1 / 8);
    for (const side of [-1, 1]) {
      cylinder(parent, "pad approach beacon", materials("amber"), 0.008, 0.025,
          x + side * 0.119, 0.046, 0.125, 12);
    }
    box(parent, `charger control pedestal ${index + 1}`, materials("frame"), 0.05, 0.35,
        0.052, x, 0.23, -0.13);
    box(parent, "charger status display", materials("cyan"), 0.036, 0.08,
        0.004, x, 0.32, -0.102);
  }

  // The solar canopy shelters only the power equipment. Its front edge is
  // 1.38 m behind the charging pads' outer edge, leaving open sky above them.
  for (const x of [-0.43, 0.43]) for (const z of [-0.42, -0.24]) {
    box(parent, "equipment canopy column", materials("frame"), 0.023, 0.74, 0.023, x, 0.40, z);
    cylinder(parent, "impact bollard", materials("amber"), 0.012, 0.13, x, 0.104, z + 0.04);
  }
  box(parent, "solar equipment canopy", materials("roof"), 0.91, 0.04, 0.24, 0, 0.79, -0.33);
  box(parent, "canopy illuminated fascia", materials("cyan"), 0.89, 0.012, 0.009, 0, 0.763, -0.209);
  for (const x of [-0.30, -0.10, 0.10, 0.30]) {
    box(parent, "solar panel", materials("solar"), 0.17, 0.009, 0.18, x, 0.817, -0.33);
    box(parent, "solar panel divider", materials("frame"), 0.005, 0.011, 0.18, x + 0.085, 0.818, -0.33);
  }
  box(parent, "power conversion cabinet", materials("frame"), 0.10, 0.56, 0.08, 0.36, 0.33, -0.33);
  box(parent, "power cabinet status strip", materials("cyan"), 0.08, 0.018, 0.004,
      0.36, 0.53, -0.288);
  box(parent, "station wayfinding pylon", materials("frame"), 0.06, 0.85, 0.075, 0.46, 0.48, -0.14);
  box(parent, "station wayfinding light", materials("cyan"), 0.055, 0.14, 0.078,
      0.46, 0.76, -0.14);
}

function validateSpec(spec: FacilityVisualSpec): void {
  if (spec.id.trim().length === 0 || spec.name.trim().length === 0) {
    throw new Error("Facility id and name must be non-empty");
  }
  for (const [label, value] of [
    ["position.x", spec.position.x], ["position.z", spec.position.z],
    ["rotationDeg", spec.rotationDeg], ["widthM", spec.widthM],
    ["depthM", spec.depthM], ["heightM", spec.heightM],
    ["chargingPowerW", spec.chargingPowerW],
  ] as const) {
    if (!Number.isFinite(value)) throw new Error(`Facility ${label} must be finite`);
  }
  if (spec.widthM <= 0 || spec.depthM <= 0 || spec.heightM <= 0) {
    throw new Error("Facility dimensions must be positive");
  }
  if (!Number.isInteger(spec.capacity) || spec.capacity < 1) {
    throw new Error("Facility capacity must be a positive integer");
  }
  if (spec.chargingPowerW < 0) throw new Error("Facility chargingPowerW must be non-negative");
  if (spec.baseY !== undefined && (!Number.isFinite(spec.baseY) || spec.baseY < 0)) {
    throw new Error("Facility baseY must be a finite non-negative height");
  }
  if (spec.kind !== "vertiport" && spec.kind !== "hub" && spec.kind !== "charger") {
    throw new Error(`Unknown facility kind: ${String(spec.kind)}`);
  }
  const minimum = FACILITY_MIN_DIMENSIONS[spec.kind];
  if (spec.widthM < minimum.widthM || spec.depthM < minimum.depthM || spec.heightM < minimum.heightM) {
    throw new Error(`${spec.kind} requires at least ${minimum.widthM} × ${minimum.depthM}`
      + ` × ${minimum.heightM} metres (width × depth × height)`);
  }
  if (spec.pads !== undefined) {
    if (!Array.isArray(spec.pads) || spec.pads.length === 0) {
      throw new Error("Facility pads must be a non-empty list");
    }
    for (const [index, pad] of spec.pads.entries()) {
      if (![pad.x, pad.z, pad.y, pad.widthM, pad.depthM].every(Number.isFinite)
          || pad.widthM <= 0 || pad.depthM <= 0) {
        throw new Error(`Facility pad ${index} must be a finite non-empty rectangle`);
      }
    }
  }
}

export function facilityFootprint(spec: FacilityVisualSpec): FacilityFootprint {
  validateSpec(spec);
  return {
    id: spec.id, x: spec.position.x, z: spec.position.z,
    widthM: spec.widthM, depthM: spec.depthM, heightM: spec.heightM,
    rotationDeg: spec.rotationDeg, baseY: spec.baseY ?? 0,
  };
}

export function facilityLandingPads(spec: FacilityVisualSpec): FacilityLandingPad[] {
  validateSpec(spec);
  if (spec.pads !== undefined) {
    return spec.pads.map(pad => ({ ...pad }));
  }
  if (spec.kind === "vertiport") {
    return [{ x: -1.44, z: 0.08, y: 0.72, widthM: 4.2, depthM: 4.2 }];
  }
  if (spec.kind === "hub") {
    return [{ x: -3.08, z: -1.155, y: 3.3625, widthM: 4.76, depthM: 4.4 }];
  }
  const stations = Math.min(3, spec.capacity);
  return Array.from({ length: stations }, (_, index) => ({
    x: (index - (stations - 1) / 2) * 3, z: 1, y: 0.12,
    widthM: 2.2, depthM: 2.2,
  }));
}

/** Decorative editor geometry. This never represents a provider observation or task result. */
export function createFacilityVisual(spec: FacilityVisualSpec, assets?: FacilityVisualAssets): THREE.Group {
  const footprint = facilityFootprint(spec);
  if (spec.kind === "vertiport" && assets === undefined) {
    throw new Error("Vertiport requires the loaded passenger shelter asset");
  }
  const root = new THREE.Group();
  root.name = `${spec.name} (${spec.kind})`;
  root.position.set(spec.position.x, spec.baseY ?? 0, spec.position.z);
  root.rotation.y = THREE.MathUtils.degToRad(spec.rotationDeg);
  root.userData.target = { kind: "entity", id: spec.id };
  root.userData.facilityKind = spec.kind;
  root.userData.facilityFootprint = footprint;
  const landingPads = facilityLandingPads(spec);
  root.userData.landingPads = landingPads;
  root.userData.landingSurfaceY = landingPads[0]?.y ?? null;
  const materials = palette();
  const site = new THREE.Group();
  site.name = "facility ground footprint";
  site.scale.set(spec.widthM, 1, spec.depthM);
  root.add(site);
  perimeter(site, spec, materials);
  const model = new THREE.Group();
  model.name = `${spec.kind} model`;
  const design = FACILITY_MIN_DIMENSIONS[spec.kind];
  model.scale.set(design.widthM, design.heightM, design.depthM);
  root.add(model);
  switch (spec.kind) {
    case "vertiport": {
      buildVertiport(model, materials, spec, landingPads);
      addPassengerShelter(root, assets!, spec, landingPads);
      const borrowed = new Set<THREE.BufferGeometry | THREE.Material>();
      assets!.passengerShelter.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return;
        borrowed.add(node.geometry);
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) borrowed.add(material);
      });
      borrowedResources.set(root, borrowed);
      break;
    }
    case "hub": buildHub(model, materials, spec, landingPads); break;
    case "charger": buildCharger(model, spec, materials); break;
  }
  cacheStaticTransforms(root, node => node === root);
  return root;
}

/** Release resources owned by this instance; materials are shared only within the instance. */
export function disposeFacilityVisual(group: THREE.Group): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const borrowed = borrowedResources.get(group);
  group.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    if (!borrowed?.has(node.geometry)) geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (!borrowed?.has(material)) materials.add(material);
    }
  });
  group.parent?.remove(group);
  group.clear();
  borrowedResources.delete(group);
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
}
