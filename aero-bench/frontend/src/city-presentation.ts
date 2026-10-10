import { installCityWindowLights } from "./city-facade-window-lights";
import * as THREE from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { createSharedCityGltfLoader, loadSharedFbx, SharedCityTextureLoader, shareFbxTextures,
  runInFrameSlices, waitForGpuCommands } from "./city-asset-cache";
import { clone as cloneSkeleton } from "three/addons/utils/SkeletonUtils.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { fitCityCharacter } from "./city-character";
import { cacheStaticTransforms, skipHiddenDynamicChildren } from "./city-rendering";
import { TrafficShadowCasters } from "./city-traffic-shadow";
import { mergeVehicleTemplateMeshes } from "./city-vehicle-merge";
import { attachCityWindowDetail, createCityPaneMaterial, hasCityWindowWall } from "./city-window-detail";
import { removeAssetAmbientLights } from "./city-lighting";
import { CITY_FLEET_ASSETS } from "./city-workspace-config";
import { attachMeteredRoofs, createMeteredBuilding, facadeTile, needsMeteredFacadePart } from "./city-building-facade";
import { sourceBuildingGeometry, type SourceBuildingEnvelope, type SourceBuildingShape } from "./city-building-shape";
import { animateCyclist, bindCyclist, cacheCyclistStaticTransforms, createCyclistTemplate,
  type CyclistPoseFrames, type CyclistRig } from "./city-cyclist";
import type { StaticSignal, VerifiedVisualAssets } from "./city-authoring-api";
import type { CityLightingSample, CityTimeOfDay } from "./city-lighting-calibration";
import { CITY_SUBSYSTEM_LIGHTING } from "./city-subsystem-lighting";
import { signalFixtureIsOmitted } from "./city-static-fixture-clearance";
import { cityBuildingMaterialRole, cityMaterialPaneProfile, rasterCityFacadePaneRoughness,
  readCityFacadePaneProfile, type CityFacadePaneProfile } from "./city-facade-pane-profile";

interface BigCityEntry { readonly title: string; readonly subgroup: string; readonly model_url?: string; }
interface BigCityManifest { readonly entries: readonly BigCityEntry[]; }
export interface CityBuilding {
  readonly id: string;
  readonly bounds: THREE.Box3;
  readonly sourceShape?: SourceBuildingShape;
}
export interface CityTree { readonly x: number; readonly z: number; readonly height: number; readonly width: number; readonly kind: "conifer" | "broad"; }
interface BuildingPlacement {
  readonly building_id: string; readonly part: number; readonly x: number; readonly z: number;
  readonly width: number; readonly depth: number; readonly rotation_deg: number;
  readonly base_y: number; readonly height: number;
}
interface BuildingPlacementData {
  readonly schema_version: "aero-bench.city-building-placement/v1";
  readonly mesh_pack_source_sha256: string;
  readonly mesh_pack_manifest_sha256: string;
  readonly displayed_surface_sha256: string;
  readonly source_kind: "verified-source-surfaces-and-inscribed-rectangles";
  readonly complete_footprint_buildings: readonly string[];
  readonly complete_footprint_envelopes: readonly SourceBuildingEnvelope[];
  readonly occluded_buildings: readonly string[];
  readonly placements: readonly BuildingPlacement[];
}

function verifiedEnvelopeFor(id: string, bounds: THREE.Box3,
                             envelopes: ReadonlyMap<string, SourceBuildingEnvelope>): SourceBuildingEnvelope {
  const envelope = envelopes.get(id);
  if (envelope === undefined || envelope.building_id !== id || envelope.part !== 0
      || ![envelope.x, envelope.z, envelope.width, envelope.depth, envelope.rotation_deg,
        envelope.base_y, envelope.height].every(Number.isFinite)
      || envelope.width <= 0 || envelope.depth <= 0 || envelope.height <= 0 || bounds.isEmpty()
      || Math.abs(envelope.base_y - bounds.min.y) > 0.01
      || Math.abs(envelope.base_y + envelope.height - bounds.max.y) > 0.01) {
    throw new Error(`Building collision envelope does not match the verified source mesh: ${id}`);
  }
  return envelope;
}

const fbxLoaders = new Map<string, Promise<FBXLoader>>();
const gltfLoader = createSharedCityGltfLoader();

async function aliasedFbxLoader(aliasesUrl: string): Promise<FBXLoader> {
  let pending = fbxLoaders.get(aliasesUrl);
  if (pending === undefined) {
    pending = fetch(aliasesUrl).then(async response => {
      if (!response.ok) throw new Error(`City asset texture aliases failed: ${response.status}`);
      const aliases = await response.json() as Record<string, string>;
      const manager = new THREE.LoadingManager();
      manager.setURLModifier(url => {
        const file = decodeURIComponent(url.replaceAll("\\", "/").split("/").pop() ?? "").toLowerCase();
        if (aliasesUrl.includes("urban-traffic") && file === "wheels_d.tga") {
          return "/models/incoming/urban-traffic/images/e40e01cfb1f8da64392d5470848a5afd.webp";
        }
        return aliases[file] ?? url;
      });
      shareFbxTextures(manager);
      return new FBXLoader(manager);
    });
    fbxLoaders.set(aliasesUrl, pending);
  }
  return pending;
}

async function loadFbx(path: string, aliasesUrl: string, visualAssets?: VerifiedVisualAssets): Promise<THREE.Group> {
  let asset: THREE.Group;
  if (visualAssets === undefined) {
    asset = await loadSharedFbx(await aliasedFbxLoader(aliasesUrl), path);
  } else {
    const rawAliases = await visualAssets.json(aliasesUrl);
    if (rawAliases === null || typeof rawAliases !== "object" || Array.isArray(rawAliases)
        || Object.entries(rawAliases).some(([name, url]) => !name || typeof url !== "string")) {
      throw new Error("已验证 BigCity 纹理别名无效");
    }
    const aliases = rawAliases as Record<string, string>;
    await visualAssets.preload([path, ...Object.values(aliases)]);
    const modelUrl = visualAssets.cachedUrl(path);
    const verifiedUrls = new Map([path, ...Object.values(aliases)].map(assetPath =>
      [assetPath, visualAssets.cachedUrl(assetPath)]));
    const manager = new THREE.LoadingManager();
    manager.setURLModifier(url => resolveVerifiedFbxUrl(url, modelUrl, aliases, verifiedUrls));
    shareFbxTextures(manager);
    asset = await loadSharedFbx(new FBXLoader(manager), modelUrl);
  }
  removeAssetAmbientLights(asset);
  return asset;
}

/** FBX dependencies may resolve only to URLs whose bytes this bundle already verified. */
export function resolveVerifiedFbxUrl(url: string, modelUrl: string,
                                      aliases: Readonly<Record<string, string>>,
                                      verifiedUrls: ReadonlyMap<string, string>): string {
  if (url === modelUrl || [...verifiedUrls.values()].includes(url)) return url;
  const file = decodeURIComponent(url.replaceAll("\\", "/").split("/").pop() ?? "").toLowerCase();
  const assetPath = aliases[file];
  const verified = assetPath === undefined ? undefined : verifiedUrls.get(assetPath);
  if (verified === undefined) throw new Error(`BigCity 模型引用未声明或未验证纹理：${url}`);
  return verified;
}

function fitted(source: THREE.Object3D, size: THREE.Vector3, rotateY = 0,
                uniformHorizontal = false): THREE.Group {
  const root = new THREE.Group();
  const fit = new THREE.Group();
  const orient = new THREE.Group();
  orient.rotation.y = rotateY;
  orient.add(source);
  fit.add(orient);
  root.add(fit);
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root);
  if (bounds.isEmpty()) throw new Error("City asset contains no renderable geometry");
  const sourceSize = bounds.getSize(new THREE.Vector3());
  if (sourceSize.x <= 0 || sourceSize.y <= 0 || sourceSize.z <= 0) throw new Error("City asset has invalid dimensions");
  if (uniformHorizontal) fit.scale.setScalar(Math.max(size.x, size.z) / Math.max(sourceSize.x, sourceSize.z));
  else fit.scale.set(size.x / sourceSize.x, size.y / sourceSize.y, size.z / sourceSize.z);
  root.updateMatrixWorld(true);
  bounds.setFromObject(root);
  const center = bounds.getCenter(new THREE.Vector3());
  fit.position.set(-center.x, -bounds.min.y, -center.z);
  root.traverse(node => {
    if (node instanceof THREE.Mesh) { node.castShadow = true; node.receiveShadow = true; }
  });
  return root;
}

export function collisionBox(size: THREE.Vector3, color: number): THREE.LineSegments {
  const box = new THREE.BoxGeometry(size.x, size.y, size.z);
  const lines = new THREE.LineSegments(
    new THREE.EdgesGeometry(box),
    new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.85 }),
  );
  box.dispose();
  lines.position.y = size.y / 2;
  lines.visible = false;
  lines.userData.collisionProxy = true;
  lines.renderOrder = 50;
  return lines;
}

export function setCollisionBoxesVisible(group: THREE.Object3D, visible: boolean): void {
  group.traverse(node => { if (node.userData.collisionProxy === true) node.visible = visible; });
}

function hashId(id: string): number {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index++) hash = Math.imul(hash ^ id.charCodeAt(index), 16777619);
  return hash >>> 0;
}

// The supplied 1.333 s walk loop covers about 1.5 m at the recorded median walking speed.
const PEDESTRIAN_METRES_PER_WALK_CYCLE = 1.5;

function windowMask(surface: THREE.CanvasTexture, variant: number): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1280;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("Building window light canvas is unavailable");
  context.fillStyle = "#000000";
  context.fillRect(0, 0, 1280, 1280);
  const grid = (x: number, y: number, columns: number, rows: number,
                stepX: number, stepY: number, width: number, height: number, seed: number): void => {
    for (let row = 0; row < rows; row++) for (let column = 0; column < columns; column++) {
      const value = (row * 37 + column * 19 + seed * 13) % 11;
      if (value > 3) continue;
      context.fillStyle = value === 0 ? "#ff9e35"
        : value === 1 ? "#ffd078" : value === 2 ? "#d4e6f5" : "#ffb64f";
      context.fillRect(x + column * stepX, y + row * stepY, width, height);
    }
  };
  grid(2, 680, 9, 8, 36, 35, 28, 27, 2 + variant);
  grid(334, 339, 17, 8, 19, 39, 14, 15, 3 + variant);
  grid(335, 995, 8, 7, 44, 40, 36, 17, 5 + variant);
  grid(695, 651, 6, 8, 47, 49, 27, 28, 6 + variant);
  grid(970, 802, 8, 7, 38, 44, 30, 28, 7 + variant);
  if (variant !== 2) {
    context.fillStyle = "#145366";
    for (let y = 270 + variant * 90; y < 1280; y += 440) context.fillRect(0, y, 1280, 2);
  }
  const lights = context.getImageData(0, 0, 1280, 1280);
  const maskContext = (surface.image as HTMLCanvasElement).getContext("2d");
  if (maskContext === null) throw new Error("Modern building surface mask is unavailable");
  const mask = maskContext.getImageData(0, 0, 1280, 1280).data;
  for (let pixel = 0; pixel < 1280 * 1280; pixel++) {
    if (mask[pixel * 4 + 2] === 0) continue;
    const offset = pixel * 4;
    lights.data[offset] = lights.data[offset + 1] = lights.data[offset + 2] = 0;
  }
  context.putImageData(lights, 0, 0);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

function glassMask(atlas: THREE.Texture): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1280;
  const context = canvas.getContext("2d");
  if (context === null || (atlas.image as HTMLImageElement).width !== 1280
      || (atlas.image as HTMLImageElement).height !== 1280) {
    throw new Error("BigCity modern atlas cannot supply a glass reflection mask");
  }
  context.drawImage(atlas.image as CanvasImageSource, 0, 0);
  const pixels = context.getImageData(0, 0, 1280, 1280);
  const allowed = new Uint8Array(1280 * 1280);
  const mark = (x: number, y: number, width: number, height: number): void => {
    for (let row = y; row < y + height; row++) allowed.fill(1, row * 1280 + x, row * 1280 + x + width);
  };
  mark(0, 0, 330, 960);
  mark(0, 960, 330, 320);
  mark(330, 329, 340, 311);
  mark(330, 640, 340, 640);
  mark(685, 640, 85, 640);
  mark(770, 74, 510, 1206);
  for (let pixel = 0; pixel < allowed.length; pixel++) {
    const offset = pixel * 4;
    const luminance = (pixels.data[offset]! * 0.2126 + pixels.data[offset + 1]! * 0.7152
      + pixels.data[offset + 2]! * 0.0722) / 255;
    const glass = allowed[pixel] === 1 && luminance < 0.75;
    pixels.data[offset] = 0;
    pixels.data[offset + 1] = glass ? 56 : 212;
    pixels.data[offset + 2] = glass ? 0 : 178;
    pixels.data[offset + 3] = 255;
  }
  context.putImageData(pixels, 0, 0);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.NoColorSpace;
  texture.flipY = atlas.flipY;
  texture.anisotropy = 8;
  return texture;
}

function storefrontTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 256; canvas.height = 64;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("Storefront window canvas is unavailable");
  context.clearRect(0, 0, 256, 64);
  for (let pane = 0; pane < 12; pane++) {
    const x = pane * 21 + 2;
    context.fillStyle = pane % 4 === 0 ? "rgba(255,246,220,0.9)" : "rgba(255,222,169,0.75)";
    context.fillRect(x, 5, 17, 46);
    context.fillStyle = "rgba(255,255,239,0.9)";
    context.fillRect(x, 5, 17, 3);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export function setCityBuildingLighting(group: THREE.Group, timeOfDay: CityTimeOfDay,
                                        environment: THREE.Texture): void {
  const lighting = CITY_SUBSYSTEM_LIGHTING[timeOfDay];
  const storefront = group.userData.storefrontLights as THREE.InstancedMesh | undefined;
  if (storefront !== undefined) storefront.visible = timeOfDay !== "day";
  const materials = group.userData.windowMaterials as THREE.MeshStandardMaterial[] | undefined;
  for (const material of materials ?? []) {
    material.emissiveIntensity = material.userData.glassFacade === true
      ? lighting.buildingWindowIntensity : 0;
    // Explicit envMap bypasses scene.environmentIntensity in Three.js. Local
    // reflection clones copy these same calibrated values on refresh.
    material.envMapIntensity = material.userData.glassFacade === true
      ? lighting.buildingFacadeEnvironmentIntensity * (material instanceof THREE.MeshPhysicalMaterial ? 2 : 1)
      : lighting.buildingEnvironmentIntensity;
    if (material.envMap !== environment) { material.envMap = environment; material.needsUpdate = true; }
  }
}

const calibratedFacadeMasks = new WeakMap<THREE.Texture,
  Map<THREE.Texture, { readonly texture: THREE.DataTexture; readonly profile: string }>>();

/** Source-pixel authored panes define glazing, including bright and unlit panes. */
export function buildCityFacadeRoughness(albedo: Uint8ClampedArray, illumination: Uint8ClampedArray,
  profile: CityFacadePaneProfile): Uint8Array {
  if (albedo.length === 0 || albedo.length % 4 !== 0 || illumination.length !== albedo.length) {
    throw new Error("Facade roughness requires matching RGBA albedo and illumination pixels");
  }
  const checked = readCityFacadePaneProfile(profile);
  if (checked.tile_size_px[0] * checked.tile_size_px[1] * 4 !== albedo.length) {
    throw new Error("Authored facade pane profile dimensions differ from source pixels");
  }
  return rasterCityFacadePaneRoughness(checked);
}

function calibratedFacadeRoughness(material: THREE.MeshStandardMaterial,
  profile: CityFacadePaneProfile): THREE.DataTexture {
  const albedo = material.map, illumination = material.emissiveMap;
  if (albedo === null || illumination === null || material.normalMap === null) {
    throw new Error(`Source facade is missing albedo, normal or Illum: ${material.name}`);
  }
  let byIllumination = calibratedFacadeMasks.get(albedo);
  if (byIllumination === undefined) {
    byIllumination = new Map(); calibratedFacadeMasks.set(albedo, byIllumination);
  }
  const profileKey = JSON.stringify([profile.material_kind, profile.tile_size_px,
    profile.glass_rectangles_px, profile.opaque_polygons_px]);
  const image = albedo.image as { width: number; height: number };
  const illuminationImage = illumination.image as { width: number; height: number };
  const normalImage = material.normalMap.image as { width: number; height: number };
  if (!Number.isSafeInteger(image?.width) || !Number.isSafeInteger(image?.height)
      || image.width <= 0 || image.height <= 0 || image.width !== illuminationImage?.width
      || image.height !== illuminationImage.height || normalImage?.width !== image.width
      || normalImage?.height !== image.height || profile.tile_size_px[0] !== image.width
      || profile.tile_size_px[1] !== image.height) throw new Error("Source facade/profile texture dimensions do not match");
  for (const texture of [albedo, illumination, material.normalMap]) {
    if (texture.channel !== 0 || texture.wrapS !== THREE.RepeatWrapping || texture.wrapT !== THREE.RepeatWrapping
        || texture.flipY !== albedo.flipY || !texture.repeat.equals(albedo.repeat) || !texture.offset.equals(albedo.offset)
        || !texture.center.equals(albedo.center) || texture.rotation !== albedo.rotation
        || texture.matrixAutoUpdate !== albedo.matrixAutoUpdate || !texture.matrix.equals(albedo.matrix)) {
      throw new Error("Source facade channels must share TEXCOORD0, repeat sampling and texture transforms");
    }
  }
  const existing = byIllumination.get(illumination);
  if (existing !== undefined) {
    if (existing.profile !== profileKey) throw new Error("Shared facade textures have conflicting authored pane profiles");
    return existing.texture;
  }
  const canvas = document.createElement("canvas");
  canvas.width = image.width; canvas.height = image.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (context === null) throw new Error("Facade material calibration canvas is unavailable");
  context.drawImage(albedo.image as CanvasImageSource, 0, 0);
  const albedoPixels = context.getImageData(0, 0, image.width, image.height).data;
  context.clearRect(0, 0, image.width, image.height);
  context.drawImage(illumination.image as CanvasImageSource, 0, 0);
  const illuminationPixels = context.getImageData(0, 0, image.width, image.height).data;
  const texture = new THREE.DataTexture(buildCityFacadeRoughness(albedoPixels, illuminationPixels, profile),
    image.width, image.height, THREE.RGBAFormat);
  texture.name = `${material.name}.derived-display-roughness`;
  texture.colorSpace = THREE.NoColorSpace; texture.flipY = albedo.flipY;
  texture.wrapS = albedo.wrapS; texture.wrapT = albedo.wrapT;
  texture.repeat.copy(albedo.repeat); texture.offset.copy(albedo.offset); texture.rotation = albedo.rotation;
  texture.center.copy(albedo.center); texture.matrixAutoUpdate = albedo.matrixAutoUpdate;
  texture.matrix.copy(albedo.matrix); texture.channel = albedo.channel;
  texture.anisotropy = albedo.anisotropy;
  texture.generateMipmaps = true; texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter; texture.needsUpdate = true;
  byIllumination.set(illumination, { texture, profile: profileKey });
  return texture;
}

function cityBuildingCalibrationMaterials(root: THREE.Object3D): {
  readonly materials: Set<THREE.Material>; readonly sourceMaterials: Set<THREE.Material>;
} {
  const materials = new Set<THREE.Material>(), sourceMaterials = new Set<THREE.Material>();
  root.traverse(node => {
    // The streamer keeps original materials when a local probe installs clones.
    const originals: unknown = node.userData.renderMaterials;
    if (originals !== undefined) {
      if (!Array.isArray(originals) || originals.some(material => !(material instanceof THREE.Material))) {
        throw new Error("Building source material registry is invalid");
      }
      for (const material of originals as THREE.Material[]) { materials.add(material); sourceMaterials.add(material); }
    }
    if (node instanceof THREE.Mesh) {
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) materials.add(material);
    }
  });
  return { materials, sourceMaterials };
}

/** Collect at deliberate probe configuration, never on each animation frame. */
export function collectCityCalibratedFacadeMaterials(root: THREE.Object3D): readonly THREE.MeshStandardMaterial[] {
  const result: THREE.MeshStandardMaterial[] = [];
  for (const material of cityBuildingCalibrationMaterials(root).materials) {
    if (cityMaterialPaneProfile(material) === null) continue;
    if (!(material instanceof THREE.MeshStandardMaterial)) throw new Error("Authored facade requires source PBR material");
    result.push(material);
  }
  return result;
}

/** Apply once per appearance change or streamed building; no lights or new scene geometry. */
export function setCityBuildingCalibration(root: THREE.Object3D, sample: CityLightingSample,
  environment: THREE.Texture): { readonly facadeMaterials: number; readonly roofMaterials: number } {
  if (!(environment instanceof THREE.Texture)) throw new Error("Calibrated building environment is required");
  const { materials, sourceMaterials } = cityBuildingCalibrationMaterials(root);
  let facadeMaterials = 0, roofMaterials = 0;
  const reflectiveMaterials: THREE.MeshStandardMaterial[] = [];
  for (const material of materials) {
    const profile = cityMaterialPaneProfile(material), role = cityBuildingMaterialRole(material);
    if (profile === null && role === null) {
      if (sourceMaterials.has(material)) throw new Error(`Source material is missing its authored presentation profile: ${material.name}`);
      continue;
    }
    if (profile !== null && role !== null) throw new Error("Source material declares conflicting facade and roof profiles");
    if (!(material instanceof THREE.MeshStandardMaterial)) throw new Error("Authored building profile requires source PBR material");
    let refreshShader = false;
    if (profile !== null) {
      const kind = profile.material_kind;
      const roughness = calibratedFacadeRoughness(material, profile);
      refreshShader = material.roughnessMap !== roughness;
      material.roughnessMap = roughness;
      material.roughness = THREE.MathUtils.lerp(1, 0.76, sample.wetness);
      material.metalness = 0;
      material.normalScale.setScalar(kind === "glass-and-masonry" ? 0.75 : 0.9);
      installCityWindowLights(material, profile);
      material.emissive.copy(sample.windowColor);
      material.emissiveIntensity = sample.windowIntensity;
      material.envMapIntensity = sample.facadeEnvironmentIntensity;
      material.userData.cityMaterialEstimate = kind;
      // The existing one-block probe accepts these materials. The roughness map
      // keeps masonry diffuse while only the window pixels reflect clearly.
      material.userData.glassFacade = true;
      reflectiveMaterials.push(material);
      facadeMaterials++;
    } else if (role === "opaque-roof") {
      material.roughness = THREE.MathUtils.lerp(0.9, 0.65, sample.wetness);
      material.metalness = 0;
      material.emissiveIntensity = 0;
      material.envMapIntensity = sample.environmentIntensity;
      roofMaterials++;
    }
    if (material.envMap !== environment) { material.envMap = environment; refreshShader = true; }
    if (refreshShader) material.needsUpdate = true;
  }
  root.userData.windowMaterials = reflectiveMaterials;
  return { facadeMaterials, roofMaterials };
}

