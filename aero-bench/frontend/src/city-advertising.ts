import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type { CityTimeOfDay } from "./city-lighting-calibration";
import { CITY_SUBSYSTEM_LIGHTING } from "./city-subsystem-lighting";

interface BuildingPlacement {
  readonly building_id: string;
  readonly part: number;
  readonly x: number;
  readonly z: number;
  readonly width: number;
  readonly depth: number;
  readonly rotation_deg: number;
  readonly base_y: number;
  readonly height: number;
}

export interface StreetPoint { readonly x: number; readonly z: number; }
export interface AdvertisingLocation extends StreetPoint {
  readonly buildingId: string;
  readonly buildingPart: number;
  readonly width: number;
  readonly y: number;
  readonly normalX: number;
  readonly normalZ: number;
}

const posters = [
  { eyebrow: "URBAN AIR", headline: "低空城市", detail: "航线可见 · 城市可达", from: "#092b3c", to: "#087d8e", accent: "#85eff0" },
  { eyebrow: "CITY MOTION", headline: "自在出行", detail: "街道正在发生", from: "#25294b", to: "#86506c", accent: "#ffcf9d" },
  { eyebrow: "NIGHT DISTRICT", headline: "城市有光", detail: "遇见街区的另一面", from: "#173240", to: "#245a69", accent: "#e7ca8d" },
] as const;

const advertisingLightIntensity = CITY_SUBSYSTEM_LIGHTING.twilight.advertisingLightIntensity;

export function setCityAdvertisingLighting(buildings: THREE.Group, timeOfDay: CityTimeOfDay): void {
  const intensity = CITY_SUBSYSTEM_LIGHTING[timeOfDay].advertisingLightIntensity;
  buildings.traverse(node => {
    if (node instanceof THREE.PointLight && node.userData.cityAdvertisingLight === true) {
      // Preserve the material shader's light count across day/night transitions.
      node.intensity = intensity;
    }
  });
}