/** Supplied modern facades fit inside oriented collision boxes derived from OSM roof triangles. */
export async function loadCityBuildings(buildings: readonly CityBuilding[], sourceSha256: string,
                                        manifestSha256: string,
                                        placement: string | { readonly value: unknown; readonly sha256: string },
                                        visualAssets?: VerifiedVisualAssets): Promise<THREE.Group> {
  const [assetResponse, placementResponse] = await Promise.all([
    visualAssets === undefined ? fetch("/models/bigcity/manifest.json") : Promise.resolve(null),
    typeof placement === "string" ? fetch(placement) : Promise.resolve(null),
  ]);
  if (assetResponse !== null && !assetResponse.ok) throw new Error(`BigCity building manifest failed: ${assetResponse.status}`);
  if (placementResponse !== null && !placementResponse.ok) throw new Error(`City building placement failed: ${placementResponse.status}`);
  const manifest = (assetResponse === null ? await visualAssets!.json("/models/bigcity/manifest.json")
    : await assetResponse.json()) as BigCityManifest;
  const placementBytes = placementResponse === null ? null : await placementResponse.arrayBuffer();
  const placementSha256 = placementBytes === null ? (placement as { readonly sha256: string }).sha256
    : [...new Uint8Array(await crypto.subtle.digest("SHA-256", placementBytes))]
      .map(value => value.toString(16).padStart(2, "0")).join("");
  const placementData = (placementBytes === null ? (placement as { readonly value: unknown }).value
    : JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(placementBytes))) as BuildingPlacementData;
  if (placementData.schema_version !== "aero-bench.city-building-placement/v1"
      || placementData.mesh_pack_source_sha256 !== sourceSha256
      || placementData.mesh_pack_manifest_sha256 !== manifestSha256
      || placementData.source_kind !== "verified-source-surfaces-and-inscribed-rectangles"
      || !/^[0-9a-f]{64}$/.test(placementData.displayed_surface_sha256)
      || !Array.isArray(placementData.complete_footprint_buildings)
      || !Array.isArray(placementData.complete_footprint_envelopes)) {
    throw new Error("Building collision boxes do not match the verified OSM mesh source");
  }
  const ids = new Set(buildings.map(building => building.id));
  const placedIds = new Set(placementData.placements.map(item => item.building_id));
  const completeIds = new Set(placementData.complete_footprint_buildings);
  const envelopeIds = new Set(placementData.complete_footprint_envelopes.map(item => item?.building_id));
  const completeEnvelopes = new Map(placementData.complete_footprint_envelopes.map(item => [item.building_id, item]));
  const occludedIds = new Set(placementData.occluded_buildings);
  const visibleIds = new Set([...placedIds, ...completeIds]);
  const sourceShapes = new Map(buildings.map(building => [building.id, building]));
  if (occludedIds.size !== placementData.occluded_buildings.length
      || completeIds.size !== placementData.complete_footprint_buildings.length
      || envelopeIds.size !== placementData.complete_footprint_envelopes.length
      || completeEnvelopes.size !== completeIds.size
      || [...completeIds].some(id => !envelopeIds.has(id))
      || [...completeIds].some(id => !ids.has(id) || !sourceShapes.get(id)?.sourceShape
        || verifiedEnvelopeFor(id, sourceShapes.get(id)!.bounds, completeEnvelopes).building_id !== id)
      || [...visibleIds].some(id => occludedIds.has(id))
      || ids.size !== visibleIds.size + occludedIds.size
      || [...ids].some(id => !visibleIds.has(id) && !occludedIds.has(id))) {
    throw new Error("Building collision boxes do not cover the OSM building inventory");
  }
  const entries = manifest.entries.filter(entry => entry.subgroup === "BigCity · 建筑"
    && /^Modern Building \d{2}$/.test(entry.title) && entry.model_url);
  if (entries.length !== 17 || new Set(entries.map(entry => entry.title)).size !== 17) {
    throw new Error(`Expected 17 supplied modern building models, found ${entries.length}`);
  }
  const [modernAtlas, modernNormal] = await Promise.all([
    new SharedCityTextureLoader().loadAsync(visualAssets === undefined
      ? "/models/bigcity/images/0c54a76aea770094497362cbfe0e2c7d.webp"
      : await visualAssets.url("/models/bigcity/images/0c54a76aea770094497362cbfe0e2c7d.webp")),
    new SharedCityTextureLoader().loadAsync(visualAssets === undefined
      ? "/models/bigcity/images/5403341011f88fe47803c35132249b44.webp"
      : await visualAssets.url("/models/bigcity/images/5403341011f88fe47803c35132249b44.webp")),
  ]);
  modernNormal.colorSpace = THREE.NoColorSpace;
  const modernGlass = glassMask(modernAtlas);
  const modernWindows = [0, 1, 2].map(variant => windowMask(modernGlass, variant));
  const windowMaterials: THREE.MeshStandardMaterial[] = [];
  const roofMap = facadeTile(modernAtlas, [475, 5, 190, 110]);
  const stoneMap = facadeTile(modernAtlas, [338, 140, 190, 174]);
  const stoneMaterial = new THREE.MeshStandardMaterial({ map: stoneMap, color: 0xf0eeea, roughness: .88 });
  const roofMaterial = new THREE.MeshStandardMaterial({ map: roofMap, color: 0xffffff, roughness: .96 });
  const plantMaterial = new THREE.MeshStandardMaterial({ color: 0xa6afb2, metalness: .3, roughness: .68 });
  const fanMaterial = new THREE.MeshStandardMaterial({ color: 0x303b40, metalness: .4, roughness: .7 });
  const meteredMaterials = [0xe4dfd1, 0xc3bbae, 0xccd0ce].map((color, index) => {
    const tileRegion = ([[689, 640, 188, 196], [330, 330, 76, 156], [0, 960, 140, 128]] as const)[index]!;
    const facadeMap = facadeTile(modernAtlas, tileRegion);
    const facadeNormal = facadeTile(modernNormal, tileRegion, THREE.NoColorSpace);
    const facadeSurface = facadeTile(modernGlass, tileRegion, THREE.NoColorSpace);
    const facade = new THREE.MeshStandardMaterial({ name: "Supplied BigCity metered stone facade",
      map: facadeMap, normalMap: facadeNormal, normalScale: new THREE.Vector2(.25, .25),
      color, roughness: .85, roughnessMap: facadeSurface, metalness: .15, metalnessMap: facadeSurface,
      emissive: 0xffffff, emissiveMap: facadeTile(modernWindows[index]!, tileRegion), emissiveIntensity: 0 });
    facade.userData.glassFacade = true;
    windowMaterials.push(facade);
    return [facade, stoneMaterial, roofMaterial, plantMaterial, fanMaterial];
  });
  const paneMaterials = meteredMaterials.map(materials => {
    const material = createCityPaneMaterial(materials[0]!.emissiveMap);
    windowMaterials.push(material);
    return material;
  });
  const windowFrame = new THREE.MeshStandardMaterial({ name: "Window aluminium frames",
    color: 0x69777e, metalness: .8, roughness: .32,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
  let detailedWindowCount = 0;
  modernAtlas.dispose();
  const templates = await Promise.all(entries.map(async entry => {
    const scene = await loadFbx(entry.model_url!, "/models/bigcity/texture-aliases.json", visualAssets);
    scene.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      const original = Array.isArray(node.material) ? node.material : [node.material];
      const materials = original.map(material => {
        if (!(material instanceof THREE.MeshPhongMaterial || material instanceof THREE.MeshStandardMaterial)) {
          throw new Error(`Unsupported modern building material in ${entry.title}`);
        }
        const mapped = material.map !== null;
        if (mapped) {
          modernGlass.flipY = material.map!.flipY;
          modernNormal.flipY = material.map!.flipY;
          for (const windows of modernWindows) windows.flipY = material.map!.flipY;
        }
        const lit = new THREE.MeshStandardMaterial({
          name: `${material.name} · source facade with glass and metal finish`,
          color: material.color,
          map: material.map,
          normalMap: mapped ? modernNormal : null,
          normalScale: new THREE.Vector2(0.4, 0.4),
          roughness: mapped ? 0.82 : 0.56,
          roughnessMap: mapped ? modernGlass : null,
          metalness: mapped ? 0.6 : 0.2,
          metalnessMap: mapped ? modernGlass : null,
          envMapIntensity: mapped ? 1.15 : 0.7,
          side: material.side,
          transparent: material.transparent,
          opacity: material.opacity,
        });
        if (mapped) {
          lit.emissive.set(0xffffff);
          lit.emissiveMap = modernWindows[Number(entry.title.slice(-2)) % modernWindows.length]!;
        }
        lit.emissiveIntensity = 0;
        lit.userData.glassFacade = mapped;
        windowMaterials.push(lit);
        return lit;
      });
      node.material = Array.isArray(node.material) ? materials : materials[0]!;
    });
    const bounds = new THREE.Box3().setFromObject(scene);
    if (bounds.isEmpty()) throw new Error(`BigCity facade has no geometry: ${entry.title}`);
    const size = bounds.getSize(new THREE.Vector3());
    if (size.x <= 0 || size.y <= 0 || size.z <= 0) throw new Error(`BigCity facade has invalid dimensions: ${entry.title}`);
    return { entry, scene, bounds, size };
  }));
  const group = new THREE.Group();
  group.name = "Modern glass facades on OSM building positions";
  const usedAssetTitles = new Set<string>();
  const renderedPlacements: BuildingPlacement[] = [];
  const storefronts: { position: THREE.Vector3; rotation: THREE.Quaternion;
    scale: THREE.Vector3; color: THREE.Color }[] = [];
  for (const id of completeIds) {
    const building = sourceShapes.get(id)!;
    const shape = building.sourceShape!;
    const envelope = verifiedEnvelopeFor(id, building.bounds, completeEnvelopes);
    const target = new THREE.Vector3(envelope.width, envelope.height, envelope.depth);
    const geometry = sourceBuildingGeometry(shape, envelope);
    if (!geometry.groups.some(item => item.materialIndex === 0)
        || !geometry.groups.some(item => item.materialIndex === 1)) {
      geometry.dispose();
      throw new Error(`Complete OSM building lacks source walls or roof triangles: ${id}`);
    }
    const variant = hashId(id) % meteredMaterials.length;
    const visual = new THREE.Group();
    visual.position.set(envelope.x, envelope.base_y, envelope.z);
    visual.rotation.y = THREE.MathUtils.degToRad(envelope.rotation_deg);
    visual.name = `Exact verified OSM building surfaces with supplied atlas ${id}`;
    visual.userData.meteredFacade = true;
    visual.userData.target = { kind: "building", id };
    visual.userData.visualAsset = "/models/bigcity/images/0c54a76aea770094497362cbfe0e2c7d.webp";
    visual.userData.sourceGeometry = true;
    visual.userData.collisionEnvelope = "minimum-area-oriented-source-obb";
    visual.userData.collisionBox = envelope;
    const mesh = new THREE.Mesh(geometry, [meteredMaterials[variant]![0]!, meteredMaterials[variant]![2]!]);
    mesh.castShadow = mesh.receiveShadow = true;
    detailedWindowCount += attachCityWindowDetail(mesh, variant, paneMaterials[variant]!, windowFrame);
    visual.add(mesh);
    visual.add(collisionBox(target, 0x2ed9df));
    group.add(visual);
    renderedPlacements.push(envelope);
    usedAssetTitles.add("BigCity atlas on complete verified OSM building surfaces");
  }
  const presentationPlacements = placementData.placements.filter(item => !completeIds.has(item.building_id));
  for (const placement of presentationPlacements) {
    const target = new THREE.Vector3(placement.width, placement.height, placement.depth);
    if (target.x <= 0 || target.y <= 0 || target.z <= 0) throw new Error(`Invalid collision box for ${placement.building_id}`);
    const desiredHeightRatio = target.y / Math.sqrt(target.x * target.z);
    const desiredPlanRatio = target.x / target.z;
    const ranked = templates.map(template => ({
      template,
      score: Math.abs(Math.log(desiredHeightRatio / (template.size.y / Math.sqrt(template.size.x * template.size.z))))
        + 0.5 * Math.abs(Math.log(desiredPlanRatio / (template.size.x / template.size.z))),
    })).sort((left, right) => left.score - right.score);
    const chosen = ranked[hashId(`${placement.building_id}:${placement.part}`) % Math.min(4, ranked.length)]!.template;
    const modular = needsMeteredFacadePart(target.x, target.z) || target.y <= 24;
    usedAssetTitles.add(modular ? "BigCity metered atlas facade" : chosen.entry.title);
    const styleSeed = hashId(`${placement.building_id}:${placement.part}`);
    const visual = modular
      ? createMeteredBuilding(target, meteredMaterials[hashId(placement.building_id) % meteredMaterials.length]!, true,
        styleSeed, hashId(placement.building_id) % meteredMaterials.length !== 2)
      : fitted(chosen.scene.clone(true), target);
    if (modular) {
      const variant = hashId(placement.building_id) % meteredMaterials.length;
      const walls: THREE.Mesh[] = [];
      visual.traverse(node => {
        if (node instanceof THREE.Mesh && hasCityWindowWall(node, meteredMaterials[variant]![0]!)) walls.push(node);
      });
      for (const wall of walls) detailedWindowCount += attachCityWindowDetail(wall, variant, paneMaterials[variant]!, windowFrame);
    }
    visual.position.set(placement.x, placement.base_y, placement.z);
    visual.rotation.y = THREE.MathUtils.degToRad(placement.rotation_deg);
    visual.name = `${modular ? "Metered BigCity facade" : chosen.entry.title} at OSM ${placement.building_id}:${placement.part}`;
    visual.userData.meteredFacade = modular;
    visual.userData.target = { kind: "building", id: placement.building_id };
    visual.userData.visualAsset = modular
      ? "/models/bigcity/images/0c54a76aea770094497362cbfe0e2c7d.webp" : chosen.entry.model_url;
    visual.userData.collisionBox = placement;
    visual.add(collisionBox(target, 0x2ed9df));
    group.add(visual);
    renderedPlacements.push(placement);
    if (placement.height >= 8 && placement.width >= 10 && hashId(placement.building_id) % 3 !== 0) {
      const angle = THREE.MathUtils.degToRad(placement.rotation_deg);
      for (const side of [1, -1]) {
        const facing = angle + (side < 0 ? Math.PI : 0);
        storefronts.push({
          position: new THREE.Vector3(
            placement.x + Math.sin(angle) * side * (placement.depth / 2 + 0.04),
            placement.base_y + 2.35,
            placement.z + Math.cos(angle) * side * (placement.depth / 2 + 0.04)),
          rotation: new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), facing),
          scale: new THREE.Vector3(Math.min(placement.width * 0.7, 18), 1.75, 1),
          color: new THREE.Color().setHex(hashId(`${placement.building_id}:${side}`) % 5 === 0
            ? 0xcceaff : 0xffd3a0),
        });
      }
    }
  }
  attachMeteredRoofs(group);
  // Pavement belongs to the source road/walkbed polygons, not expanded building boxes.
  const storefrontLights = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ map: storefrontTexture(), alphaTest: 0.25,
      side: THREE.FrontSide, toneMapped: false }), storefronts.length);
  const transform = new THREE.Matrix4();
  for (const [index, shop] of storefronts.entries()) {
    transform.compose(shop.position, shop.rotation, shop.scale);
    storefrontLights.setMatrixAt(index, transform);
    storefrontLights.setColorAt(index, shop.color);
  }
  storefrontLights.instanceMatrix.needsUpdate = true;
  storefrontLights.frustumCulled = false;
  storefrontLights.raycast = () => undefined;
  storefrontLights.name = "Provided city buildings with illuminated ground floor shops";
  storefrontLights.userData.target = { kind: "building", id: "visual-storefronts" };
  storefrontLights.userData.cityStorefrontLight = true;
  storefrontLights.visible = false;
  group.add(storefrontLights);
  group.userData.storefrontLights = storefrontLights;
  group.userData.storefrontLightCount = storefronts.length;
  group.userData.assetCount = templates.length;
  group.userData.buildingStyle = "metered-facades-and-modern-glass";
  group.userData.usedAssetTitles = [...usedAssetTitles].sort();
  group.userData.buildingCount = visibleIds.size;
  group.userData.occludedSourceBuildings = placementData.occluded_buildings;
  group.userData.visualPartCount = renderedPlacements.length;
  group.userData.completeSourceBuildingCount = completeIds.size;
  group.userData.deferredSourceBuildingCount = ids.size - completeIds.size;
  group.userData.conservativeCollisionEnvelopeCount = completeIds.size;
  group.userData.placementSha256 = placementSha256;
  group.userData.displayedSurfaceSha256 = placementData.displayed_surface_sha256;
  group.userData.buildingPlacements = renderedPlacements;
  group.userData.detailedWindowCount = detailedWindowCount;
  group.userData.windowMaterials = windowMaterials;
  group.userData.windowMaterialCount = windowMaterials.length;
  // Building visuals, their collision proxies and window LODs are placed once here and
  // never move again; attachMeteredRoofs has already consumed the owner matrices above.
  // The shared storefront batch is frozen directly and also left on the group root.
  cacheStaticTransforms(storefrontLights);
  for (const visual of group.children) {
    if (visual === storefrontLights) continue;
    cacheStaticTransforms(visual);
  }
  return group;
}

/** Reuse the exact OSM2World tree centers and replace its crossed image planes. */
export async function loadCityTrees(trees: readonly CityTree[], visualAssets?: VerifiedVisualAssets): Promise<THREE.Group> {
  const response = visualAssets === undefined ? await fetch("/models/bigcity/manifest.json") : null;
  if (response !== null && !response.ok) throw new Error(`BigCity tree manifest failed: ${response.status}`);
  const manifest = (response === null ? await visualAssets!.json("/models/bigcity/manifest.json")
    : await response.json()) as BigCityManifest;
  const titles = ["Tree 01", "Tree 03", "Tree 05", "Tree 07", "Tree 09", "Tree 10"];
  const templates = await Promise.all(titles.map(async title => {
    const entry = manifest.entries.find(item => item.title === title && item.model_url !== undefined);
    if (entry?.model_url === undefined) throw new Error(`BigCity tree model is missing: ${title}`);
    return { title, scene: await loadFbx(entry.model_url, "/models/bigcity/texture-aliases.json", visualAssets) };
  }));
  const group = new THREE.Group();
  group.name = "BigCity trees on OSM vegetation positions";
  for (const [index, tree] of trees.entries()) {
    const variants = tree.kind === "conifer" ? templates.slice(4) : templates.slice(0, 4);
    const chosen = variants[hashId(`${tree.x}:${tree.z}:${index}`) % variants.length]!;
    const width = Math.max(2.5, Math.min(tree.width, 13));
    const visual = fitted(chosen.scene.clone(true), new THREE.Vector3(width, tree.height, width));
    visual.position.set(tree.x, 0, tree.z);
    visual.rotation.y = (hashId(`${index}:${tree.kind}`) % 360) * Math.PI / 180;
    visual.userData.visualAsset = chosen.title;
    group.add(visual);
  }
  // Tree visuals sit at fixed OSM positions; only their fitted chain was ever static.
  for (const visual of group.children) cacheStaticTransforms(visual);
  group.userData.treeCount = trees.length;
  return group;
}

type VehicleType = "sedan" | "taxi" | "police" | "bus" | "truck" | "bicycle";
type VehicleSample = readonly [id: string, x: number, z: number, heading: number, type: VehicleType, groundY?: number];
type PersonSample = readonly [id: string, x: number, z: number, heading: number, groundY?: number];
interface SignalLocation { readonly id: string; readonly tls: string; readonly link: number; readonly x: number; readonly z: number; readonly heading: number; }
interface TrafficFrame { readonly second: number; readonly vehicles: readonly VehicleSample[]; readonly persons: readonly PersonSample[]; readonly tls: Readonly<Record<string, string>>; }
export interface TrafficData {
  readonly schema_version: "aero-bench.city-sumo-preview/v1" | "aero-bench.city-sumo-preview/v2";
  readonly source_kind: "offline-sumo-engineering-preview";
  readonly source_network_sha256: string;
  readonly mesh_pack_source_sha256: string;
  readonly duration_seconds: number;
  readonly step_seconds: number;
  readonly signals: readonly SignalLocation[];
  readonly frames: readonly TrafficFrame[];
  readonly demand: { readonly observed: Readonly<Record<string, number>> };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function gridIntervalCount(duration: unknown, step: unknown, label: string): number {
  if (typeof duration !== "number" || !Number.isFinite(duration) || duration <= 0
      || typeof step !== "number" || !Number.isFinite(step) || step <= 0) {
    throw new Error(`${label} duration and step must be finite positive numbers`);
  }
  const quotient = duration / step;
  const intervals = Math.round(quotient);
  if (!Number.isSafeInteger(intervals) || intervals < 1 || Math.abs(quotient - intervals) > 1e-9) {
    throw new Error(`${label} duration must contain a whole, safe number of steps`);
  }
  return intervals;
}

function onFrameGrid(actual: number, expected: number): boolean {
  const tolerance = Math.max(1e-9, Math.abs(expected) * Number.EPSILON * 16);
  return Math.abs(actual - expected) <= tolerance;
}

function trafficContractError(detail: string): never {
  throw new Error(`SUMO city preview contract is invalid: ${detail}`);
}

/** Validate an authored or measured SUMO preview without imposing a city-specific fixture count. */
export function parseCityTrafficData(value: unknown): TrafficData {
  if (!isRecord(value)) trafficContractError("root must be an object");
  if ((value.schema_version !== "aero-bench.city-sumo-preview/v1"
      && value.schema_version !== "aero-bench.city-sumo-preview/v2")
      || value.source_kind !== "offline-sumo-engineering-preview") {
    trafficContractError("schema or source kind is unsupported");
  }
  if (!isSha256(value.source_network_sha256) || !isSha256(value.mesh_pack_source_sha256)) {
    trafficContractError("source identities must be SHA-256 digests");
  }
  let intervals: number;
  try {
    intervals = gridIntervalCount(value.duration_seconds, value.step_seconds, "SUMO city preview");
  } catch (error) {
    trafficContractError(error instanceof Error ? error.message : "time grid is invalid");
  }
  if (!Array.isArray(value.signals) || !Array.isArray(value.frames)) {
    trafficContractError("signals and frames must be arrays");
  }
  if (value.frames.length !== intervals + 1) trafficContractError("frame count does not match its time grid");
  if (!isRecord(value.demand) || !isRecord(value.demand.observed)
      || Object.entries(value.demand.observed).some(([key, count]) => key.length === 0
        || typeof count !== "number" || !Number.isSafeInteger(count) || count < 0)) {
    trafficContractError("observed demand counts must be non-negative safe integers");
  }

  const signalIds = new Set<string>();
  const signalLinks = new Set<string>();
  const maximumLinkByTls = new Map<string, number>();
  for (const signal of value.signals) {
    if (!isRecord(signal) || typeof signal.id !== "string" || signal.id.length === 0
        || typeof signal.tls !== "string" || signal.tls.length === 0
        || typeof signal.link !== "number" || !Number.isSafeInteger(signal.link) || signal.link < 0
        || typeof signal.x !== "number" || !Number.isFinite(signal.x)
        || typeof signal.z !== "number" || !Number.isFinite(signal.z)
        || typeof signal.heading !== "number" || !Number.isFinite(signal.heading)) {
      trafficContractError("signal locations must have typed finite coordinates and TLS links");
    }
    if (signalIds.has(signal.id)) trafficContractError(`duplicate signal identity ${signal.id}`);
    signalIds.add(signal.id);
    const linkIdentity = `${signal.tls}\u0000${signal.link}`;
    if (signalLinks.has(linkIdentity)) trafficContractError(`duplicate signal TLS link ${signal.tls}:${signal.link}`);
    signalLinks.add(linkIdentity);
    maximumLinkByTls.set(signal.tls, Math.max(maximumLinkByTls.get(signal.tls) ?? -1, signal.link));
  }

  const actorKinds = new Map<string, string>();
  const vehicleTypes = new Set<VehicleType>(["sedan", "taxi", "police", "bus", "truck", "bicycle"]);
  for (let frameIndex = 0; frameIndex < value.frames.length; frameIndex++) {
    const frame = value.frames[frameIndex];
    if (!isRecord(frame) || typeof frame.second !== "number" || !Number.isFinite(frame.second)
        || !onFrameGrid(frame.second, frameIndex * (value.step_seconds as number))
        || !Array.isArray(frame.vehicles) || !Array.isArray(frame.persons) || !isRecord(frame.tls)) {
      trafficContractError(`frame ${frameIndex} is not on the declared typed frame grid`);
    }
    const frameActorIds = new Set<string>();
    for (const sample of frame.vehicles) {
      if (!Array.isArray(sample) || (sample.length !== 5 && sample.length !== 6)
          || typeof sample[0] !== "string" || sample[0].length === 0
          || [sample[1], sample[2], sample[3]].some(number => typeof number !== "number" || !Number.isFinite(number))
          || !vehicleTypes.has(sample[4] as VehicleType)
          || (sample.length === 6 && (typeof sample[5] !== "number" || !Number.isFinite(sample[5])))) {
        trafficContractError(`frame ${frameIndex} has an invalid vehicle sample`);
      }
      if (frameActorIds.has(sample[0])) trafficContractError(`frame ${frameIndex} repeats actor ${sample[0]}`);
      frameActorIds.add(sample[0]);
      const kind = `vehicle:${sample[4] as string}`;
      const priorKind = actorKinds.get(sample[0]);
      if (priorKind !== undefined && priorKind !== kind) trafficContractError(`actor ${sample[0]} changes type`);
      actorKinds.set(sample[0], kind);
    }
    for (const sample of frame.persons) {
      if (!Array.isArray(sample) || (sample.length !== 4 && sample.length !== 5)
          || typeof sample[0] !== "string" || sample[0].length === 0
          || [sample[1], sample[2], sample[3]].some(number => typeof number !== "number" || !Number.isFinite(number))
          || (sample.length === 5 && (typeof sample[4] !== "number" || !Number.isFinite(sample[4])))) {
        trafficContractError(`frame ${frameIndex} has an invalid pedestrian sample`);
      }
      if (frameActorIds.has(sample[0])) trafficContractError(`frame ${frameIndex} repeats actor ${sample[0]}`);
      frameActorIds.add(sample[0]);
      const priorKind = actorKinds.get(sample[0]);
      if (priorKind !== undefined && priorKind !== "person") trafficContractError(`actor ${sample[0]} changes type`);
      actorKinds.set(sample[0], "person");
    }

    const tlsEntries = Object.entries(frame.tls);
    if (tlsEntries.length !== maximumLinkByTls.size
        || tlsEntries.some(([tls]) => !maximumLinkByTls.has(tls))) {
      trafficContractError(`frame ${frameIndex} TLS identities do not match the declared signals`);
    }
    for (const [tls, maximumLink] of maximumLinkByTls) {
      const state = frame.tls[tls];
      if (typeof state !== "string" || !/^[rygGsuOo]+$/.test(state) || state.length <= maximumLink) {
        trafficContractError(`frame ${frameIndex} has an invalid state for TLS ${tls}`);
      }
    }
  }
  return value as unknown as TrafficData;
}

export interface CityTrafficDisplayLimits { vehicles: number; bicycles: number; pedestrians: number }

/** Pick recorded identities once, so scrubbing cannot swap which trajectories are shown. */
export function recordedDisplayIds(frames: readonly TrafficFrame[], limits: CityTrafficDisplayLimits):
    { vehicles: ReadonlySet<string>; bicycles: ReadonlySet<string>; pedestrians: ReadonlySet<string> } {
  for (const kind of ["vehicles", "bicycles", "pedestrians"] as const) {
    const count = limits[kind];
    if (!Number.isSafeInteger(count) || count < 0) throw new RangeError(`SUMO ${kind} display limit must be a non-negative integer`);
  }
  const vehicles = new Map<string, number>(), bicycles = new Map<string, number>();
  const pedestrians = new Map<string, number>();
  const count = (records: Map<string, number>, id: string): void => {
    records.set(id, (records.get(id) ?? 0) + 1);
  };
  for (const frame of frames) {
    for (const sample of frame.vehicles) count(sample[4] === "bicycle" ? bicycles : vehicles, sample[0]);
    for (const sample of frame.persons) count(pedestrians, sample[0]);
  }
  const take = (ids: Map<string, number>, limit: number): ReadonlySet<string> =>
    new Set([...ids].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, limit).map(([id]) => id));
  return {
    vehicles: take(vehicles, limits.vehicles), bicycles: take(bicycles, limits.bicycles),
    pedestrians: take(pedestrians, limits.pedestrians),
  };
}
type FlightSample = readonly [id: string, x: number, z: number, up: number, yaw: number, pitch: number, roll: number, inAir: boolean];
interface RecordedFlightData {
  readonly schema_version: "aero-bench.city-px4-preview/v1";
  readonly source_kind: "recorded-px4-gazebo-engineering-replay";
  readonly independent_from_sumo_preview: true;
  readonly verification_scope: "executor_validation";
  readonly verification_status: "invalid";
  readonly mesh_pack_source_sha256: string;
  readonly duration_seconds: number;
  readonly step_seconds: number;
  readonly vehicle_ids: readonly string[];
  readonly frames: readonly (readonly FlightSample[])[];
}
interface PlannedFlightData {
  readonly schema_version: "aero-bench.city-planned-flight-preview/v1" | "aero-bench.city-planned-flight-preview/v2";
  readonly source_kind: "planned-visual-flight";
  readonly physical_simulation: false;
  readonly independent_from_sumo_preview: true;
  readonly mesh_pack_source_sha256: string;
  readonly geometry_audit: {
    readonly minimum_building_clearance_m: number;
    readonly minimum_aircraft_vertical_separation_m: number;
  };
  readonly duration_seconds: number;
  readonly step_seconds: number;
  readonly vehicle_ids: readonly string[];
  readonly frames: readonly (readonly FlightSample[])[];
}
type FlightData = RecordedFlightData | PlannedFlightData;
/** Canonical scenes draw only effective signal fixtures; earlier scenes draw every source signal. */
export type CitySignalSelection = ReadonlySet<string> | "all-source-signals";