function posterTexture(index: number): THREE.CanvasTexture {
  const poster = posters[index]!;
  const canvas = document.createElement("canvas");
  canvas.width = 1024; canvas.height = 384;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("City advertising canvas is unavailable");
  const background = context.createLinearGradient(0, 0, canvas.width, canvas.height);
  background.addColorStop(0, poster.from);
  background.addColorStop(1, poster.to);
  context.fillStyle = background;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = `${poster.accent}55`;
  context.lineWidth = 2;
  for (let line = -200; line < 1200; line += 96) {
    context.beginPath(); context.moveTo(line, 384); context.lineTo(line + 300, 0); context.stroke();
  }
  context.fillStyle = poster.accent;
  context.fillRect(47, 45, 7, 292);
  context.font = "600 28px sans-serif";
  context.fillText(poster.eyebrow, 86, 89);
  context.fillStyle = "#ffffff";
  context.font = "700 122px sans-serif";
  context.fillText(poster.headline, 82, 242);
  context.fillStyle = "#d9e6e9";
  context.font = "400 30px sans-serif";
  context.fillText(poster.detail, 88, 311);
  context.fillStyle = `${poster.accent}33`;
  context.beginPath(); context.arc(913, 215, 145, 0, Math.PI * 2); context.fill();
  context.strokeStyle = poster.accent;
  context.lineWidth = 5;
  context.beginPath(); context.arc(913, 215, 113, -0.7, 1.9); context.stroke();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

function fittedFrame(source: THREE.Object3D, width: number, height: number, frameMaterial: THREE.Material): THREE.Group {
  const group = new THREE.Group();
  const model = source.clone(true);
  group.add(model);
  const bounds = new THREE.Box3().setFromObject(model);
  const size = bounds.getSize(new THREE.Vector3());
  if (bounds.isEmpty() || size.x <= 0 || size.y <= 0 || size.z <= 0) {
    throw new Error("Provided wall billboard has invalid geometry");
  }
  model.scale.set(width / size.x, height / size.y, 0.3 / size.z);
  model.position.set(-bounds.getCenter(new THREE.Vector3()).x * model.scale.x,
                     -bounds.getCenter(new THREE.Vector3()).y * model.scale.y,
                     -bounds.getCenter(new THREE.Vector3()).z * model.scale.z);
  model.traverse(node => { if (node instanceof THREE.Mesh) node.material = frameMaterial; });
  return group;
}

/** Decorative screens attach to road-facing OSM building boxes; they are not traffic telemetry. */
export async function addCityAdvertising(buildings: THREE.Group, signals: readonly StreetPoint[],
                                         focalPoint: StreetPoint): Promise<number> {
  const source = (await new GLTFLoader().loadAsync("/models/incoming/furniture/glb/wall_billboard_2.glb")).scene;
  const frameMaterial = new THREE.MeshStandardMaterial({ color: 0x263844, metalness: 0.58, roughness: 0.36 });
  const textures = posters.map((_, index) => posterTexture(index));
  const candidates = buildings.children.flatMap(object => {
    const placement = object.userData.collisionBox as BuildingPlacement | undefined;
    if (placement === undefined || placement.width < 11 || placement.height < 10
        || Math.hypot(placement.x, placement.z) > 460) return [];
    const nearest = signals.reduce<{ point: StreetPoint; distance: number } | null>((best, point) => {
      const distance = Math.hypot(point.x - placement.x, point.z - placement.z);
      return best === null || distance < best.distance ? { point, distance } : best;
    }, null);
    if (nearest === null || nearest.distance > 55) return [];
    return [{ object, placement, nearest }];
  }).sort((left, right) =>
    (left.nearest.distance + Math.hypot(left.placement.x, left.placement.z) * 0.018)
    - (right.nearest.distance + Math.hypot(right.placement.x, right.placement.z) * 0.018));
  const selected: typeof candidates = [];
  const buildingIds = new Set<string>();
  const streetCandidates = candidates.filter(item =>
    Math.hypot(item.placement.x - focalPoint.x, item.placement.z - focalPoint.z) < 85)
    .sort((left, right) =>
      Math.hypot(left.placement.x - focalPoint.x, left.placement.z - focalPoint.z)
      - Math.hypot(right.placement.x - focalPoint.x, right.placement.z - focalPoint.z));
  const addCandidate = (candidate: typeof candidates[number]): void => {
    if (buildingIds.has(candidate.placement.building_id)) return;
    if (selected.some(other => Math.hypot(other.placement.x - candidate.placement.x,
                                        other.placement.z - candidate.placement.z) < 58)) return;
    selected.push(candidate);
    buildingIds.add(candidate.placement.building_id);
  };
  for (const candidate of streetCandidates) {
    if (selected.length === 3) break;
    addCandidate(candidate);
  }
  for (const candidate of candidates) {
    if (selected.length === 18) break;
    addCandidate(candidate);
  }
  if (selected.length < 8) throw new Error("Verified city has too few road-facing facades for illuminated signs");

  const locations: AdvertisingLocation[] = [];
  for (const [index, { object, placement, nearest }] of selected.entries()) {
    const angle = THREE.MathUtils.degToRad(placement.rotation_deg);
    const dx = nearest.point.x - placement.x, dz = nearest.point.z - placement.z;
    const localX = Math.cos(angle) * dx - Math.sin(angle) * dz;
    const localZ = Math.sin(angle) * dx + Math.cos(angle) * dz;
    const xFace = Math.abs(localX) / placement.width > Math.abs(localZ) / placement.depth;
    const faceWidth = xFace ? placement.depth : placement.width;
    const width = Math.min(8.8, faceWidth - 1.4);
    if (width <= 3.5) continue;
    const height = Math.min(3.4, Math.max(2.5, placement.height * 0.2));
    const sign = new THREE.Group();
    sign.name = `Illuminated wall billboard on ${placement.building_id}`;
    sign.position.set(xFace ? Math.sign(localX) * (placement.width / 2 + 0.18) : 0,
                      Math.min(placement.height - height / 2 - 0.5, 5.2),
                      xFace ? 0 : Math.sign(localZ) * (placement.depth / 2 + 0.18));
    sign.rotation.y = xFace ? Math.sign(localX) * Math.PI / 2 : localZ >= 0 ? 0 : Math.PI;
    sign.add(fittedFrame(source, width, height, frameMaterial));
    const poster = posters[index % posters.length]!;
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(width * 0.76, height * 0.57),
      new THREE.MeshBasicMaterial({ map: textures[index % textures.length], toneMapped: false }));
    screen.position.set(-width * 0.045, -height * 0.10, 0.18);
    screen.name = `Display ${poster.eyebrow}`;
    sign.add(screen);
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(width * 0.9, height * 0.73),
      new THREE.MeshBasicMaterial({ color: poster.accent, transparent: true, opacity: 0.1,
        blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    glow.position.set(-width * 0.045, -height * 0.10, 0.17);
    sign.add(glow);
    const edgeMaterial = new THREE.MeshBasicMaterial({ color: poster.accent, toneMapped: false });
    const edgeWidth = width * 0.78, edgeHeight = height * 0.59;
    for (const edge of [
      { w: edgeWidth, h: 0.045, x: 0, y: edgeHeight / 2 },
      { w: edgeWidth, h: 0.045, x: 0, y: -edgeHeight / 2 },
      { w: 0.045, h: edgeHeight, x: -edgeWidth / 2, y: 0 },
      { w: 0.045, h: edgeHeight, x: edgeWidth / 2, y: 0 },
    ]) {
      const strip = new THREE.Mesh(new THREE.PlaneGeometry(edge.w, edge.h), edgeMaterial);
      strip.position.set(edge.x - width * 0.045, edge.y - height * 0.10, 0.20);
      sign.add(strip);
    }
    if (index < 6) {
      const light = new THREE.PointLight(poster.accent, advertisingLightIntensity, 11, 2);
      light.position.set(0, -height * 0.1, 1.5);
      light.userData.cityAdvertisingLight = true;
      sign.add(light);
    }
    object.add(sign);
    const normalAngle = angle + sign.rotation.y;
    locations.push({
      buildingId: placement.building_id,
      buildingPart: placement.part,
      width,
      x: placement.x + Math.cos(angle) * sign.position.x + Math.sin(angle) * sign.position.z,
      z: placement.z - Math.sin(angle) * sign.position.x + Math.cos(angle) * sign.position.z,
      y: placement.base_y + sign.position.y,
      normalX: Math.sin(normalAngle), normalZ: Math.cos(normalAngle),
    });
  }
  buildings.userData.advertisingCount = locations.length;
  buildings.userData.advertisingLocations = locations;
  return locations.length;
}