const aircraftAssets = {
  "uav.01": { name: CITY_FLEET_ASSETS[0].label, path: CITY_FLEET_ASSETS[0].url,
    size: new THREE.Vector3(CITY_FLEET_ASSETS[0].sizeM.x, CITY_FLEET_ASSETS[0].sizeM.y,
      CITY_FLEET_ASSETS[0].sizeM.z), trailColor: 0x52deef },
  "uav.02": { name: CITY_FLEET_ASSETS[1].label, path: CITY_FLEET_ASSETS[1].url,
    size: new THREE.Vector3(CITY_FLEET_ASSETS[1].sizeM.x, CITY_FLEET_ASSETS[1].sizeM.y,
      CITY_FLEET_ASSETS[1].sizeM.z), trailColor: 0xffb456 },
} as const;
type AircraftId = keyof typeof aircraftAssets;

function flightContractError(detail: string): never {
  throw new Error(`City flight preview contract is invalid: ${detail}`);
}

function parseCityFlightData(value: unknown, meshPackSourceSha256: string): FlightData {
  if (!isRecord(value)) flightContractError("root must be an object");
  const recorded = value.source_kind === "recorded-px4-gazebo-engineering-replay"
    && value.schema_version === "aero-bench.city-px4-preview/v1"
    && value.verification_scope === "executor_validation"
    && value.verification_status === "invalid";
  const audit = value.geometry_audit;
  const planned = value.source_kind === "planned-visual-flight"
    && (value.schema_version === "aero-bench.city-planned-flight-preview/v1"
      || value.schema_version === "aero-bench.city-planned-flight-preview/v2")
    && value.physical_simulation === false
    && isRecord(audit)
    && typeof audit.minimum_building_clearance_m === "number"
    && Number.isFinite(audit.minimum_building_clearance_m)
    && audit.minimum_building_clearance_m >= 20
    && typeof audit.minimum_aircraft_vertical_separation_m === "number"
    && Number.isFinite(audit.minimum_aircraft_vertical_separation_m)
    && audit.minimum_aircraft_vertical_separation_m >= 10;
  if (!recorded && !planned) flightContractError("source identity or physical-simulation claim is unsupported");
  if (value.independent_from_sumo_preview !== true
      || !isSha256(value.mesh_pack_source_sha256)
      || value.mesh_pack_source_sha256 !== meshPackSourceSha256) {
    flightContractError("flight must independently reference the loaded city mesh source");
  }

  let intervals: number;
  try {
    intervals = gridIntervalCount(value.duration_seconds, value.step_seconds, "City flight preview");
  } catch (error) {
    flightContractError(error instanceof Error ? error.message : "time grid is invalid");
  }
  const supportedIds = Object.keys(aircraftAssets) as AircraftId[];
  if (!Array.isArray(value.vehicle_ids)) {
    flightContractError("aircraft identities must be an array");
  }
  const vehicleIds = value.vehicle_ids;
  if (vehicleIds.some(id => typeof id !== "string" || !(id in aircraftAssets))
      || new Set(vehicleIds).size !== vehicleIds.length
      || vehicleIds.length !== supportedIds.length
      || supportedIds.some(id => !vehicleIds.includes(id))) {
    flightContractError("aircraft identities must match the supported fleet exactly once");
  }
  if (!Array.isArray(value.frames) || value.frames.length !== intervals + 1) {
    flightContractError("frame count does not match the independent flight time grid");
  }
  const declaredIds = new Set(vehicleIds as string[]);
  const flies = new Set<string>();
  for (let frameIndex = 0; frameIndex < value.frames.length; frameIndex++) {
    const frame = value.frames[frameIndex];
    if (!Array.isArray(frame) || frame.length !== declaredIds.size) {
      flightContractError(`frame ${frameIndex} does not contain every declared aircraft once`);
    }
    const frameIds = new Set<string>();
    for (const sample of frame) {
      if (!Array.isArray(sample) || sample.length !== 8
          || typeof sample[0] !== "string" || !declaredIds.has(sample[0])
          || sample.slice(1, 7).some(number => typeof number !== "number" || !Number.isFinite(number))
          || typeof sample[7] !== "boolean") {
        flightContractError(`frame ${frameIndex} has an invalid aircraft sample`);
      }
      if (frameIds.has(sample[0])) flightContractError(`frame ${frameIndex} repeats aircraft ${sample[0]}`);
      frameIds.add(sample[0]);
      if (sample[7]) flies.add(sample[0]);
    }
  }
  if (flies.size !== declaredIds.size) flightContractError("each declared aircraft must have an airborne sample");
  return value as unknown as FlightData;
}

const vehicleAssets: Readonly<Record<VehicleType, { readonly path: string; readonly size: THREE.Vector3;
  readonly aliases?: string; readonly collisionSize?: THREE.Vector3 }>> = {
  sedan: { path: "/models/city-runtime/Car_6-preview.glb", size: new THREE.Vector3(1.8, 1.5, 4.5) },
  taxi: { path: "/models/city-runtime/Taxi_1-day-preview.glb", size: new THREE.Vector3(1.85, 1.65, 4.7) },
  police: { path: "/models/city-runtime/Police_1-day-preview.glb", size: new THREE.Vector3(1.9, 1.7, 4.8) },
  bus: { path: "/models/incoming/urban-traffic/fbx/0c0ace7261ddca04ea015e96dfcfbfee.fbx", size: new THREE.Vector3(2.5, 3.3, 11.5), aliases: "/models/incoming/urban-traffic/texture-aliases.json" },
  truck: { path: "/models/incoming/urban-traffic/fbx/d85819a07026b954c8699700056d1aec.fbx", size: new THREE.Vector3(2.2, 3.2, 7), aliases: "/models/incoming/urban-traffic/texture-aliases.json" },
  bicycle: { path: "/models/city-runtime/Bicycle_Man_34-bike-only.glb",
    size: new THREE.Vector3(0.65, 1.05, 1.8), collisionSize: new THREE.Vector3(0.65, 1.72, 1.8) },
};

const pedestrianAssets = [
  { name: "casual27_m", path: "/models/city-runtime/casual27_m_highpoly_walk.glb" },
  { name: "sportive06_f", path: "/models/incoming/citizens/fbx/7f0a640a8c9985640b6d9b0bceac81fb.fbx" },
  { name: "business03_m", path: "/models/incoming/citizens/fbx/9f10c5ecad081aa4e88bff3ffa7bb88e.fbx" },
  { name: "casual02_f", path: "/models/incoming/citizens/fbx/1089a793ce2490548ba33e2b6ba83d82.fbx" },
  { name: "casual01_m", path: "/models/incoming/citizens/fbx/01ba8670c2766ba43b98968966ac0b24.fbx" },
  { name: "business04_f", path: "/models/incoming/citizens/fbx/67c5435b8332cb6479964424131f2d53.fbx" },
  { name: "sportive09_m", path: "/models/incoming/citizens/fbx/09edd510e7843964bb78269abd0b8bb2.fbx" },
  { name: "casual11_m", path: "/models/incoming/citizens/fbx/35a034e4f2c6eed4981fcaa01eccc667.fbx" },
] as const;

function prepareCitizenFbx(model: THREE.Group, name: string): THREE.Group {
  // FBX skin bounds depend on up-to-date world bone matrices.
  model.updateMatrixWorld(true);
  const upright = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
  if (upright.y < upright.z * 2 || upright.y < upright.x * 1.1) {
    throw new Error(`Provided pedestrian ${name} is not upright after FBX import`);
  }
  let texturedMeshes = 0;
  model.traverse(node => {
    if (!(node instanceof THREE.SkinnedMesh)) return;
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (!(material instanceof THREE.MeshPhongMaterial || material instanceof THREE.MeshStandardMaterial)) continue;
      if (!material.map) continue;
      // Unity assigns the color through its material; raw FBX carries black here.
      material.color.set(0xffffff);
      texturedMeshes++;
    }
  });
  if (texturedMeshes === 0) throw new Error(`Provided pedestrian ${name} has no source texture`);
  return model;
}

function retargetCitizenWalk(source: THREE.Group, target: THREE.Group,
                             sourceClip: THREE.AnimationClip): THREE.AnimationClip {
  const tracks: THREE.QuaternionKeyframeTrack[] = [];
  for (const track of sourceClip.tracks) {
    if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
    const name = track.name.slice(0, -".quaternion".length);
    const sourceBone = source.getObjectByName(name);
    const targetBone = target.getObjectByName(name);
    if (!(sourceBone instanceof THREE.Bone) || !(targetBone instanceof THREE.Bone)) continue;
    const sourceInverse = sourceBone.quaternion.clone().invert();
    const values = Float32Array.from(track.values);
    for (let index = 0; index < values.length; index += 4) {
      const pose = new THREE.Quaternion().fromArray(values, index);
      const adjusted = targetBone.quaternion.clone().multiply(sourceInverse).multiply(pose).normalize();
      adjusted.toArray(values, index);
    }
    tracks.push(new THREE.QuaternionKeyframeTrack(track.name, track.times, values));
  }
  if (tracks.length < 30) throw new Error("Provided pedestrian rig has too few shared walking bones");
  return new THREE.AnimationClip("walk-retargeted", sourceClip.duration, tracks);
}

type VehicleLampMaterial = THREE.MeshStandardMaterial | THREE.MeshPhongMaterial;

function frontLampName(name: string): boolean {
  const lower = name.toLowerCase();
  return lower === "glass_light" || lower.includes("headlights");
}

/** The imported traffic files use different forward axes; infer each one from its front lamp geometry. */
function vehicleModelRotation(source: THREE.Object3D, type: VehicleType): number {
  if (type === "bicycle") return 0;
  source.updateMatrixWorld(true);
  const body = new THREE.Box3().setFromObject(source);
  const lamps = new THREE.Box3();
  const lampDepths: number[] = [];
  const point = new THREE.Vector3();
  source.traverse(node => {
    if (!(node instanceof THREE.Mesh) || !node.visible) return;
    const materials: THREE.Material[] = Array.isArray(node.material) ? node.material : [node.material];
    const positions = node.geometry.getAttribute("position");
    const indices = node.geometry.index;
    const groups = node.geometry.groups.length > 0 ? node.geometry.groups
      : [{ start: 0, count: indices?.count ?? positions.count, materialIndex: 0 }];
    for (const group of groups) {
      const name = materials[group.materialIndex]?.name.toLowerCase() ?? "";
      if (type === "bus" || type === "truck" ? !name.includes("headlights") : name !== "glass_light") continue;
      for (let index = group.start; index < group.start + group.count; index++) {
        point.fromBufferAttribute(positions, indices?.getX(index) ?? index).applyMatrix4(node.matrixWorld);
        lamps.expandByPoint(point);
        lampDepths.push(point.z);
      }
    }
  });
  lampDepths.sort((a, b) => a - b);
  const frontZ = lampDepths[Math.floor(lampDepths.length / 2)] ?? NaN;
  const bodyZ = body.isEmpty() ? NaN : body.getCenter(new THREE.Vector3()).z;
  const extentZ = body.getSize(new THREE.Vector3()).z;
  if (!Number.isFinite(frontZ) || !Number.isFinite(bodyZ) || Math.abs(frontZ - bodyZ) < extentZ * 0.1) {
    throw new Error(`Provided ${type} vehicle front lamps cannot establish its forward axis: lamp median ${frontZ.toFixed(2)} [${lamps.min.z.toFixed(2)}, ${lamps.max.z.toFixed(2)}], body ${bodyZ.toFixed(2)} [${body.min.z.toFixed(2)}, ${body.max.z.toFixed(2)}]`);
  }
  return frontZ > bodyZ ? Math.PI : 0;
}

/** The supplied vehicle files have separate lamp faces and textures. Keep their UVs and materials. */
function vehicleLampMaterials(templates: Readonly<Record<VehicleType, THREE.Group>>):
    { material: VehicleLampMaterial; rear: boolean }[] {
  const lamps: { material: VehicleLampMaterial; rear: boolean }[] = [];
  for (const [type, template] of Object.entries(templates) as [VehicleType, THREE.Group][]) {
    if (type === "bicycle") continue;
    let front = 0, rear = 0;
    const copies = new Map<THREE.Material, VehicleLampMaterial>();
    template.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      const arrayMaterial = Array.isArray(node.material);
      const originals: THREE.Material[] = arrayMaterial ? node.material as THREE.Material[] : [node.material];
      const materials = originals.map(original => {
        const name = original.name.toLowerCase();
        const isFront = frontLampName(name);
        const isRear = name === "taxi_light_d" || name.includes("taillight");
        if (!isFront && !isRear) return original;
        if (!(original instanceof THREE.MeshStandardMaterial || original instanceof THREE.MeshPhongMaterial)) {
          throw new Error(`Vehicle ${type} lamp has an unsupported material: ${original.name}`);
        }
        if (isFront) front++;
        if (isRear) rear++;
        let material = copies.get(original);
        if (material === undefined) {
          material = original.clone();
          material.emissive.setHex(name === "glass_light" ? 0xffe9ce : 0xffffff);
          if (material.map !== null && material.emissiveMap === null) material.emissiveMap = material.map;
          material.emissiveIntensity = 0;
          material.toneMapped = false;
          material.needsUpdate = true;
          copies.set(original, material);
          lamps.push({ material, rear: isRear });
        }
        return material;
      });
      node.material = arrayMaterial ? materials : materials[0]!;
    });
    if (front === 0 || rear === 0) {
      throw new Error(`Provided ${type} vehicle has no separate front and rear lamp faces`);
    }
  }
  return lamps;
}

function onlyHighestLod(scene: THREE.Object3D): void {
  scene.traverse(node => {
    if (node instanceof THREE.Mesh && /_LOD[1-9]\d*$/i.test(node.name)) node.visible = false;
  });
}

/** Apply the same source orientation and fitting before static vehicle surfaces are merged. */
export function prepareCityVehicleTemplate(source: THREE.Group, type: VehicleType): THREE.Group {
  onlyHighestLod(source);
  return fitted(source, vehicleAssets[type].size, vehicleModelRotation(source, type));
}

function signalMaterial(mesh: THREE.Mesh): THREE.MeshStandardMaterial[] {
  const arrayMaterial = Array.isArray(mesh.material);
  const original: THREE.Material[] = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  const materials = original.map(material => material.clone());
  mesh.material = arrayMaterial ? materials : materials[0]!;
  for (const material of materials) {
    if (!(material instanceof THREE.MeshStandardMaterial)) continue;
    if (material.name.endsWith("SG7")) {
      material.emissive.set(0xff1f1f);
      material.emissiveIntensity = 0.02;
      material.toneMapped = false;
    } else if (material.name.endsWith("SG3")) {
      material.emissive.set(0xffb522);
      material.emissiveIntensity = 0.02;
      material.toneMapped = false;
    } else if (material.name.endsWith("SG8")) {
      material.emissive.set(0x42fa62);
      material.emissiveIntensity = 0.02;
      material.toneMapped = false;
    } else if (material.name.endsWith("SG1")) {
      material.color.setHex(0x78828a);
      material.metalness = 0.2;
      material.roughness = 0.7;
    }
  }
  return materials.filter((value): value is THREE.MeshStandardMaterial => value instanceof THREE.MeshStandardMaterial);
}

/** Displayed signal height; must equal SIGNAL_DISPLAY_HEIGHT_M in scripts/city_fixture_geometry.py,
 * which places the measured arm's lower edge above the declared motor height band. */
export const SIGNAL_DISPLAY_HEIGHT_M = 6.4;

function trafficLampTemplate(lamp: THREE.Group): THREE.Group {
  const lampBounds = new THREE.Box3().setFromObject(lamp);
  const lampSourceSize = lampBounds.getSize(new THREE.Vector3());
  if (Math.abs(lampSourceSize.x - 3.94872) > 0.02 || Math.abs(lampSourceSize.y - 5.70084) > 0.02
      || Math.abs(lampSourceSize.z - 1.24054) > 0.02) {
    throw new Error("Traffic lamp geometry changed; its pole anchor must be remeasured");
  }
  const lampScale = SIGNAL_DISPLAY_HEIGHT_M / lampSourceSize.y;
  lamp.updateMatrixWorld(true);
  const lampParts = new Map<string, { material: THREE.MeshStandardMaterial; geometries: THREE.BufferGeometry[] }>();
  lamp.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    if (Array.isArray(node.material) || !(node.material instanceof THREE.MeshStandardMaterial)) {
      throw new Error("Provided traffic lamp has an unsupported material layout");
    }
    const name = node.material.name;
    const part = lampParts.get(name) ?? { material: node.material, geometries: [] };
    part.geometries.push((node.geometry.index === null ? node.geometry.clone() : node.geometry.toNonIndexed())
      .applyMatrix4(node.matrixWorld));
    lampParts.set(name, part);
  });
  if (lampParts.size !== 6) throw new Error("Provided traffic lamp material inventory changed");
  const lampTemplate = new THREE.Group();
  for (const [name, part] of lampParts) {
    const geometry = mergeGeometries(part.geometries);
    for (const fragment of part.geometries) fragment.dispose();
    if (geometry === null) throw new Error(`Traffic lamp geometry cannot be merged: ${name}`);
    geometry.translate(-1.12309, 0, 0.24263);
    geometry.scale(lampScale, lampScale, lampScale);
    const mesh = new THREE.Mesh(geometry, part.material);
    mesh.name = name;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    lampTemplate.add(mesh);
  }
  return lampTemplate;
}

/** Show real network signal fixtures without inventing an unrecorded phase. */
export async function loadStaticTrafficSignals(signals: readonly StaticSignal[],
                                               visualAssets: VerifiedVisualAssets): Promise<THREE.Group> {
  const group = new THREE.Group();
  group.name = "Verified static network signals; no running phase";
  group.userData.signalCount = signals.length;
  if (signals.length === 0) return group;
  const lamp = (await gltfLoader.loadAsync(await visualAssets.url(
    "/models/incoming/furniture/glb/traffic_light_4.glb"))).scene;
  const template = trafficLampTemplate(lamp);
  for (const location of signals) {
    const object = new THREE.Group();
    object.add(template.clone(true));
    object.position.set(location.x, 0, location.z);
    object.rotation.y = -THREE.MathUtils.degToRad(location.heading);
    object.userData.target = { kind: "traffic_signal", id: location.id };
    object.userData.signalState = "not-running";
    const lens = new Set<string>();
    object.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      for (const material of signalMaterial(node)) {
        if (["SG7", "SG3", "SG8"].some(suffix => material.name.endsWith(suffix))) {
          lens.add(material.name.slice(-3));
          material.emissiveIntensity = 0;
        }
      }
    });
    if (lens.size !== 3) throw new Error("Provided traffic lamp lacks three separate unpowered signal lenses");
    group.add(object);
  }
  cacheStaticTransforms(group);
  return group;
}

function headingBetween(before: number, after: number, fraction: number): number {
  const delta = THREE.MathUtils.euclideanModulo(after - before + 180, 360) - 180;
  return before + delta * fraction;
}

/** Citizens face local +Z while SUMO's zero-degree heading travels toward world -Z. */
/** Root-to-foot nodes, each listed once in parent-before-child order. */
export function footChain(root: THREE.Object3D, feet: readonly THREE.Object3D[]): THREE.Object3D[] {
  const chain: THREE.Object3D[] = [];
  for (const foot of feet) {
    const path: THREE.Object3D[] = [];
    for (let node: THREE.Object3D | null = foot; node !== root; node = node.parent) {
      if (node === null) throw new Error(`Foot ${foot.name} is not below ${root.name}`);
      path.unshift(node);
    }
    for (const node of [root, ...path]) if (!chain.includes(node)) chain.push(node);
  }
  return chain;
}

/** The world matrices root.updateMatrixWorld(true) gives these nodes, without the rest of the subtree. */
export function updateChainWorld(chain: readonly THREE.Object3D[]): void {
  for (const node of chain) {
    if (node.matrixAutoUpdate) node.updateMatrix();
    if (!node.matrixWorldAutoUpdate) continue;
    if (node.parent === null) node.matrixWorld.copy(node.matrix);
    else node.matrixWorld.multiplyMatrices(node.parent.matrixWorld, node.matrix);
  }
}

export function pedestrianYaw(headingDegrees: number): number {
  return Math.PI - THREE.MathUtils.degToRad(headingDegrees);
}

function angleBetween(before: number, after: number, fraction: number): number {
  const delta = THREE.MathUtils.euclideanModulo(after - before + Math.PI, Math.PI * 2) - Math.PI;
  return before + delta * fraction;
}

/** Distance is derived from recorded positions so wheels stop when SUMO bicycles stop. */
export function recordedBicycleDistances(frames: readonly TrafficFrame[]): ReadonlyMap<string, Float32Array> {
  const ids = new Set(frames.flatMap(frame => frame.vehicles
    .filter(sample => sample[4] === "bicycle").map(sample => sample[0])));
  const distances = new Map([...ids].map(id => [id, new Float32Array(frames.length)]));
  let previous = new Map<string, VehicleSample>();
  for (let frameIndex = 0; frameIndex < frames.length; frameIndex++) {
    for (const values of distances.values()) if (frameIndex > 0) values[frameIndex] = values[frameIndex - 1]!;
    const current = new Map<string, VehicleSample>();
    for (const sample of frames[frameIndex]!.vehicles) {
      if (sample[4] !== "bicycle") continue;
      current.set(sample[0], sample);
      const prior = previous.get(sample[0]);
      if (prior !== undefined) {
        const values = distances.get(sample[0])!;
        values[frameIndex] = values[frameIndex]! + Math.hypot(sample[1] - prior[1], sample[2] - prior[2]);
      }
    }
    previous = current;
  }
  return distances;
}

/** Match each pedestrian's walking cycle to recorded travel, including pauses and route gaps. */
export function recordedPedestrianDistances(frames: readonly TrafficFrame[]): ReadonlyMap<string, Float32Array> {
  const ids = new Set(frames.flatMap(frame => frame.persons.map(sample => sample[0])));
  const distances = new Map([...ids].map(id => [id, new Float32Array(frames.length)]));
  let previous = new Map<string, PersonSample>();
  for (let frameIndex = 0; frameIndex < frames.length; frameIndex++) {
    for (const values of distances.values()) if (frameIndex > 0) values[frameIndex] = values[frameIndex - 1]!;
    const current = new Map<string, PersonSample>();
    for (const sample of frames[frameIndex]!.persons) {
      current.set(sample[0], sample);
      const prior = previous.get(sample[0]);
      if (prior !== undefined) {
        const values = distances.get(sample[0])!;
        values[frameIndex] = values[frameIndex]! + Math.hypot(sample[1] - prior[1], sample[2] - prior[2]);
      }
    }
    previous = current;
  }
  return distances;
}

export class CityTrafficPreview {
  readonly group = new THREE.Group();
  readonly data: TrafficData;
  readonly flightData: FlightData;
  private readonly vehicleTemplates: Readonly<Record<VehicleType, THREE.Group>>;
  private readonly personTemplates: readonly { name: string; model: THREE.Group;
    walkClip: THREE.AnimationClip }[];
  private readonly cyclistPoses: CyclistPoseFrames;
  private readonly bicycleDistances: ReadonlyMap<string, Float32Array>;
  private readonly pedestrianDistances: ReadonlyMap<string, Float32Array>;
  private readonly shadowCasters: TrafficShadowCasters;
  private readonly vehicleLamps: { material: VehicleLampMaterial; rear: boolean }[];
  private readonly headlightBeams: { light: THREE.SpotLight; target: THREE.Object3D }[] = [];
  private vehicleLightingTimeOfDay: CityTimeOfDay | null = null;
  private readonly vehicles = new Map<string, { object: THREE.Group; type: VehicleType; cyclist: CyclistRig | null }>();
  private readonly people = new Map<string, { object: THREE.Group; mixer: THREE.AnimationMixer;
    feet: readonly [THREE.Object3D, THREE.Object3D]; footChain: readonly THREE.Object3D[];
    referenceFootY: number; walkDuration: number }>();
  private readonly lamps: { location: SignalLocation; object: THREE.Group; red: THREE.MeshStandardMaterial[]; yellow: THREE.MeshStandardMaterial[]; green: THREE.MeshStandardMaterial[] }[] = [];
  private readonly aircraft = new Map<string, THREE.Group>();
  private readonly trails = new Map<string, { line: THREE.Line; startFrame: number }>();
  private currentSecond = -1;
  private collisionBoxesVisible = false;
  private displayIds: ReturnType<typeof recordedDisplayIds> | null = null;
  private sidewalkHeight: ((x: number, z: number, sourceY: number) => number) | null = null;

  private constructor(data: TrafficData, templates: Readonly<Record<VehicleType, THREE.Group>>,
                      personTemplates: readonly { name: string; model: THREE.Group;
                        walkClip: THREE.AnimationClip }[],
                      lamp: THREE.Group,
                      flightData: FlightData, flightModels: Readonly<Record<AircraftId, THREE.Group>>,
                      cyclistPoses: CyclistPoseFrames, drawnSignals: CitySignalSelection) {
    this.data = data; this.vehicleTemplates = templates; this.personTemplates = personTemplates;
    this.flightData = flightData;
    this.cyclistPoses = cyclistPoses;
    this.bicycleDistances = recordedBicycleDistances(data.frames);
    this.pedestrianDistances = recordedPedestrianDistances(data.frames);
    this.group.name = "SUMO OSM engineering traffic preview";
    this.group.matrixAutoUpdate = false;
    // Departed and filtered records stay pooled as hidden children; their rigs wait until shown.
    skipHiddenDynamicChildren(this.group);
    this.vehicleLamps = vehicleLampMaterials(templates);
    for (const [type, template] of Object.entries(templates)) {
      if (type !== "bicycle") mergeVehicleTemplateMeshes(template);
    }
    this.shadowCasters = new TrafficShadowCasters(this.group, templates);
    this.group.userData.vehicleLampMaterialCount = this.vehicleLamps.length;
    for (let index = 0; index < 2; index++) {
      const light = new THREE.SpotLight(0xffe7c2, 0, 22, Math.PI / 7, 0.7, 2);
      const target = new THREE.Object3D();
      this.group.add(light, target);
      light.target = target;
      this.headlightBeams.push({ light, target });
    }
    for (const [id, spec] of Object.entries(aircraftAssets) as [AircraftId, typeof aircraftAssets[AircraftId]][]) {
      const aircraft = fitted(flightModels[id], spec.size, 0, true);
      const planned = flightData.source_kind === "planned-visual-flight";
      aircraft.name = `${spec.name} · ${planned ? "planned city demonstration" : "recorded PX4 path"} ${id}`;
      aircraft.userData.target = { kind: "entity", id };
      aircraft.userData.entityKind = "uav";
      aircraft.userData.visualAircraftType = spec.name;
      aircraft.userData.sourceDynamics = planned ? "Visual flight plan; no PX4 simulation" : "PX4/Gazebo engineering replay";
      aircraft.add(collisionBox(new THREE.Box3().setFromObject(aircraft).getSize(new THREE.Vector3()), 0xa27bff));
      // The fitted chain and its collision proxy are fixed relative to the craft; only the
      // aircraft root moves per tick. Fleet UAV models carry no skins or animation tracks.
      cacheStaticTransforms(aircraft, node => node === aircraft);
      this.group.add(aircraft);
      this.aircraft.set(id, aircraft);
      const startFrame = flightData.frames.findIndex(frame => frame.some(sample => sample[0] === id && sample[7]));
      if (startFrame < 0) throw new Error(`City preview aircraft never flies: ${id}`);
      const path = flightData.frames.slice(startFrame).map(frame => {
        const sample = frame.find(value => value[0] === id);
        if (sample === undefined) throw new Error(`City preview flight path is incomplete: ${id}`);
        return new THREE.Vector3(sample[1], Math.max(0, sample[3]) + 0.06, sample[2]);
      });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(path),
        new THREE.LineBasicMaterial({ color: spec.trailColor, transparent: true, opacity: 0.88 }));
      line.name = `${spec.name} 已飞轨迹`;
      line.userData.target = { kind: "entity", id };
      line.geometry.setDrawRange(0, 0);
      line.frustumCulled = false;
      this.group.add(line);
      this.trails.set(id, { line, startFrame });
    }
    const lampTemplate = trafficLampTemplate(lamp);
    for (const location of data.signals) {
      if (drawnSignals !== "all-source-signals" && !drawnSignals.has(location.id)) continue;
      const object = new THREE.Group();
      const fixture = lampTemplate.clone(true);
      object.add(fixture);
      object.position.set(location.x, 0, location.z);
      object.rotation.y = -THREE.MathUtils.degToRad(location.heading);
      object.userData.target = { kind: "traffic_signal", id: location.id };
      object.userData.signal = location;
      const red: THREE.MeshStandardMaterial[] = [], yellow: THREE.MeshStandardMaterial[] = [], green: THREE.MeshStandardMaterial[] = [];
      object.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return;
        for (const material of signalMaterial(node)) {
          if (material.name.endsWith("SG7")) red.push(material);
          if (material.name.endsWith("SG3")) yellow.push(material);
          if (material.name.endsWith("SG8")) green.push(material);
        }
      });
      if (red.length === 0 || yellow.length === 0 || green.length === 0) throw new Error("Provided traffic lamp lacks separate red, yellow, and green materials");
      object.add(collisionBox(new THREE.Vector3(0.55, SIGNAL_DISPLAY_HEIGHT_M, 0.55), 0xffc247));
      cacheStaticTransforms(object);
      object.visible = false;
      this.group.add(object);
      this.lamps.push({ location, object, red, yellow, green });
    }
  }

  static async load(trafficUrl: string, flightUrl: string,
    fixtureAssets: Pick<VerifiedVisualAssets, "url">, drawnSignals: CitySignalSelection): Promise<CityTrafficPreview> {
    if (!fixtureAssets || typeof fixtureAssets.url !== "function") {
      throw new Error("Signal fixture requires verified visual assets");
    }
    const response = await fetch(trafficUrl);
    if (!response.ok) throw new Error(`SUMO city preview failed: ${response.status}`);
    const data = parseCityTrafficData(await response.json());
    const signalPath = "/models/incoming/furniture/glb/traffic_light_4.glb";
    const signalUrl = await fixtureAssets.url(signalPath);
    const flightResponse = await fetch(flightUrl);
    if (!flightResponse.ok) throw new Error(`Recorded PX4 city preview failed: ${flightResponse.status}`);
    const flightData = parseCityFlightData(await flightResponse.json(), data.mesh_pack_source_sha256);
    const [rows, people, lamp, flightModels] = await Promise.all([
      Promise.all((Object.entries(vehicleAssets) as [VehicleType, typeof vehicleAssets[VehicleType]][]).map(async ([type, spec]) => {
        const loaded = spec.aliases ? await loadFbx(spec.path, spec.aliases) : (await gltfLoader.loadAsync(spec.path)).scene;
        return [type, prepareCityVehicleTemplate(loaded, type)] as const;
      })),
      Promise.all(pedestrianAssets.map(async (asset, index) => {
        if (index === 0) {
          const loaded = await gltfLoader.loadAsync(asset.path);
          return { name: asset.name, model: loaded.scene, animations: loaded.animations };
        }
        return { name: asset.name,
          model: prepareCitizenFbx(
            await loadFbx(asset.path, "/models/incoming/citizens/texture-aliases.json"), asset.name),
          animations: [] as THREE.AnimationClip[] };
      })),
      gltfLoader.loadAsync(signalUrl),
      Promise.all((Object.entries(aircraftAssets) as [AircraftId, typeof aircraftAssets[AircraftId]][])
        .map(async ([id, spec]) => [id, (await gltfLoader.loadAsync(spec.path)).scene] as const)),
    ]);
    const templates = Object.fromEntries(rows) as Record<VehicleType, THREE.Group>;
    const person = people[0]!;
    const walk = person.animations.find(clip => clip.name === "walk");
    if (walk === undefined) throw new Error("Provided pedestrian has no walking clip");
    const invalidRootTracks = new Set([
      "Bip01.position", "Bip01.quaternion", "Bip01.scale",
      "Bip01_Footsteps.position", "Bip01_Footsteps.quaternion", "Bip01_Footsteps.scale",
    ]);
    if (walk.tracks.filter(track => invalidRootTracks.has(track.name)).length !== invalidRootTracks.size) {
      throw new Error("Provided walking clip root tracks changed; orientation repair must be rechecked");
    }
    // The source FBX clip stores its two root transforms in converted Y-up coordinates while
    // its bind skeleton still uses Z-up. Keep the limb tracks on the verified upright bind pose.
    const repairedWalk = new THREE.AnimationClip("walk-upright", walk.duration,
      walk.tracks.filter(track => !invalidRootTracks.has(track.name)));
    const cyclist = createCyclistTemplate(templates.bicycle, person.model, repairedWalk);
    templates.bicycle = cyclist.model;
    // Motor templates have no animated descendants, so their fitted chains freeze now and
    // every spawned vehicle clone inherits them. In the bicycle template, animateCyclist writes
    // only the wheel, crank and leg quaternions each tick; the rest of the rig freezes too.
    for (const [type, template] of Object.entries(templates) as [VehicleType, THREE.Group][]) {
      if (type === "bicycle") cacheCyclistStaticTransforms(template);
      else cacheStaticTransforms(template, node => node === template);
    }
    for (const citizen of people) {
      const mesh = new THREE.Box3().setFromObject(citizen.model);
      if (mesh.isEmpty() || citizen.model.getObjectByName("Bip01_L_Foot") === undefined
          || citizen.model.getObjectByName("Bip01_R_Foot") === undefined) {
        throw new Error(`Provided pedestrian ${citizen.name} has no compatible skinned walking rig`);
      }
      // Clones copy the template's skinned bounds; without a template sphere, every new
      // pedestrian skins all of its vertices on the CPU the first time it is frustum-tested.
      citizen.model.traverse(node => {
        if (node instanceof THREE.SkinnedMesh) node.computeBoundingSphere();
      });
    }
    const pedestrianTemplates = people.map((citizen, index) => ({
      name: citizen.name, model: citizen.model,
      walkClip: index === 0 ? repairedWalk : retargetCitizenWalk(person.model, citizen.model, repairedWalk),
    }));
    return new CityTrafficPreview(data, templates, pedestrianTemplates,
                                  lamp.scene, flightData, Object.fromEntries(flightModels) as Record<AircraftId, THREE.Group>,
                                  cyclist.poses, drawnSignals);
  }

  /** Upload and compile all supplied citizen/vehicle variants before playback begins. */
  async prepareRenderer(renderer: THREE.WebGLRenderer, camera: THREE.Camera, scene: THREE.Scene): Promise<void> {
    this.shadowCasters.prepareRenderer(renderer);
    const templates = new THREE.Group();
    templates.add(...Object.values(this.vehicleTemplates), ...this.personTemplates.map(person => person.model));
    try {
      const textures = new Set<THREE.Texture>();
      for (const root of [scene, templates]) root.traverse(node => {
        if (!(node instanceof THREE.Mesh || node instanceof THREE.Line || node instanceof THREE.Sprite)) return;
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
          for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
          if (material instanceof THREE.ShaderMaterial) {
            for (const uniform of Object.values(material.uniforms)) {
              if (uniform.value instanceof THREE.Texture) textures.add(uniform.value);
            }
          }
        }
      });
      for (const texture of [scene.background, scene.environment]) {
        if (texture instanceof THREE.Texture && !texture.isRenderTargetTexture) textures.add(texture);
      }
      await runInFrameSlices(textures, texture => renderer.initTexture(texture));
      await renderer.compileAsync(scene, camera);
      await renderer.compileAsync(templates, camera, scene);
      await waitForGpuCommands(renderer);
      this.shadowCasters.warmShadowPrograms(renderer, camera, scene);
      // Each program reads its uniform and attribute tables on first use; read them before the first frame.
      await runInFrameSlices([...renderer.info.programs ?? []], program => {
        program.getUniforms();
        program.getAttributes();
      });
    } finally {
      templates.clear();
    }
  }

  visibleCounts(): { vehicles: number; bicycles: number; pedestrians: number; aircraft: number } {
    let vehicles = 0, bicycles = 0, pedestrians = 0, aircraft = 0;
    for (const record of this.vehicles.values()) if (record.object.visible) {
      if (record.type === "bicycle") bicycles++;
      else vehicles++;
    }
    for (const record of this.people.values()) if (record.object.visible) pedestrians++;
    for (const object of this.aircraft.values()) if (object.visible) aircraft++;
    return { vehicles, bicycles, pedestrians, aircraft };
  }

  entityPosition(id: string): THREE.Vector3 | null {
    const object = this.aircraft.get(id) ?? this.vehicles.get(id)?.object ?? this.people.get(id)?.object;
    return object?.visible ? object.position : null;
  }

  aircraftLabel(id: string): string {
    const aircraft = this.aircraft.get(id);
    if (aircraft === undefined) throw new Error(`City flight has no visual UAV: ${id}`);
    return `${aircraft.userData.visualAircraftType as string} · ${id}`
      + (this.flightData.source_kind === "planned-visual-flight" ? " · 规划航线" : " · PX4 记录");
  }

  setCollisionBoxesVisible(visible: boolean): void {
    this.collisionBoxesVisible = visible;
    setCollisionBoxesVisible(this.group, visible);
  }

  setSidewalkHeightSampler(sample: (x: number, z: number, sourceY: number) => number): void {
    this.sidewalkHeight = sample;
  }

  /** Filters existing SUMO records; it never synthesizes traffic or changes recorded motion. */
  setDisplayLimits(limits: CityTrafficDisplayLimits): void {
    this.displayIds = recordedDisplayIds(this.data.frames, limits);
    for (const [id, record] of this.vehicles) {
      if (!(record.type === "bicycle" ? this.displayIds.bicycles : this.displayIds.vehicles).has(id)) record.object.visible = false;
    }
    for (const [id, record] of this.people) if (!this.displayIds.pedestrians.has(id)) record.object.visible = false;
  }

  /** High detail source signal meshes are drawn only where their lenses can be seen. */
  setSignalVisibility(camera: THREE.Camera, roadsVisible: boolean): number {
    let count = 0;
    for (const lamp of this.lamps) {
      const distanceSquared = (lamp.location.x - camera.position.x) ** 2
        + (lamp.location.z - camera.position.z) ** 2
        + camera.position.y ** 2;
      lamp.object.visible = !signalFixtureIsOmitted(lamp.object) && roadsVisible && distanceSquared < 120 ** 2;
      if (lamp.object.visible) count++;
    }
    return count;
  }

  /** Two nearby moving vehicles cast road light; every vehicle keeps its source lamp faces lit. */
  setVehicleLighting(camera: THREE.Camera, timeOfDay: CityTimeOfDay): number {
    const lighting = CITY_SUBSYSTEM_LIGHTING[timeOfDay];
    if (this.vehicleLightingTimeOfDay !== timeOfDay) {
      for (const { material, rear } of this.vehicleLamps) {
        material.emissiveIntensity = rear
          ? lighting.vehicleRearEmissiveIntensity : lighting.vehicleFrontEmissiveIntensity;
      }
      this.vehicleLightingTimeOfDay = timeOfDay;
    }
    const nearby = timeOfDay !== "day" ? [...this.vehicles.values()]
      .filter(record => record.object.visible && record.type !== "bicycle")
      .map(record => ({ record, distance: (record.object.position.x - camera.position.x) ** 2
        + (record.object.position.z - camera.position.z) ** 2 }))
      .filter(item => item.distance < 65 ** 2)
      .sort((left, right) => left.distance - right.distance)
      .slice(0, this.headlightBeams.length) : [];
    for (const [index, { light, target }] of this.headlightBeams.entries()) {
      const item = nearby[index];
      // Keep the shader's light count constant while vehicles enter/leave range.
      if (item === undefined) { light.intensity = 0; continue; }
      const object = item.record.object;
      const size = vehicleAssets[item.record.type].size;
      const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(object.quaternion);
      light.position.copy(object.position).add(new THREE.Vector3(0, Math.min(0.95, size.y * 0.43), 0))
        .addScaledVector(forward, size.z / 2 - 0.08);
      target.position.copy(light.position).addScaledVector(forward, 16);
      target.position.y = 0.1;
      light.intensity = lighting.vehicleHeadlightIntensity;
    }
    return nearby.length;
  }

  update(seconds: number, visibleVehicles: boolean, visiblePeople: boolean, visibleRoads: boolean,
         visibleUav: boolean, visibleTrails: boolean): void {
    const wrapped = ((seconds % this.data.duration_seconds) + this.data.duration_seconds) % this.data.duration_seconds;
    const frameIndex = Math.min(Math.floor(wrapped / this.data.step_seconds), this.data.frames.length - 2);
    const frame = this.data.frames[frameIndex]!;
    const next = this.data.frames[frameIndex + 1]!;
    const fraction = (wrapped - frameIndex * this.data.step_seconds) / this.data.step_seconds;
    const nextVehicles = new Map(next.vehicles.map(sample => [sample[0], sample]));
    const nextPeople = new Map(next.persons.map(sample => [sample[0], sample]));
    const activeVehicles = new Set<string>();
    for (const sample of frame.vehicles) {
      if (this.displayIds !== null && !(sample[4] === "bicycle"
        ? this.displayIds.bicycles : this.displayIds.vehicles).has(sample[0])) continue;
      activeVehicles.add(sample[0]);
      let record = this.vehicles.get(sample[0]);
      if (record === undefined) {
        const object = sample[4] === "bicycle" ? cloneSkeleton(this.vehicleTemplates.bicycle) as THREE.Group
          : this.vehicleTemplates[sample[4]].clone(true);
        if (sample[4] !== "bicycle") this.shadowCasters.add(this.vehicleTemplates[sample[4]], object);
        const proxy = collisionBox(vehicleAssets[sample[4]].collisionSize ?? vehicleAssets[sample[4]].size,
                                   0xf3ae47);
        proxy.visible = this.collisionBoxesVisible;
        object.add(proxy);
        object.userData.target = { kind: "entity", id: sample[0] };
        object.userData.entityKind = "ugv";
        this.group.add(object);
        record = { object, type: sample[4], cyclist: sample[4] === "bicycle" ? bindCyclist(object) : null };
        this.vehicles.set(sample[0], record);
      }
      const after = nextVehicles.get(sample[0]) ?? sample;
      record.object.position.set(THREE.MathUtils.lerp(sample[1], after[1], fraction),
                                 THREE.MathUtils.lerp(sample[5] ?? 0.1, after[5] ?? 0.1, fraction) + 0.01,
                                 THREE.MathUtils.lerp(sample[2], after[2], fraction));
      // The supplied bicycle and its rider face local +Z; SUMO headings use local -Z.
      record.object.rotation.y = -THREE.MathUtils.degToRad(headingBetween(sample[3], after[3], fraction))
        + (record.type === "bicycle" ? Math.PI : 0);
      if (record.cyclist !== null) {
        const travelled = this.bicycleDistances.get(sample[0]);
        if (travelled === undefined) throw new Error(`Recorded bicycle has no travelled distance: ${sample[0]}`);
        animateCyclist(record.cyclist, this.cyclistPoses,
          THREE.MathUtils.lerp(travelled[frameIndex]!, travelled[frameIndex + 1]!, fraction));
      }
      record.object.visible = visibleVehicles;
    }
    for (const [id, record] of this.vehicles) if (!activeVehicles.has(id)) record.object.visible = false;

    const activePeople = new Set<string>();
    for (const sample of frame.persons) {
      if (this.displayIds !== null && !this.displayIds.pedestrians.has(sample[0])) continue;
      activePeople.add(sample[0]);
      let record = this.people.get(sample[0]);
      if (record === undefined) {
        const citizen = this.personTemplates[hashId(sample[0]) % this.personTemplates.length]!;
        const object = fitCityCharacter(cloneSkeleton(citizen.model), 1.72);
        const proxy = collisionBox(new THREE.Vector3(0.6, 1.72, 0.42), 0x6deb8a);
        proxy.visible = this.collisionBoxesVisible;
        object.add(proxy);
        object.userData.target = { kind: "entity", id: sample[0] };
        object.userData.entityKind = "pedestrian";
        object.userData.visualAsset = citizen.name;
        const mixer = new THREE.AnimationMixer(object);
        mixer.clipAction(citizen.walkClip).play();
        mixer.setTime(0);
        const leftFoot = object.getObjectByName("Bip01_L_Foot");
        const rightFoot = object.getObjectByName("Bip01_R_Foot");
        if (!(leftFoot instanceof THREE.Bone) || !(rightFoot instanceof THREE.Bone)) {
          throw new Error("Provided pedestrian walking rig has no left and right foot bones");
        }
        object.updateMatrixWorld(true);
        const referenceFootY = Math.min(leftFoot.matrixWorld.elements[13]!, rightFoot.matrixWorld.elements[13]!);
        this.group.add(object);
        record = { object, mixer, feet: [leftFoot, rightFoot], footChain: footChain(object, [leftFoot, rightFoot]),
          referenceFootY, walkDuration: citizen.walkClip.duration };
        this.people.set(sample[0], record);
      }
      const after = nextPeople.get(sample[0]) ?? sample;
      record.object.position.set(THREE.MathUtils.lerp(sample[1], after[1], fraction),
                                 THREE.MathUtils.lerp(sample[4] ?? 0.1, after[4] ?? 0.1, fraction) + 0.01,
                                 THREE.MathUtils.lerp(sample[2], after[2], fraction));
      if (this.sidewalkHeight !== null) {
        const position = record.object.position;
        position.y = this.sidewalkHeight(position.x, position.z, position.y - .01) + .01;
      }
      record.object.rotation.y = pedestrianYaw(headingBetween(sample[3], after[3], fraction));
      record.object.visible = visiblePeople;
      const travelled = this.pedestrianDistances.get(sample[0]);
      if (travelled === undefined) throw new Error(`Recorded pedestrian has no travelled distance: ${sample[0]}`);
      const distance = THREE.MathUtils.lerp(travelled[frameIndex]!, travelled[frameIndex + 1]!, fraction);
      const phase = hashId(sample[0]) / 0xffffffff;
      record.mixer.setTime((distance / PEDESTRIAN_METRES_PER_WALK_CYCLE + phase) * record.walkDuration);
      // Only the feet are read here; the render's scene update moves the rest of the rig once.
      updateChainWorld(record.footChain);
      const lowestFootY = Math.min(record.feet[0].matrixWorld.elements[13]!, record.feet[1].matrixWorld.elements[13]!);
      record.object.position.y += record.referenceFootY - (lowestFootY - record.object.position.y);
    }
    for (const [id, record] of this.people) if (!activePeople.has(id)) record.object.visible = false;

    if (frameIndex !== this.currentSecond) {
      this.currentSecond = frameIndex;
      for (const lamp of this.lamps) {
        const state = frame.tls[lamp.location.tls]?.[lamp.location.link]?.toLowerCase() ?? "r";
        const red = state === "r" || state === "s";
        const yellow = state === "y" || state === "u";
        const green = state === "g";
        for (const material of lamp.red) material.emissiveIntensity = red ? 3.5 : 0.02;
        for (const material of lamp.yellow) material.emissiveIntensity = yellow ? 3.5 : 0.02;
        for (const material of lamp.green) material.emissiveIntensity = green ? 3.5 : 0.02;
        lamp.object.userData.signalState = state;
      }
    }
    for (const lamp of this.lamps) lamp.object.visible = visibleRoads && lamp.object.visible;
    const flightWrapped = ((seconds % this.flightData.duration_seconds) + this.flightData.duration_seconds)
      % this.flightData.duration_seconds;
    const flightIndex = Math.min(Math.floor(flightWrapped / this.flightData.step_seconds),
      this.flightData.frames.length - 2);
    const flightFraction = (flightWrapped - flightIndex * this.flightData.step_seconds)
      / this.flightData.step_seconds;
    const flightFrame = this.flightData.frames[flightIndex]!;
    const flightNext = new Map(this.flightData.frames[flightIndex + 1]!.map(sample => [sample[0], sample]));
    for (const sample of flightFrame) {
      const object = this.aircraft.get(sample[0]);
      if (object === undefined) throw new Error(`City preview aircraft is missing: ${sample[0]}`);
      const after = flightNext.get(sample[0]);
      if (after === undefined) throw new Error(`City preview flight path is incomplete: ${sample[0]}`);
      object.position.set(THREE.MathUtils.lerp(sample[1], after[1], flightFraction),
                          Math.max(0, THREE.MathUtils.lerp(sample[3], after[3], flightFraction)) + 0.01,
                          THREE.MathUtils.lerp(sample[2], after[2], flightFraction));
      object.rotation.set(THREE.MathUtils.lerp(sample[5], after[5], flightFraction),
                          -angleBetween(sample[4], after[4], flightFraction),
                          THREE.MathUtils.lerp(sample[6], after[6], flightFraction), "YXZ");
      object.visible = visibleUav;
      const trail = this.trails.get(sample[0]);
      if (trail === undefined) throw new Error(`City preview flight trail is missing: ${sample[0]}`);
      const visiblePoints = Math.max(0, flightIndex - trail.startFrame + 1);
      trail.line.geometry.setDrawRange(0, visiblePoints);
      trail.line.visible = visibleUav && visibleTrails && visiblePoints >= 2;
    }
  }

  dispose(): void { this.shadowCasters.dispose(); disposePresentation(this.group); }
}

export function disposePresentation(group: THREE.Group): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  group.traverse(node => {
    if (node instanceof THREE.Sprite) {
      materials.add(node.material);
      if (node.material.map !== null) textures.add(node.material.map);
      return;
    }
    if (!(node instanceof THREE.Mesh || node instanceof THREE.Line)) return;
    geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      materials.add(material);
      // Environment maps are borrowed from the map lifetime sky/HDR/probe
      // owner. Releasing one presentation must not destroy another scene's IBL.
      for (const [slot, value] of Object.entries(material)) {
        if (slot !== "envMap" && value instanceof THREE.Texture) textures.add(value);
      }
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
  for (const texture of textures) texture.dispose();
  group.clear();
}
