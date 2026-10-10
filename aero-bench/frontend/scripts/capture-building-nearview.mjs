/**
 * Matched near/mid/far building-quality capture for the 414-building scene.
 *
 * The controlled baseline run and candidate run use the same current application
 * origin. Pass one camera plan to both and the baseline report to the candidate so
 * camera coordinates, projection, viewport, sources and renderer identity stay
 * fixed. The script also compares retained canonical research GLBs with the served
 * derived GLBs for the two captured subjects.
 *
 * This is an acceptance harness, not an alternate renderer. It uses the production
 * building streamer, material setup and single local-reflection owner. A capture is
 * never marked accepted automatically; matched-frame review remains required.
 *
 * Usage:
 *   node scripts/capture-building-nearview.mjs <origin> <output-dir> [label]
 *     [--camera-plan <camera-plan.json>]
 *     [--matched-report <controlled-baseline-capture-report.json>]
 *     [--source-assets <canonical-glb-directory>]
 *     [--controlled-baseline-commit <40-character-commit>]
 *     [--controlled-baseline-manifest <sha256>:<bytes>]
 *     [--allow-local-self-signed]
 *     [--allow-software]
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { get as httpsGet } from "node:https";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

export const BUILDING_CAPTURE_PLAN_SCHEMA =
  "aero-bench.building-quality-camera-plan/v2";
export const BUILDING_CAPTURE_REPORT_SCHEMA =
  "aero-bench.building-quality-capture-report/v3";
export const BUILDING_SCENE_PATH =
  "/city-presentation/building-render-scene-v1.json";
export const BUILDING_SCENE_QUERY =
  "?scene=1&city=%2Fcity-presentation%2Fbuilding-render-scene-v1.json";
export const CAPTURE_VIEWPORT = Object.freeze({ width: 1600, height: 900 });
export const CAPTURE_MOODS = Object.freeze(["day", "dusk"]);
export const CAPTURE_CAMERA_IDS = Object.freeze([
  "material-near",
  "material-mid",
  "material-far",
  "roof-near",
  "roof-mid",
  "roof-far",
]);
export const EXPECTED_REFLECTION_PROFILE = Object.freeze({
  probeSizePx: 256,
  reflectiveRadiusM: 75,
  captureFarM: 140,
  maxReflectiveBuildings: 12,
  cubeFacesPerRefresh: 6,
});
export const HARDWARE_BROWSER_ARGS = Object.freeze([
  "--enable-gpu",
  "--use-angle=vulkan",
  "--enable-features=Vulkan",
  "--disable-vulkan-surface",
  "--disable-software-rasterizer",
  "--ignore-gpu-blocklist",
  "--disable-background-timer-throttling",
  "--disable-renderer-backgrounding",
  "--no-proxy-server",
  "--proxy-bypass-list=*",
]);

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = resolve(SCRIPT_DIR, "../..");
const DEFAULT_SOURCE_ASSETS = resolve(
  SCRIPT_DIR,
  "../../validation/building-render-scaleout-mimo-20260929/assets",
);
const DIGEST = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const LABEL = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const ENVELOPE_TOLERANCE_M = 1e-3;
const ROAD_BINDING_KINDS = Object.freeze([
  "road",
  "effective_fixtures",
  "traffic",
  "flight",
]);

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function round(value) {
  return Number(value.toFixed(6));
}

function finiteNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be finite`);
  }
  return value;
}

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function array(value, label) {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function string(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function parseFileIdentity(value, label) {
  const match = /^([0-9a-f]{64}):([1-9][0-9]*)$/.exec(value);
  if (match === null || !Number.isSafeInteger(Number(match[2]))) {
    throw new Error(`${label} must be <sha256>:<positive byte count>`);
  }
  return { sha256: match[1], bytes: Number(match[2]) };
}

export function parseCaptureArguments(argv) {
  const positional = [];
  let cameraPlanPath = null;
  let matchedReportPath = null;
  let sourceAssets = DEFAULT_SOURCE_ASSETS;
  let allowSoftware = false;
  let allowLocalSelfSigned = false;
  let baselineCommit = null;
  let baselineManifest = null;
  for (let index = 0; index < argv.length; index++) {
    const value = argv[index];
    if (
      value === "--camera-plan" ||
      value === "--matched-report" ||
      value === "--source-assets" ||
      value === "--controlled-baseline-commit" ||
      value === "--controlled-baseline-manifest"
    ) {
      const next = argv[++index];
      if (next === undefined || next.startsWith("--")) {
        throw new Error(`${value} requires a path`);
      }
      if (value === "--camera-plan") cameraPlanPath = resolve(next);
      else if (value === "--matched-report") matchedReportPath = resolve(next);
      else if (value === "--source-assets") sourceAssets = resolve(next);
      else if (value === "--controlled-baseline-commit") {
        if (!COMMIT.test(next)) {
          throw new Error(
            "Controlled baseline commit must be a full lowercase commit id",
          );
        }
        baselineCommit = next;
      } else {
        baselineManifest = parseFileIdentity(
          next,
          "Controlled baseline manifest",
        );
      }
    } else if (value === "--allow-software") {
      allowSoftware = true;
    } else if (value === "--allow-local-self-signed") {
      allowLocalSelfSigned = true;
    } else if (value.startsWith("--")) {
      throw new Error(`Unknown option: ${value}`);
    } else {
      positional.push(value);
    }
  }
  if (positional.length < 2 || positional.length > 3) {
    throw new Error(
      "Expected <origin> <output-dir> [label]; output directory is required",
    );
  }
  const originUrl = new URL(positional[0]);
  if (
    !["http:", "https:"].includes(originUrl.protocol) ||
    originUrl.username !== "" ||
    originUrl.password !== "" ||
    originUrl.pathname !== "/" ||
    originUrl.search !== "" ||
    originUrl.hash !== ""
  ) {
    throw new Error(
      "Origin must be an HTTP(S) origin without credentials, path, query, or fragment",
    );
  }
  if (positional[1].length === 0) {
    throw new Error("Output directory must not be empty");
  }
  if (
    allowLocalSelfSigned &&
    (originUrl.protocol !== "https:" ||
      !["127.0.0.1", "[::1]", "localhost"].includes(originUrl.hostname))
  ) {
    throw new Error(
      "Self-signed certificate bypass is restricted to a loopback HTTPS origin",
    );
  }
  if ((baselineCommit === null) !== (baselineManifest === null)) {
    throw new Error(
      "Controlled baseline commit and manifest identity must be supplied together",
    );
  }
  if (baselineCommit !== null && matchedReportPath !== null) {
    throw new Error(
      "A controlled baseline capture cannot also consume a matched report",
    );
  }
  const label = positional[2] ?? "capture";
  if (!LABEL.test(label)) throw new Error("Capture label is not filename-safe");
  return {
    origin: originUrl.origin,
    output: resolve(positional[1]),
    label,
    cameraPlanPath,
    matchedReportPath,
    sourceAssets,
    controlledBaseline:
      baselineCommit === null
        ? null
        : { commit: baselineCommit, manifest: baselineManifest },
    allowSoftware,
    allowLocalSelfSigned,
  };
}

/** Expose the production map instance without changing the served build. Exactly
 * one constructor assignment must match; drift fails before any screenshot. */
export function instrumentAppBundle(source) {
  const pattern =
    /this\.map\s*=\s*new\s+[\w$]+\(\s*this\.shell\.map\.querySelector\(\s*([`"'])#city-map\1\s*\)\s*,\s*\{/g;
  const matches = [...source.matchAll(pattern)];
  if (matches.length !== 1) {
    throw new Error(
      `Expected one production CityMap constructor, found ${matches.length}`,
    );
  }
  const match = matches[0][0];
  const replacement = match.replace(
    /this\.map\s*=\s*/,
    "window.__aeroBuildingCaptureMap=this.map=",
  );
  const instrumented = source.replace(match, replacement);
  return {
    source: instrumented,
    matchCount: 1,
    sourceSha256: sha256(Buffer.from(source)),
    instrumentedSha256: sha256(Buffer.from(instrumented)),
  };
}

function worldBounds(entry) {
  const envelope = object(entry.envelope, `${entry.object_id}.envelope`);
  return {
    minX: finiteNumber(envelope.min_e, "envelope.min_e"),
    maxX: finiteNumber(envelope.max_e, "envelope.max_e"),
    minZ: -finiteNumber(envelope.max_n, "envelope.max_n"),
    maxZ: -finiteNumber(envelope.min_n, "envelope.min_n"),
    minY: finiteNumber(envelope.base_up, "envelope.base_up"),
    maxY: finiteNumber(envelope.top_up, "envelope.top_up"),
  };
}

function segmentIntersectsBox(start, end, box, padding = 0) {
  let low = 0;
  let high = 1;
  for (const [axis, minimum, maximum] of [
    [0, box.minX - padding, box.maxX + padding],
    [1, box.minY - padding, box.maxY + padding],
    [2, box.minZ - padding, box.maxZ + padding],
  ]) {
    const delta = end[axis] - start[axis];
    if (Math.abs(delta) < 1e-12) {
      if (start[axis] < minimum || start[axis] > maximum) return false;
      continue;
    }
    let enter = (minimum - start[axis]) / delta;
    let exit = (maximum - start[axis]) / delta;
    if (enter > exit) [enter, exit] = [exit, enter];
    low = Math.max(low, enter);
    high = Math.min(high, exit);
    if (low > high) return false;
  }
  return high >= 0.08 && low <= 1;
}

function pointBoxDistance(point, box) {
  const dx = Math.max(box.minX - point[0], 0, point[0] - box.maxX);
  const dy = Math.max(box.minY - point[1], 0, point[1] - box.maxY);
  const dz = Math.max(box.minZ - point[2], 0, point[2] - box.maxZ);
  return Math.hypot(dx, dy, dz);
}

/** Select one source-only angle shared by all three distance bands. Camera
 * endpoints inside another declared building envelope are disallowed because
 * the production reflection recenter path rejects those positions. */
function viewDirection(subject, buildings, poses) {
  const box = worldBounds(subject);
  const center = [(box.minX + box.maxX) / 2, (box.minZ + box.maxZ) / 2];
  const directions = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
    [Math.SQRT1_2, Math.SQRT1_2],
    [-Math.SQRT1_2, Math.SQRT1_2],
    [Math.SQRT1_2, -Math.SQRT1_2],
    [-Math.SQRT1_2, -Math.SQRT1_2],
  ];
  const others = buildings.filter(
    (entry) => entry.object_id !== subject.object_id,
  );
  const ranked = directions
    .map((direction, index) => {
      const support =
        (Math.abs(direction[0]) * (box.maxX - box.minX)) / 2 +
        (Math.abs(direction[1]) * (box.maxZ - box.minZ)) / 2;
      let occupiedEndpoints = 0;
      let blockedSegments = 0;
      let cameraClearance = Number.POSITIVE_INFINITY;
      for (const pose of poses) {
        const start = [center[0], pose.targetY, center[1]];
        const end = [
          center[0] + direction[0] * (support + pose.gap),
          pose.cameraY,
          center[1] + direction[1] * (support + pose.gap),
        ];
        for (const entry of others) {
          const candidate = worldBounds(entry);
          const clearance = pointBoxDistance(end, candidate);
          if (clearance === 0) occupiedEndpoints++;
          cameraClearance = Math.min(cameraClearance, clearance);
          if (segmentIntersectsBox(start, end, candidate)) blockedSegments++;
        }
      }
      return {
        direction,
        index,
        occupiedEndpoints,
        blockedSegments,
        cameraClearance,
      };
    })
    .sort(
      (left, right) =>
        left.occupiedEndpoints - right.occupiedEndpoints ||
        left.blockedSegments - right.blockedSegments ||
        right.cameraClearance - left.cameraClearance ||
        left.index - right.index,
    );
  if (ranked[0].occupiedEndpoints !== 0) {
    throw new Error(
      `${subject.object_id} has no envelope-clear C3 camera direction`,
    );
  }
  return ranked[0].direction;
}

function cameraEntry(
  subject,
  focus,
  band,
  direction,
  gap,
  cameraY,
  targetY,
  fov,
) {
  const box = worldBounds(subject);
  const centerX = (box.minX + box.maxX) / 2;
  const centerZ = (box.minZ + box.maxZ) / 2;
  const halfX = (box.maxX - box.minX) / 2;
  const halfZ = (box.maxZ - box.minZ) / 2;
  const support =
    Math.abs(direction[0]) * halfX + Math.abs(direction[1]) * halfZ;
  return {
    id: `${focus}-${band}`,
    focus,
    distance_band: band,
    subject_object_id: subject.object_id,
    subject_source_sha256: subject.glb.sha256,
    position: [
      round(centerX + direction[0] * (support + gap)),
      round(cameraY),
      round(centerZ + direction[1] * (support + gap)),
    ],
    target: [round(centerX), round(targetY), round(centerZ)],
    up: [0, 1, 0],
    fov_deg: fov,
    near_m: 0.1,
    far_m: 4000,
  };
}

export function selectBuildingCaptureSubjects(manifest) {
  const buildings = array(manifest.buildings, "manifest.buildings");
  if (buildings.length !== 414) {
    throw new Error(`C3 expects 414 buildings, found ${buildings.length}`);
  }
  const facade = buildings
    .filter((entry) => {
      const bounds = worldBounds(entry);
      const width = bounds.maxX - bounds.minX;
      const depth = bounds.maxZ - bounds.minZ;
      return Math.max(width, depth) <= 60 && Math.min(width, depth) >= 12;
    })
    .sort(
      (left, right) =>
        Math.abs(left.height_m - 32) - Math.abs(right.height_m - 32) ||
        left.object_id.localeCompare(right.object_id),
    )[0];
  const roof = [...buildings].sort(
    (left, right) =>
      right.height_m - left.height_m ||
      left.object_id.localeCompare(right.object_id),
  )[0];
  if (facade === undefined || roof === undefined) {
    throw new Error(
      "C3 cannot select facade and roof subjects from the manifest",
    );
  }
  return { facade, roof };
}

export function buildFrozenCameraPlan(manifest, manifestSha256) {
  if (!DIGEST.test(manifestSha256))
    throw new Error("Manifest digest is invalid");
  const buildings = array(manifest.buildings, "manifest.buildings");
  const { facade, roof } = selectBuildingCaptureSubjects(manifest);
  const facadeBox = worldBounds(facade);
  const facadeHeight = facadeBox.maxY - facadeBox.minY;
  const facadePoses = [
    {
      band: "near",
      gap: Math.max(10, Math.min(18, facadeHeight * 0.45)),
      cameraY: facadeBox.minY + facadeHeight * 0.38,
      targetY: facadeBox.minY + facadeHeight * 0.48,
      fov: 42,
    },
    {
      band: "mid",
      gap: Math.max(35, facadeHeight * 1.25),
      cameraY: facadeBox.minY + facadeHeight * 0.42,
      targetY: facadeBox.minY + facadeHeight * 0.48,
      fov: 45,
    },
    {
      band: "far",
      gap: Math.max(100, facadeHeight * 3.5),
      cameraY: facadeBox.minY + facadeHeight * 0.65,
      targetY: facadeBox.minY + facadeHeight * 0.48,
      fov: 48,
    },
  ];
  const facadeDirection = viewDirection(facade, buildings, facadePoses);
  const roofBox = worldBounds(roof);
  const roofHeight = roofBox.maxY - roofBox.minY;
  const roofPoses = [
    {
      band: "near",
      gap: 20,
      cameraY: roofBox.maxY + Math.max(18, roofHeight * 0.2),
      targetY: roofBox.maxY - Math.min(2, roofHeight * 0.03),
      fov: 42,
    },
    {
      band: "mid",
      gap: 55,
      cameraY: roofBox.maxY + Math.max(45, roofHeight * 0.4),
      targetY: roofBox.maxY - Math.min(5, roofHeight * 0.06),
      fov: 45,
    },
    {
      band: "far",
      gap: 140,
      cameraY: roofBox.maxY + Math.max(110, roofHeight * 0.9),
      targetY: roofBox.maxY - Math.min(10, roofHeight * 0.1),
      fov: 48,
    },
  ];
  const roofDirection = viewDirection(roof, buildings, roofPoses);
  const cameras = [
    ...facadePoses.map((pose) =>
      cameraEntry(
        facade,
        "material",
        pose.band,
        facadeDirection,
        pose.gap,
        pose.cameraY,
        pose.targetY,
        pose.fov,
      ),
    ),
    ...roofPoses.map((pose) =>
      cameraEntry(
        roof,
        "roof",
        pose.band,
        roofDirection,
        pose.gap,
        pose.cameraY,
        pose.targetY,
        pose.fov,
      ),
    ),
  ];
  return {
    schema: BUILDING_CAPTURE_PLAN_SCHEMA,
    scene_id: string(manifest.scene?.id, "manifest.scene.id"),
    reference_manifest_sha256: manifestSha256,
    objects_json_sha256: string(
      manifest.scene?.objects_json_sha256,
      "manifest.scene.objects_json_sha256",
    ),
    viewport: { ...CAPTURE_VIEWPORT, device_scale_factor: 1 },
    moods: [...CAPTURE_MOODS],
    cameras,
  };
}

export function validateFrozenCameraPlan(plan, manifest, manifestSha256) {
  const parsed = object(plan, "camera plan");
  if (parsed.schema !== BUILDING_CAPTURE_PLAN_SCHEMA) {
    throw new Error("Camera plan schema is unsupported");
  }
  if (!DIGEST.test(manifestSha256)) {
    throw new Error("Active building manifest digest is invalid");
  }
  if (
    !DIGEST.test(parsed.reference_manifest_sha256) ||
    parsed.scene_id !== manifest.scene?.id ||
    parsed.objects_json_sha256 !== manifest.scene?.objects_json_sha256
  ) {
    throw new Error(
      "Camera plan is bound to different source building geometry",
    );
  }
  const viewport = object(parsed.viewport, "camera plan viewport");
  if (
    viewport.width !== CAPTURE_VIEWPORT.width ||
    viewport.height !== CAPTURE_VIEWPORT.height ||
    viewport.device_scale_factor !== 1
  ) {
    throw new Error("Camera plan viewport is not the fixed C3 viewport");
  }
  if (JSON.stringify(parsed.moods) !== JSON.stringify(CAPTURE_MOODS)) {
    throw new Error("Camera plan moods have changed");
  }
  const buildings = array(manifest.buildings, "manifest.buildings");
  const buildingById = new Map(
    buildings.map((entry) => [entry.object_id, entry]),
  );
  const selected = selectBuildingCaptureSubjects(manifest);
  const cameras = array(parsed.cameras, "camera plan cameras");
  if (
    cameras.length !== 6 ||
    JSON.stringify(cameras.map((camera) => camera.id)) !==
      JSON.stringify(CAPTURE_CAMERA_IDS)
  ) {
    throw new Error(
      "Camera plan must contain the ordered C3 material/roof views",
    );
  }
  for (const camera of cameras) {
    const entry = buildingById.get(camera.subject_object_id);
    if (entry === undefined) {
      throw new Error(`Camera ${camera.id} references an unknown building`);
    }
    const [expectedFocus, expectedBand] = camera.id.split("-");
    if (
      camera.focus !== expectedFocus ||
      camera.distance_band !== expectedBand ||
      (camera.focus === "material"
        ? camera.subject_object_id !== selected.facade.object_id
        : camera.focus === "roof"
          ? camera.subject_object_id !== selected.roof.object_id
          : true)
    ) {
      throw new Error(
        `Camera ${camera.id} has changed its C3 focus or subject`,
      );
    }
    if (
      camera.subject_source_sha256 !== entry.glb?.sha256 ||
      !DIGEST.test(camera.subject_source_sha256)
    ) {
      throw new Error(`Camera ${camera.id} has stale source geometry pins`);
    }
    for (const key of ["position", "target", "up"]) {
      if (
        !Array.isArray(camera[key]) ||
        camera[key].length !== 3 ||
        camera[key].some((value) => !Number.isFinite(value))
      ) {
        throw new Error(`Camera ${camera.id} ${key} is invalid`);
      }
    }
    for (const key of ["fov_deg", "near_m", "far_m"]) {
      if (!Number.isFinite(camera[key]) || camera[key] <= 0) {
        throw new Error(`Camera ${camera.id} ${key} is invalid`);
      }
    }
    if (
      camera.fov_deg >= 180 ||
      camera.near_m >= camera.far_m ||
      Math.hypot(...camera.up) <= 1e-9 ||
      Math.hypot(
        camera.position[0] - camera.target[0],
        camera.position[1] - camera.target[1],
        camera.position[2] - camera.target[2],
      ) <= camera.near_m
    ) {
      throw new Error(
        `Camera ${camera.id} projection or view direction is invalid`,
      );
    }
  }
  const expected = buildFrozenCameraPlan(
    manifest,
    parsed.reference_manifest_sha256,
  );
  if (JSON.stringify(cameras) !== JSON.stringify(expected.cameras)) {
    throw new Error("Camera plan differs from the source-derived C3 matrix");
  }
  return parsed;
}

function glbParts(bytes, label) {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (data.byteLength < 28) throw new Error(`${label} is too short for GLB`);
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  if (
    view.getUint32(0, true) !== 0x46546c67 ||
    view.getUint32(4, true) !== 2 ||
    view.getUint32(8, true) !== data.byteLength
  ) {
    throw new Error(`${label} is not an exact glTF 2.0 GLB`);
  }
  const jsonLength = view.getUint32(12, true);
  if (
    view.getUint32(16, true) !== 0x4e4f534a ||
    jsonLength % 4 !== 0 ||
    28 + jsonLength > data.byteLength
  ) {
    throw new Error(`${label} has no valid JSON chunk`);
  }
  const binLength = view.getUint32(20 + jsonLength, true);
  if (
    view.getUint32(24 + jsonLength, true) !== 0x004e4942 ||
    28 + jsonLength + binLength !== data.byteLength
  ) {
    throw new Error(`${label} has no exact BIN chunk`);
  }
  const document = JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(
      data.subarray(20, 20 + jsonLength),
    ),
  );
  return { data, document, binOffset: 28 + jsonLength, binLength };
}

function encodeGlbDocument(parts, document) {
  const encoded = Buffer.from(JSON.stringify(document));
  const jsonLength = Math.ceil(encoded.byteLength / 4) * 4;
  const totalLength = 12 + 8 + jsonLength + 8 + parts.binLength;
  const output = Buffer.alloc(totalLength, 0x20);
  output.writeUInt32LE(0x46546c67, 0);
  output.writeUInt32LE(2, 4);
  output.writeUInt32LE(totalLength, 8);
  output.writeUInt32LE(jsonLength, 12);
  output.writeUInt32LE(0x4e4f534a, 16);
  encoded.copy(output, 20);
  const binHeader = 20 + jsonLength;
  output.writeUInt32LE(parts.binLength, binHeader);
  output.writeUInt32LE(0x004e4942, binHeader + 4);
  Buffer.from(
    parts.data.subarray(parts.binOffset, parts.binOffset + parts.binLength),
  ).copy(output, binHeader + 8);
  return output;
}

function expandFacadeProfileToRetainedSource(profileValue, template, entry) {
  const profile = object(profileValue, "candidate facade pane profile");
  if (profile.style_id !== entry.style_id) {
    throw new Error(`${entry.object_id} candidate facade style changed`);
  }
  const asset = object(template.document.asset, "candidate GLB asset");
  const extras = object(asset.extras, "candidate GLB asset.extras");
  const art = object(extras.art_detail, "candidate GLB art detail");
  const detail = object(art.detail, "candidate GLB art detail payload");
  const tiling = object(detail.facade_tiling, "candidate GLB facade tiling");
  const region = array(
    tiling.source_region_px,
    "candidate facade source region",
  );
  const crop = array(profile.source_crop_px, "candidate facade source crop");
  if (
    region.length !== 4 ||
    crop.length !== 4 ||
    ![...region, ...crop].every(Number.isSafeInteger)
  ) {
    throw new Error(`${entry.object_id} facade source coordinates are invalid`);
  }
  const width = region[2] - region[0];
  const height = region[3] - region[1];
  const cropWidth = crop[2] - crop[0];
  const cropHeight = crop[3] - crop[1];
  if (
    width <= 0 ||
    height <= 0 ||
    cropWidth <= 0 ||
    cropHeight <= 0 ||
    JSON.stringify(profile.tile_size_px) !==
      JSON.stringify([cropWidth, cropHeight]) ||
    crop[0] < 0 ||
    crop[1] < 0 ||
    crop[2] > width ||
    crop[3] > height
  ) {
    throw new Error(
      `${entry.object_id} facade crop differs from source region`,
    );
  }
  const rectangles = array(
    profile.glass_rectangles_px,
    "candidate facade panes",
  ).map((raw) => {
    const rectangle = array(raw, "candidate facade pane");
    if (rectangle.length !== 4 || !rectangle.every(Number.isSafeInteger)) {
      throw new Error(`${entry.object_id} facade pane is invalid`);
    }
    return [
      rectangle[0] + crop[0],
      rectangle[1] + crop[1],
      rectangle[2] + crop[0],
      rectangle[3] + crop[1],
    ];
  });
  const polygons = array(
    profile.opaque_polygons_px,
    "candidate facade opaque polygons",
  ).map((raw) =>
    array(raw, "candidate facade opaque polygon").map((point) => {
      const pair = array(point, "candidate facade opaque point");
      if (pair.length !== 2 || !pair.every(Number.isSafeInteger)) {
        throw new Error(`${entry.object_id} facade opaque point is invalid`);
      }
      return [pair[0] + crop[0], pair[1] + crop[1]];
    }),
  );
  return {
    profile: {
      ...structuredClone(profile),
      tile_size_px: [width, height],
      source_crop_px: [0, 0, width, height],
      glass_rectangles_px: rectangles,
      opaque_polygons_px: polygons,
    },
    adaptation: {
      style_id: profile.style_id,
      candidate_tile_size_px: structuredClone(profile.tile_size_px),
      retained_source_tile_size_px: [width, height],
      pane_coordinate_translation_px: [crop[0], crop[1]],
      policy:
        "translate the verified candidate crop-relative pane profile into the retained full source texture",
    },
  };
}

/** Add only the provenance declarations required by the current strict GLB
 * parser and calibrated material path. The retained baseline JSON and BIN
 * payload are first verified; the wrapper preserves every BIN byte and copies
 * material profiles only from the same subject's verified candidate GLB. */
function wrapControlledBaselineGlb(
  bytes,
  presentationTemplateBytes,
  entry,
  artProfile,
  measurement,
) {
  const parts = glbParts(bytes, `${entry.object_id} retained baseline GLB`);
  const template = glbParts(
    presentationTemplateBytes,
    `${entry.object_id} candidate presentation template GLB`,
  );
  const document = structuredClone(parts.document);
  const asset = object(document.asset, "retained baseline GLB asset");
  const extras = object(asset.extras, "retained baseline GLB asset.extras");
  const buffers = array(document.buffers, "retained baseline GLB buffers");
  if (buffers.length !== 1) {
    throw new Error(
      `${entry.object_id} retained baseline has multiple buffers`,
    );
  }
  object(buffers[0], "retained baseline GLB buffer").byteLength =
    parts.binLength;
  if (extras.art_detail !== undefined) {
    throw new Error(
      `${entry.object_id} retained baseline unexpectedly declares art detail`,
    );
  }
  const materials = array(document.materials, "retained baseline materials");
  const templateMaterials = array(
    template.document.materials,
    "candidate presentation template materials",
  );
  if (materials.length !== templateMaterials.length) {
    throw new Error(`${entry.object_id} material inventory changed`);
  }
  const profileAdaptations = [];
  for (const material of materials) {
    const source = object(material, "retained baseline material");
    const matches = templateMaterials.filter(
      (candidate) => candidate.name === source.name,
    );
    if (matches.length !== 1) {
      throw new Error(
        `${entry.object_id} lacks one candidate profile for ${source.name}`,
      );
    }
    const templateMaterial = object(
      matches[0],
      "candidate presentation template material",
    );
    const sourceWithoutExtras = structuredClone(source);
    const templateWithoutExtras = structuredClone(templateMaterial);
    delete sourceWithoutExtras.extras;
    delete templateWithoutExtras.extras;
    if (
      JSON.stringify(sourceWithoutExtras) !==
      JSON.stringify(templateWithoutExtras)
    ) {
      throw new Error(
        `${entry.object_id} candidate profile material differs outside provenance`,
      );
    }
    const templateExtras = object(
      templateMaterial.extras,
      `${entry.object_id} ${source.name} presentation profile`,
    );
    const keys = Object.keys(templateExtras);
    if (!(
      keys.length === 1 &&
      (keys[0] === "authoredFacadePaneProfile" ||
        keys[0] === "authoredBuildingMaterialRole")
    )) {
      throw new Error(
        `${entry.object_id} ${source.name} has an unsupported presentation profile`,
      );
    }
    if (keys[0] === "authoredFacadePaneProfile") {
      const expanded = expandFacadeProfileToRetainedSource(
        templateExtras.authoredFacadePaneProfile,
        template,
        entry,
      );
      source.extras = { authoredFacadePaneProfile: expanded.profile };
      profileAdaptations.push(expanded.adaptation);
    } else {
      source.extras = structuredClone(templateExtras);
    }
  }
  const detail = {
    bays: 0,
    parapet: false,
    parapet_height_m: 0,
    parapet_thickness_m: 0,
    relief: false,
    relief_edges: 0,
    roof_structures: 0,
    triangles: measurement.geometry_counts.derived_triangles,
    vertices: measurement.geometry_counts.derived_vertices,
  };
  extras.art_detail = {
    schema: artProfile.schema,
    class: artProfile.class,
    disclaimer_en: artProfile.disclaimer_en,
    disclaimer_zh: artProfile.disclaimer_zh,
    layers: structuredClone(artProfile.layers),
    max_up_m: entry.height_m,
    triangles: detail.triangles,
    vertices: detail.vertices,
    detail: {
      bays: detail.bays,
      parapet: detail.parapet,
      parapet_height_m: detail.parapet_height_m,
      parapet_thickness_m: detail.parapet_thickness_m,
      facade_relief: detail.relief,
      relief_edges: detail.relief_edges,
      roof_structures: detail.roof_structures,
    },
  };
  const wrapped = encodeGlbDocument(parts, document);
  const wrappedParts = glbParts(
    wrapped,
    `${entry.object_id} controlled baseline GLB`,
  );
  const retainedBin = Buffer.from(
    parts.data.subarray(parts.binOffset, parts.binOffset + parts.binLength),
  );
  const wrappedBin = Buffer.from(
    wrappedParts.data.subarray(
      wrappedParts.binOffset,
      wrappedParts.binOffset + wrappedParts.binLength,
    ),
  );
  if (!retainedBin.equals(wrappedBin)) {
    throw new Error(`${entry.object_id} controlled wrapper changed BIN bytes`);
  }
  return {
    bytes: wrapped,
    artDetail: detail,
    retainedBinSha256: sha256(retainedBin),
    controlledBinSha256: sha256(wrappedBin),
    presentationTemplateSha256: sha256(presentationTemplateBytes),
    profileAdaptations,
  };
}

const COMPONENT_SIZE = Object.freeze({
  5120: 1,
  5121: 1,
  5122: 2,
  5123: 2,
  5125: 4,
  5126: 4,
});
const COMPONENTS = Object.freeze({ SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 });

function accessorLayout(parts, index) {
  const accessor = parts.document.accessors?.[index];
  if (accessor === undefined) throw new Error(`Missing accessor ${index}`);
  const bufferView = parts.document.bufferViews?.[accessor.bufferView];
  if (bufferView === undefined || bufferView.buffer !== 0) {
    throw new Error(`Accessor ${index} has no embedded buffer view`);
  }
  const componentSize = COMPONENT_SIZE[accessor.componentType];
  const components = COMPONENTS[accessor.type];
  if (componentSize === undefined || components === undefined) {
    throw new Error(`Accessor ${index} type is unsupported`);
  }
  const itemBytes = componentSize * components;
  const stride = bufferView.byteStride ?? itemBytes;
  const start =
    parts.binOffset + (bufferView.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
  if (
    !Number.isSafeInteger(accessor.count) ||
    accessor.count < 1 ||
    stride < itemBytes ||
    start + (accessor.count - 1) * stride + itemBytes >
      parts.binOffset + parts.binLength
  ) {
    throw new Error(`Accessor ${index} byte range is invalid`);
  }
  return { accessor, start, stride, itemBytes, componentSize, components };
}

function accessorPayload(parts, index, count = null) {
  const layout = accessorLayout(parts, index);
  const items = count ?? layout.accessor.count;
  if (
    !Number.isSafeInteger(items) ||
    items < 0 ||
    items > layout.accessor.count
  ) {
    throw new Error(`Accessor ${index} prefix count is invalid`);
  }
  const result = Buffer.alloc(items * layout.itemBytes);
  for (let item = 0; item < items; item++) {
    result.set(
      parts.data.subarray(
        layout.start + item * layout.stride,
        layout.start + item * layout.stride + layout.itemBytes,
      ),
      item * layout.itemBytes,
    );
  }
  return result;
}

function positionValues(parts, index, count = null) {
  const layout = accessorLayout(parts, index);
  if (
    layout.accessor.componentType !== 5126 ||
    layout.accessor.type !== "VEC3"
  ) {
    throw new Error(`Accessor ${index} is not float32 VEC3`);
  }
  const items = count ?? layout.accessor.count;
  if (
    !Number.isSafeInteger(items) ||
    items < 0 ||
    items > layout.accessor.count
  ) {
    throw new Error(`Accessor ${index} position prefix count is invalid`);
  }
  const view = new DataView(
    parts.data.buffer,
    parts.data.byteOffset,
    parts.data.byteLength,
  );
  return Array.from({ length: items }, (_, item) =>
    [0, 1, 2].map((axis) =>
      view.getFloat32(
        layout.start + item * layout.stride + axis * layout.componentSize,
        true,
      ),
    ),
  );
}

function measuredEnvelope(parts, entry) {
  const positionIndices = new Set();
  for (const mesh of array(parts.document.meshes, "GLB meshes")) {
    for (const primitive of array(mesh.primitives, "GLB primitives")) {
      positionIndices.add(primitive.attributes?.POSITION);
    }
  }
  const measured = {
    min_e: Infinity,
    min_n: Infinity,
    max_e: -Infinity,
    max_n: -Infinity,
    base_up: Infinity,
    top_up: -Infinity,
  };
  for (const index of positionIndices) {
    for (const [x, y, z] of positionValues(parts, index)) {
      const east = entry.anchor_east_m + x;
      const north = entry.anchor_north_m - z;
      const up = entry.base_enu_up_m + y;
      measured.min_e = Math.min(measured.min_e, east);
      measured.max_e = Math.max(measured.max_e, east);
      measured.min_n = Math.min(measured.min_n, north);
      measured.max_n = Math.max(measured.max_n, north);
      measured.base_up = Math.min(measured.base_up, up);
      measured.top_up = Math.max(measured.top_up, up);
    }
  }
  return Object.fromEntries(
    Object.entries(measured).map(([key, value]) => [key, round(value)]),
  );
}

function envelopeError(actual, expected) {
  return Math.max(
    ...Object.keys(actual).map((key) => Math.abs(actual[key] - expected[key])),
  );
}

/** Compare canonical and derived geometry for a captured building. Derived art
 * may lower the original roof ring by the declared parapet height, then add the
 * labelled parapet/roof structures. It may not move the footprint or envelope. */
export function measureSourceDerivedGeometry(entry, sourceBytes, derivedBytes) {
  const source = glbParts(sourceBytes, `${entry.object_id} canonical GLB`);
  const derived = glbParts(derivedBytes, `${entry.object_id} derived GLB`);
  const sourceDigest = sha256(source.data);
  const derivedDigest = sha256(derived.data);
  const sourceAccessors = array(
    source.document.accessors,
    "canonical GLB accessors",
  );
  const derivedAccessors = array(
    derived.document.accessors,
    "derived GLB accessors",
  );
  if (sourceAccessors.length < 7 || derivedAccessors.length < 7) {
    throw new Error(
      `${entry.object_id} no longer has the recorded wall/roof layout`,
    );
  }
  const geometryCounts = {
    source_vertices: sourceAccessors[0].count + sourceAccessors[4].count,
    source_triangles: (sourceAccessors[3].count + sourceAccessors[6].count) / 3,
    derived_vertices: derivedAccessors[0].count + derivedAccessors[4].count,
    derived_triangles:
      (derivedAccessors[3].count + derivedAccessors[6].count) / 3,
  };
  if (
    Object.values(geometryCounts).some((value) => !Number.isSafeInteger(value))
  ) {
    throw new Error(`${entry.object_id} has non-triangular recorded geometry`);
  }
  const exactCommonAccessors = [0, 1, 3, 5, 6].map((index) => {
    const sourceCount = sourceAccessors[index].count;
    const sourcePayload = accessorPayload(source, index);
    const derivedPrefix = accessorPayload(derived, index, sourceCount);
    return {
      accessor: index,
      source_count: sourceCount,
      source_sha256: sha256(sourcePayload),
      derived_prefix_sha256: sha256(derivedPrefix),
      byte_identical: sourcePayload.equals(derivedPrefix),
    };
  });
  const sourceRoof = positionValues(source, 4);
  const derivedRoofPrefix = positionValues(derived, 4, sourceRoof.length);
  let horizontalErrorM = 0;
  const verticalDrops = [];
  for (let index = 0; index < sourceRoof.length; index++) {
    horizontalErrorM = Math.max(
      horizontalErrorM,
      Math.abs(sourceRoof[index][0] - derivedRoofPrefix[index][0]),
      Math.abs(sourceRoof[index][2] - derivedRoofPrefix[index][2]),
    );
    verticalDrops.push(sourceRoof[index][1] - derivedRoofPrefix[index][1]);
  }
  const declaredDrop = entry.art_detail?.parapet
    ? entry.art_detail.parapet_height_m
    : 0;
  const verticalDropErrorM = Math.max(
    ...verticalDrops.map((value) => Math.abs(value - declaredDrop)),
  );
  const sourceEnvelope = measuredEnvelope(source, entry);
  const derivedEnvelope = measuredEnvelope(derived, entry);
  const sourceEnvelopeErrorM = envelopeError(sourceEnvelope, entry.envelope);
  const derivedEnvelopeErrorM = envelopeError(derivedEnvelope, entry.envelope);
  const commonGeometryMatches = exactCommonAccessors.every(
    (item) => item.byte_identical,
  );
  const match =
    sourceDigest === entry.glb.sha256 &&
    source.data.byteLength === entry.glb.bytes &&
    derivedDigest === entry.derived_glb.sha256 &&
    derived.data.byteLength === entry.derived_glb.bytes &&
    commonGeometryMatches &&
    horizontalErrorM <= 1e-6 &&
    verticalDropErrorM <= 1e-5 &&
    sourceEnvelopeErrorM <= ENVELOPE_TOLERANCE_M &&
    derivedEnvelopeErrorM <= ENVELOPE_TOLERANCE_M;
  return {
    object_id: entry.object_id,
    source: {
      sha256: sourceDigest,
      bytes: source.data.byteLength,
      envelope: sourceEnvelope,
    },
    derived: {
      sha256: derivedDigest,
      bytes: derived.data.byteLength,
      envelope: derivedEnvelope,
    },
    exact_common_accessors: exactCommonAccessors,
    geometry_counts: geometryCounts,
    roof_transform: {
      common_vertex_count: sourceRoof.length,
      horizontal_max_error_m: horizontalErrorM,
      declared_parapet_drop_m: declaredDrop,
      measured_drop_min_m: Math.min(...verticalDrops),
      measured_drop_max_m: Math.max(...verticalDrops),
      drop_max_error_m: verticalDropErrorM,
      added_vertices: derivedAccessors[4].count - sourceAccessors[4].count,
      added_triangles:
        (derivedAccessors[6].count - sourceAccessors[6].count) / 3,
    },
    source_envelope_max_error_m: sourceEnvelopeErrorM,
    derived_envelope_max_error_m: derivedEnvelopeErrorM,
    source_vs_derived_geometry_match: match,
  };
}

export function summarizeFrameSamples(values) {
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error("Frame sample set must not be empty");
  }
  if (values.some((value) => !Number.isFinite(value) || value < 0)) {
    throw new Error("Frame samples must be finite and nonnegative");
  }
  const sorted = [...values].sort((left, right) => left - right);
  const middle = sorted.length / 2;
  const median = Number.isInteger(middle)
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[Math.floor(middle)];
  return {
    count: values.length,
    median_ms: median,
    p90_ms: sorted[Math.ceil(sorted.length * 0.9) - 1],
    min_ms: sorted[0],
    max_ms: sorted.at(-1),
    values_ms: values,
  };
}

export function expectedReflectionCaptureDelta(transition) {
  const state = object(transition, "reflection transition");
  return state.target_after === null ? 0 : 1;
}

/** Chromium may issue its own favicon request even when the application does
 * not declare one. Ignore only that exact loopback request and abort reason;
 * every application and scene request failure remains fatal. */
export function isIgnorableBrowserRequestFailure(value, origin) {
  if (typeof value !== "string") return false;
  let parsed;
  try {
    parsed = new URL(origin);
  } catch {
    return false;
  }
  if (
    parsed.protocol !== "https:" ||
    !["127.0.0.1", "[::1]", "localhost"].includes(parsed.hostname)
  ) {
    return false;
  }
  return value === `${parsed.origin}/favicon.ico net::ERR_ABORTED`;
}

function recordBrowserRequestFailures(report, failures, origin) {
  const values = [...failures].sort();
  report.browser_ignored_request_failures = values.filter((value) =>
    isIgnorableBrowserRequestFailure(value, origin),
  );
  report.request_failures = values.filter(
    (value) => !isIgnorableBrowserRequestFailure(value, origin),
  );
}

/** Fail closed if the candidate run did not use the same integrated renderer,
 * source scene, frozen cameras and hardware context as its private baseline. */
export function verifyMatchedCaptureReports(referenceValue, candidateValue) {
  const reference = object(referenceValue, "matched baseline report");
  const candidate = object(candidateValue, "candidate report");
  if (
    reference.schema !== BUILDING_CAPTURE_REPORT_SCHEMA ||
    candidate.schema !== BUILDING_CAPTURE_REPORT_SCHEMA ||
    reference.completed !== true
  ) {
    throw new Error("Matched baseline report is incomplete or unsupported");
  }
  if (
    reference.building_variant?.kind !== "controlled-subject-baseline" ||
    candidate.building_variant?.kind !== "served-candidate" ||
    reference.private_route_overrides?.enabled !== true ||
    candidate.private_route_overrides?.enabled !== false
  ) {
    throw new Error(
      "Matched reports do not form a controlled baseline/candidate pair",
    );
  }
  const candidateManifest = string(
    candidate.building_variant.served_candidate_manifest_sha256,
    "candidate served manifest",
  );
  if (
    reference.building_variant.served_candidate_manifest_sha256 !==
      candidateManifest ||
    candidate.manifest?.sha256 !== candidateManifest
  ) {
    throw new Error("Matched reports use different candidate manifests");
  }
  const equal = (label, left, right) => {
    if (JSON.stringify(left) !== JSON.stringify(right)) {
      throw new Error(`Matched reports differ in ${label}`);
    }
  };
  equal("camera plan", reference.camera_plan, candidate.camera_plan);
  equal(
    "served scene document",
    reference.scene_document?.served,
    candidate.scene_document?.served,
  );
  equal("scene id", reference.manifest?.scene_id, candidate.manifest?.scene_id);
  equal(
    "source object identity",
    reference.manifest?.objects_json_sha256,
    candidate.manifest?.objects_json_sha256,
  );
  equal(
    "application bundle",
    reference.bundle_instrumentation?.source_sha256,
    candidate.bundle_instrumentation?.source_sha256,
  );
  const referenceRenderer = object(
    reference.renderer_query,
    "matched baseline renderer query",
  );
  const candidateRenderer = object(
    candidate.renderer_query,
    "candidate renderer query",
  );
  if (
    referenceRenderer.render_backend !== "hardware" ||
    candidateRenderer.render_backend !== "hardware"
  ) {
    throw new Error("Matched C3 reports require hardware renderers");
  }
  for (const field of [
    "webgl_vendor",
    "webgl_renderer",
    "webgl_version",
    "canvas_buffer",
    "canvas_css",
    "local_reflection_owners",
    "reflections_enabled",
    "reflection_profile",
  ]) {
    equal(
      `renderer ${field}`,
      referenceRenderer[field],
      candidateRenderer[field],
    );
  }
  const referenceFrames = object(reference.frames, "matched baseline frames");
  const candidateFrames = object(candidate.frames, "candidate frames");
  const frameNames = Object.keys(referenceFrames).sort();
  equal("frame inventory", frameNames, Object.keys(candidateFrames).sort());
  if (frameNames.length !== CAPTURE_CAMERA_IDS.length * CAPTURE_MOODS.length) {
    throw new Error("Matched baseline report has an incomplete frame matrix");
  }
  for (const name of frameNames) {
    equal(
      `${name} camera state`,
      referenceFrames[name]?.camera_state_sha256,
      candidateFrames[name]?.camera_state_sha256,
    );
  }
  const sourceIdentity = (report, label) =>
    array(report.geometry_measurements, `${label} geometry measurements`)
      .map((measurement) => ({
        object_id: measurement.object_id,
        source_sha256: measurement.source?.sha256,
        source_bytes: measurement.source?.bytes,
      }))
      .sort((left, right) => left.object_id.localeCompare(right.object_id));
  equal(
    "source geometry",
    sourceIdentity(reference, "matched baseline"),
    sourceIdentity(candidate, "candidate"),
  );
  return {
    baseline_label: reference.label,
    candidate_manifest_sha256: candidateManifest,
    application_bundle_sha256: candidate.bundle_instrumentation.source_sha256,
    camera_plan_sha256: candidate.camera_plan.sha256,
    matched_frames: frameNames.length,
    runtime_identity_match: true,
  };
}

async function fetchBytes(
  url,
  label,
  expected = null,
  allowLocalSelfSigned = false,
) {
  const parsed = new URL(url);
  let bytes;
  if (allowLocalSelfSigned) {
    if (
      parsed.protocol !== "https:" ||
      !["127.0.0.1", "[::1]", "localhost"].includes(parsed.hostname)
    ) {
      throw new Error(
        `${label} self-signed certificate bypass escaped the loopback HTTPS origin`,
      );
    }
    bytes = await new Promise((resolveBytes, reject) => {
      const request = httpsGet(
        parsed,
        { rejectUnauthorized: false },
        (response) => {
          const status = response.statusCode ?? 0;
          if (status < 200 || status >= 300) {
            response.resume();
            reject(new Error(`${label} returned HTTP ${status}`));
            return;
          }
          const chunks = [];
          response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
          response.on("end", () => resolveBytes(Buffer.concat(chunks)));
          response.on("error", reject);
        },
      );
      request.on("error", reject);
    });
  } else {
    const response = await fetch(parsed);
    if (!response.ok)
      throw new Error(`${label} returned HTTP ${response.status}`);
    bytes = Buffer.from(await response.arrayBuffer());
  }
  if (expected !== null) {
    if (bytes.byteLength !== expected.bytes) {
      throw new Error(`${label} byte count differs from its pin`);
    }
    if (sha256(bytes) !== expected.sha256) {
      throw new Error(`${label} digest differs from its pin`);
    }
  }
  return bytes;
}

function publicRepositoryBase(baseUrl) {
  const parsed = new URL(baseUrl, "https://capture.invalid");
  if (
    parsed.origin !== "https://capture.invalid" ||
    !parsed.pathname.startsWith("/building-renders/") ||
    !parsed.pathname.endsWith("/") ||
    parsed.pathname.includes("..") ||
    parsed.search !== "" ||
    parsed.hash !== ""
  ) {
    throw new Error(
      "Building render base URL cannot be mapped to public assets",
    );
  }
  return `frontend/public${parsed.pathname}`;
}

function gitBytes(commit, path, label) {
  try {
    return Buffer.from(
      execFileSync("git", ["show", `${commit}:${path}`], {
        cwd: REPOSITORY_ROOT,
        encoding: "buffer",
        maxBuffer: 16 * 1024 * 1024,
      }),
    );
  } catch {
    throw new Error(`${label} is missing from controlled baseline commit`);
  }
}

function pinnedGitBytes(commit, path, expected, label) {
  const bytes = gitBytes(commit, path, label);
  if (
    bytes.byteLength !== expected.bytes ||
    sha256(bytes) !== expected.sha256
  ) {
    throw new Error(`${label} differs from its retained baseline identity`);
  }
  return bytes;
}

function canonicalEntryIdentity(entry) {
  return JSON.stringify({
    object_id: entry.object_id,
    osm: entry.osm,
    glb: entry.glb,
    anchor_east_m: entry.anchor_east_m,
    anchor_north_m: entry.anchor_north_m,
    base_enu_up_m: entry.base_enu_up_m,
    height_m: entry.height_m,
    envelope: entry.envelope,
    style_id: entry.style_id,
  });
}

function assertBaselineSourceCompatibility(current, baseline) {
  if (
    current.scene?.id !== baseline.scene?.id ||
    current.scene?.objects_json_sha256 !==
      baseline.scene?.objects_json_sha256 ||
    current.scene?.mesh_pack_source_sha256 !==
      baseline.scene?.mesh_pack_source_sha256
  ) {
    throw new Error("Controlled baseline uses different source scene geometry");
  }
  const baselineById = new Map(
    array(baseline.buildings, "baseline buildings").map((entry) => [
      entry.object_id,
      entry,
    ]),
  );
  const buildings = array(current.buildings, "current buildings");
  if (baselineById.size !== buildings.length) {
    throw new Error("Controlled baseline building inventory has changed");
  }
  for (const entry of buildings) {
    const baselineEntry = baselineById.get(entry.object_id);
    if (
      baselineEntry === undefined ||
      canonicalEntryIdentity(entry) !== canonicalEntryIdentity(baselineEntry)
    ) {
      throw new Error(
        `Controlled baseline source identity changed for ${entry.object_id}`,
      );
    }
  }
}

function glbTextureDigests(bytes, label) {
  const parts = glbParts(bytes, label);
  return unique(
    array(parts.document.images ?? [], `${label} images`).map(
      (image, index) => {
        const uri = string(image.uri, `${label} images[${index}].uri`);
        const match = /^assets\/([0-9a-f]{64})$/.exec(uri);
        if (match === null) {
          throw new Error(`${label} references a non-content-addressed image`);
        }
        return match[1];
      },
    ),
  );
}

export function buildControlledBaselineManifest(
  current,
  baseline,
  baselineMeasurements,
  baselineTextureRefs,
  controlledGlbRefs,
) {
  assertBaselineSourceCompatibility(current, baseline);
  if (current.art_detail === undefined) {
    throw new Error(
      "Controlled C3 baseline requires the candidate art contract",
    );
  }
  const fixture = structuredClone(current);
  const baselineById = new Map(
    baseline.buildings.map((entry) => [entry.object_id, entry]),
  );
  const measurements = new Map(
    baselineMeasurements.map((measurement) => [
      measurement.object_id,
      measurement,
    ]),
  );
  const subjectIds = Object.values(selectBuildingCaptureSubjects(current)).map(
    (entry) => entry.object_id,
  );
  for (const objectId of subjectIds) {
    const entry = fixture.buildings.find(
      (candidate) => candidate.object_id === objectId,
    );
    const baselineEntry = baselineById.get(objectId);
    const measurement = measurements.get(objectId);
    const controlledGlb = controlledGlbRefs.get(objectId);
    if (
      entry === undefined ||
      baselineEntry === undefined ||
      measurement === undefined ||
      controlledGlb === undefined ||
      !measurement.source_vs_derived_geometry_match
    ) {
      throw new Error(`Controlled baseline subject ${objectId} is unverified`);
    }
    entry.derived_glb = structuredClone(controlledGlb.reference);
    entry.art_detail = structuredClone(controlledGlb.artDetail);
  }
  const textures = new Map(
    fixture.textures.map((texture) => [texture.sha256, texture]),
  );
  for (const texture of baselineTextureRefs) {
    const prior = textures.get(texture.sha256);
    if (
      prior !== undefined &&
      JSON.stringify(prior) !== JSON.stringify(texture)
    ) {
      throw new Error(
        `Controlled baseline texture ${texture.sha256} changed identity`,
      );
    }
    textures.set(texture.sha256, structuredClone(texture));
  }
  fixture.textures = [...textures.values()].sort((left, right) =>
    left.sha256.localeCompare(right.sha256),
  );
  const details = fixture.buildings.map((entry) => entry.art_detail);
  if (details.some((detail) => detail === undefined)) {
    throw new Error("Controlled baseline lost candidate art metadata");
  }
  Object.assign(fixture.counts, {
    derived_total_bytes: fixture.buildings.reduce(
      (total, entry) => total + entry.derived_glb.bytes,
      0,
    ),
    texture_bytes: fixture.textures.reduce(
      (total, texture) => total + texture.bytes,
      0,
    ),
    textures: fixture.textures.length,
    derived_vertices: details.reduce(
      (total, detail) => total + detail.vertices,
      0,
    ),
    derived_triangles: details.reduce(
      (total, detail) => total + detail.triangles,
      0,
    ),
    art_detail_buildings: details.filter((detail) => detail.vertices > 0)
      .length,
    art_detail_relief_buildings: details.filter((detail) => detail.relief)
      .length,
    art_detail_roofs: details.filter((detail) => detail.parapet).length,
    art_detail_structures: details.filter(
      (detail) => detail.roof_structures > 0,
    ).length,
  });
  return fixture;
}

function replaceSingleDigest(bytes, before, after, label) {
  if (!DIGEST.test(before) || !DIGEST.test(after)) {
    throw new Error(`${label} digest replacement is invalid`);
  }
  const source = bytes.toString("utf8");
  const pieces = source.split(before);
  if (pieces.length !== 2) {
    throw new Error(`${label} must contain its prior binding exactly once`);
  }
  return Buffer.from(`${pieces[0]}${after}${pieces[1]}`);
}

/** Re-pin the unchanged current road documents to the in-memory controlled
 * building manifest. This private fixture changes only strict source-binding
 * digests; road, fixture, traffic and flight geometry remain byte-for-byte
 * otherwise identical to the served candidate documents. */
async function buildControlledRoadBindingOverrides(
  served,
  scene,
  controlledManifestSha256,
) {
  const refs = object(scene.road_assets, "scene road assets");
  const documents = new Map();
  const loaded = new Map();
  for (const kind of ROAD_BINDING_KINDS) {
    const ref = object(refs[kind], `scene road assets ${kind}`);
    const expected = {
      sha256: string(ref.sha256, `${kind} SHA-256`),
      bytes: finiteNumber(ref.size_bytes, `${kind} byte count`),
    };
    if (
      !DIGEST.test(expected.sha256) ||
      !Number.isSafeInteger(expected.bytes)
    ) {
      throw new Error(`Scene road assets ${kind} pin is invalid`);
    }
    const url = new URL(string(ref.url, `${kind} URL`), served.baseUrl);
    if (url.origin !== served.baseUrl.origin) {
      throw new Error(`Scene road assets ${kind} must remain same-origin`);
    }
    const supplied = served.roadBindingDocuments?.get(kind);
    const bytes =
      supplied === undefined
        ? await fetchBytes(
            url,
            `served ${kind} source binding`,
            expected,
            served.allowLocalSelfSigned,
          )
        : Buffer.from(supplied);
    if (
      bytes.byteLength !== expected.bytes ||
      sha256(bytes) !== expected.sha256
    ) {
      throw new Error(`Served ${kind} source binding differs from its pin`);
    }
    const value = object(JSON.parse(bytes.toString("utf8")), `${kind} payload`);
    const context = object(value.source_context, `${kind} source context`);
    if (context.building_render_manifest_sha256 !== served.manifestSha256) {
      throw new Error(
        `Served ${kind} source context does not pin the candidate building manifest`,
      );
    }
    loaded.set(kind, { ref, url, bytes, value });
  }

  const replacements = new Map();
  for (const kind of ROAD_BINDING_KINDS) {
    const item = loaded.get(kind);
    replacements.set(
      kind,
      replaceSingleDigest(
        item.bytes,
        served.manifestSha256,
        controlledManifestSha256,
        `${kind} building-manifest binding`,
      ),
    );
  }
  const fixture = loaded.get("effective_fixtures");
  const fixtureSha256 = sha256(replacements.get("effective_fixtures"));
  const traffic = loaded.get("traffic");
  const trafficBasis = object(
    traffic.value.visual_obstacle_basis,
    "traffic visual obstacle basis",
  );
  if (trafficBasis.effective_fixture_geometry_sha256 !== fixture.ref.sha256) {
    throw new Error(
      "Served traffic does not pin the candidate effective fixtures",
    );
  }
  replacements.set(
    "traffic",
    replaceSingleDigest(
      replacements.get("traffic"),
      fixture.ref.sha256,
      fixtureSha256,
      "traffic effective-fixture binding",
    ),
  );

  const records = [];
  for (const kind of ROAD_BINDING_KINDS) {
    const item = loaded.get(kind);
    const bytes = replacements.get(kind);
    const digest = sha256(bytes);
    refs[kind] = {
      ...item.ref,
      sha256: digest,
      size_bytes: bytes.byteLength,
    };
    documents.set(item.url.href, {
      bytes,
      mediaType: "application/json",
      source: `served candidate ${kind}; source-binding digests repinned in memory`,
    });
    records.push({
      kind,
      url: item.url.pathname,
      served_sha256: item.ref.sha256,
      controlled_sha256: digest,
      bytes: bytes.byteLength,
      changed_fields:
        kind === "traffic"
          ? [
              "source_context.building_render_manifest_sha256",
              "visual_obstacle_basis.effective_fixture_geometry_sha256",
            ]
          : ["source_context.building_render_manifest_sha256"],
    });
  }
  return { documents, records };
}

export async function buildControlledBaselineOverride(
  served,
  controlledBaseline,
  sourceAssets,
) {
  const resolvedCommit = execFileSync(
    "git",
    ["rev-parse", "--verify", `${controlledBaseline.commit}^{commit}`],
    { cwd: REPOSITORY_ROOT, encoding: "utf8" },
  ).trim();
  if (resolvedCommit !== controlledBaseline.commit) {
    throw new Error("Controlled baseline commit did not resolve exactly");
  }
  const baselineScenePath = `frontend/public${BUILDING_SCENE_PATH}`;
  const baselineSceneBytes = gitBytes(
    controlledBaseline.commit,
    baselineScenePath,
    "baseline scene",
  );
  const baselineScene = JSON.parse(baselineSceneBytes.toString("utf8"));
  const baselineReference = object(
    baselineScene.building_render?.manifest,
    "baseline scene building manifest",
  );
  if (
    baselineReference.sha256 !== controlledBaseline.manifest.sha256 ||
    baselineReference.size_bytes !== controlledBaseline.manifest.bytes
  ) {
    throw new Error("Controlled baseline scene has a different manifest pin");
  }
  const baselineBase = publicRepositoryBase(
    baselineScene.building_render.base_url,
  );
  const servedBase = publicRepositoryBase(
    served.scene.building_render.base_url,
  );
  if (baselineBase !== servedBase) {
    throw new Error("Controlled baseline building base URL has changed");
  }
  const baselineManifestBytes = pinnedGitBytes(
    controlledBaseline.commit,
    `${baselineBase}assets/${controlledBaseline.manifest.sha256}`,
    controlledBaseline.manifest,
    "baseline building manifest",
  );
  const baselineManifest = JSON.parse(baselineManifestBytes.toString("utf8"));
  assertBaselineSourceCompatibility(served.manifest, baselineManifest);
  const baselineTextures = new Map(
    baselineManifest.textures.map((texture) => [texture.sha256, texture]),
  );
  const baselineById = new Map(
    baselineManifest.buildings.map((entry) => [entry.object_id, entry]),
  );
  const overrideAssets = new Map();
  const measurements = [];
  const textureRefs = new Map();
  const controlledGlbRefs = new Map();
  const controlledGlbWrappers = [];
  const artProfile = object(
    served.manifest.art_detail,
    "candidate manifest art profile",
  );
  for (const currentEntry of Object.values(
    selectBuildingCaptureSubjects(served.manifest),
  )) {
    const baselineEntry = baselineById.get(currentEntry.object_id);
    if (baselineEntry === undefined) {
      throw new Error(`Baseline subject ${currentEntry.object_id} is missing`);
    }
    const derivedBytes = pinnedGitBytes(
      controlledBaseline.commit,
      `${baselineBase}${baselineEntry.derived_glb.path}`,
      {
        sha256: baselineEntry.derived_glb.sha256,
        bytes: baselineEntry.derived_glb.bytes,
      },
      `${currentEntry.object_id} baseline derived GLB`,
    );
    const sourceBytes = await readFile(
      resolve(sourceAssets, `${currentEntry.object_id}.glb`),
    );
    const suppliedTemplate = served.candidateGlbBytes?.get(
      currentEntry.object_id,
    );
    const presentationTemplateBytes =
      suppliedTemplate === undefined
        ? await activeAssetBytes(
            served,
            currentEntry.derived_glb,
            `${currentEntry.object_id} candidate presentation template GLB`,
          )
        : Buffer.from(suppliedTemplate);
    if (
      presentationTemplateBytes.byteLength !== currentEntry.derived_glb.bytes ||
      sha256(presentationTemplateBytes) !== currentEntry.derived_glb.sha256
    ) {
      throw new Error(
        `${currentEntry.object_id} candidate presentation template differs from its pin`,
      );
    }
    const measurement = measureSourceDerivedGeometry(
      baselineEntry,
      sourceBytes,
      derivedBytes,
    );
    if (!measurement.source_vs_derived_geometry_match) {
      throw new Error(
        `${currentEntry.object_id} retained baseline geometry did not verify`,
      );
    }
    measurements.push(measurement);
    const wrapper = wrapControlledBaselineGlb(
      derivedBytes,
      presentationTemplateBytes,
      baselineEntry,
      artProfile,
      measurement,
    );
    const controlledSha256 = sha256(wrapper.bytes);
    const controlledReference = {
      ...baselineEntry.derived_glb,
      path: `assets/${controlledSha256}`,
      sha256: controlledSha256,
      bytes: wrapper.bytes.byteLength,
    };
    controlledGlbRefs.set(currentEntry.object_id, {
      reference: controlledReference,
      artDetail: wrapper.artDetail,
    });
    controlledGlbWrappers.push({
      object_id: currentEntry.object_id,
      retained_sha256: baselineEntry.derived_glb.sha256,
      retained_bytes: derivedBytes.byteLength,
      controlled_sha256: controlledSha256,
      controlled_bytes: wrapper.bytes.byteLength,
      retained_bin_sha256: wrapper.retainedBinSha256,
      controlled_bin_sha256: wrapper.controlledBinSha256,
      presentation_template_sha256: wrapper.presentationTemplateSha256,
      material_profile_adaptations: wrapper.profileAdaptations,
      bin_byte_identical: true,
      changed_scope:
        "GLB JSON art/material provenance, crop-to-full-source profile coordinates and strict padded BIN byte-count declaration only",
    });
    overrideAssets.set(controlledSha256, {
      bytes: wrapper.bytes,
      mediaType: "model/gltf-binary",
      source: `${controlledBaseline.commit}:${baselineBase}${baselineEntry.derived_glb.path}; JSON provenance wrapper generated in memory`,
    });
    for (const digest of glbTextureDigests(
      derivedBytes,
      `${currentEntry.object_id} baseline derived GLB`,
    )) {
      const ref = baselineTextures.get(digest);
      if (ref === undefined) {
        throw new Error(`Baseline GLB references undeclared texture ${digest}`);
      }
      textureRefs.set(digest, ref);
    }
  }
  for (const texture of textureRefs.values()) {
    const bytes = pinnedGitBytes(
      controlledBaseline.commit,
      `${baselineBase}assets/${texture.sha256}`,
      { sha256: texture.sha256, bytes: texture.bytes },
      `baseline texture ${texture.sha256}`,
    );
    overrideAssets.set(texture.sha256, {
      bytes,
      mediaType: texture.media_type,
      source: `${controlledBaseline.commit}:${baselineBase}assets/${texture.sha256}`,
    });
  }
  const manifest = buildControlledBaselineManifest(
    served.manifest,
    baselineManifest,
    measurements,
    [...textureRefs.values()],
    controlledGlbRefs,
  );
  const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  const manifestSha256 = sha256(manifestBytes);
  overrideAssets.set(manifestSha256, {
    bytes: manifestBytes,
    mediaType: "application/json",
    source: "generated controlled subject-only baseline manifest",
  });
  const scene = structuredClone(served.scene);
  scene.building_render.manifest = {
    sha256: manifestSha256,
    size_bytes: manifestBytes.byteLength,
  };
  const roadBindings = await buildControlledRoadBindingOverrides(
    served,
    scene,
    manifestSha256,
  );
  const sceneOverrideBytes = Buffer.from(`${JSON.stringify(scene, null, 2)}\n`);
  return {
    ...served,
    scene,
    sceneOverrideBytes,
    manifest,
    manifestBytes,
    manifestSha256,
    overrideAssets,
    overrideDocuments: roadBindings.documents,
    variant: {
      kind: "controlled-subject-baseline",
      baseline_commit: controlledBaseline.commit,
      retained_baseline_manifest: controlledBaseline.manifest,
      served_candidate_manifest_sha256: served.manifestSha256,
      controlled_manifest_sha256: manifestSha256,
      controlled_manifest_bytes: manifestBytes.byteLength,
      subjects: measurements.map((measurement) => measurement.object_id),
      overridden_assets: [...overrideAssets]
        .map(([digest, asset]) => ({
          sha256: digest,
          bytes: asset.bytes.byteLength,
          media_type: asset.mediaType,
          source: asset.source,
        }))
        .sort((left, right) => left.sha256.localeCompare(right.sha256)),
      repinned_source_bindings: roadBindings.records,
      controlled_glb_wrappers: controlledGlbWrappers,
      retained_geometry_measurements: measurements,
    },
  };
}

async function loadPinnedManifest(
  origin,
  controlledBaseline,
  sourceAssets,
  allowLocalSelfSigned,
) {
  const sceneBytes = await fetchBytes(
    new URL(BUILDING_SCENE_PATH, origin),
    "building scene",
    null,
    allowLocalSelfSigned,
  );
  const scene = JSON.parse(sceneBytes.toString("utf8"));
  const render = object(scene.building_render, "scene.building_render");
  const reference = object(render.manifest, "scene.building_render.manifest");
  const manifestSha256 = string(reference.sha256, "manifest.sha256");
  const manifestBytes = finiteNumber(
    reference.size_bytes,
    "manifest.size_bytes",
  );
  if (!DIGEST.test(manifestSha256) || !Number.isSafeInteger(manifestBytes)) {
    throw new Error("Scene building manifest pin is invalid");
  }
  const baseUrl = new URL(string(render.base_url, "building base_url"), origin);
  if (baseUrl.origin !== new URL(origin).origin) {
    throw new Error("Building base URL must remain same-origin");
  }
  const bytes = await fetchBytes(
    new URL(`assets/${manifestSha256}`, baseUrl),
    "building manifest",
    { sha256: manifestSha256, bytes: manifestBytes },
    allowLocalSelfSigned,
  );
  const served = {
    scene,
    sceneBytes,
    sceneOverrideBytes: null,
    manifest: JSON.parse(bytes.toString("utf8")),
    manifestBytes: bytes,
    manifestSha256,
    baseUrl,
    allowLocalSelfSigned,
    overrideAssets: new Map(),
    overrideDocuments: new Map(),
    variant: {
      kind: "served-candidate",
      served_candidate_manifest_sha256: manifestSha256,
      overridden_assets: [],
    },
  };
  return controlledBaseline === null
    ? served
    : buildControlledBaselineOverride(served, controlledBaseline, sourceAssets);
}

async function activeAssetBytes(loaded, reference, label) {
  const override = loaded.overrideAssets.get(reference.sha256);
  if (override !== undefined) {
    if (
      override.bytes.byteLength !== reference.bytes ||
      sha256(override.bytes) !== reference.sha256
    ) {
      throw new Error(`${label} route override differs from its active pin`);
    }
    return override.bytes;
  }
  return fetchBytes(
    new URL(reference.path, loaded.baseUrl),
    label,
    { sha256: reference.sha256, bytes: reference.bytes },
    loaded.allowLocalSelfSigned,
  );
}

function unique(values) {
  return [...new Set(values)];
}

async function captureFrame(
  page,
  output,
  label,
  camera,
  mood,
  activeDerivedSha256,
) {
  const transition = await page.evaluate(
    ({ camera, mood, viewport }) => {
      const map = window.__aeroBuildingCaptureMap;
      if (map === undefined)
        throw new Error("Instrumented production map is missing");
      map.previewPlaying = false;
      map.previewSeconds = 0;
      cancelAnimationFrame(map.previewAnimation);
      map.previewAnimation = 0;
      cancelAnimationFrame(map.hoverAnimation);
      map.hoverAnimation = 0;
      map.controls.enabled = false;
      map.controls.enableDamping = false;
      map.setCityMood(mood, false);
      map.camera.position.fromArray(camera.position);
      map.camera.up.fromArray(camera.up);
      map.controls.target.fromArray(camera.target);
      map.camera.fov = camera.fov_deg;
      map.camera.near = camera.near_m;
      map.camera.far = camera.far_m;
      map.camera.aspect = viewport.width / viewport.height;
      // Interactive OrbitControls clamp below-target views. The frozen C3
      // camera uses the same target without applying that input-only clamp.
      map.camera.lookAt(map.controls.target);
      map.camera.updateProjectionMatrix();
      map.focusSunShadow();
      map.renderStreamer.update(map.camera);
      map.recenterCityReflections();
      map.localReflections?.invalidate();
      const capturesBefore = Number(
        document.querySelector("#city-map").dataset.staticReflectionCaptures ??
          0,
      );
      const reflection = map.localReflections;
      const ids = (window.__aeroBuildingCaptureObjectIds ??= new WeakMap());
      let nextId = window.__aeroBuildingCaptureNextObjectId ?? 1;
      const identity = (value) => {
        if (value === null || value === undefined) return null;
        let result = ids.get(value);
        if (result === undefined) {
          result = `capture-object-${nextId++}`;
          ids.set(value, result);
        }
        return result;
      };
      const targetBefore = identity(reflection?.target);
      const filteredBefore = identity(reflection?.filtered);
      map.renderStaticFrame();
      const targetAfter = identity(reflection?.target);
      const filteredAfter = identity(reflection?.filtered);
      window.__aeroBuildingCaptureNextObjectId = nextId;
      return {
        captures_before: capturesBefore,
        captures_after: Number(
          document.querySelector("#city-map").dataset
            .staticReflectionCaptures ?? 0,
        ),
        selected_buildings: reflection?.selectedBuildings().length ?? 0,
        target_before: targetBefore,
        target_after: targetAfter,
        filtered_before: filteredBefore,
        filtered_after: filteredAfter,
        renderer_textures_after: map.renderer.info.memory.textures,
      };
    },
    { camera, mood, viewport: CAPTURE_VIEWPORT },
  );
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(resolve)),
  );
  const measured = await page.evaluate(
    async ({ camera, activeDerivedSha256 }) => {
      const map = window.__aeroBuildingCaptureMap;
      const subject = map.buildingPresentation.children.filter(
        (child) => child.userData.objectId === camera.subject_object_id,
      );
      if (subject.length !== 1) {
        throw new Error(
          `Expected one runtime subject ${camera.subject_object_id}, found ${subject.length}`,
        );
      }
      if (subject[0].userData.derivedGlbSha256 !== activeDerivedSha256) {
        throw new Error(
          `Runtime subject ${camera.subject_object_id} has the wrong derived GLB`,
        );
      }
      const materialRecords = [];
      let meshCount = 0;
      let runtimeTriangles = 0;
      subject[0].traverse((node) => {
        if (!node.isMesh) return;
        meshCount++;
        const geometry = node.geometry;
        runtimeTriangles += geometry.index
          ? geometry.index.count / 3
          : geometry.attributes.position.count / 3;
        const materials = Array.isArray(node.material)
          ? node.material
          : [node.material];
        for (const material of materials)
          materialRecords.push({
            name: material.name,
            type: material.type,
            role: material.userData?.cityBuildingMaterialRole ?? null,
            glass_facade: material.userData?.glassFacade === true,
            has_base_color_map: material.map != null,
            roughness: Number.isFinite(material.roughness)
              ? material.roughness
              : null,
            metalness: Number.isFinite(material.metalness)
              ? material.metalness
              : null,
          });
      });
      const cameraState = {
        position: map.camera.position.toArray(),
        target: map.controls.target.toArray(),
        up: map.camera.up.toArray(),
        quaternion: map.camera.quaternion.toArray(),
        projection: map.camera.projectionMatrix.toArray(),
        fov_deg: map.camera.fov,
        near_m: map.camera.near,
        far_m: map.camera.far,
        aspect: map.camera.aspect,
      };
      const close = (actual, expected) =>
        actual.length === expected.length &&
        actual.every(
          (value, index) => Math.abs(value - expected[index]) <= 1e-7,
        );
      if (
        !close(cameraState.position, camera.position) ||
        !close(cameraState.target, camera.target) ||
        !close(cameraState.up, camera.up) ||
        Math.abs(cameraState.fov_deg - camera.fov_deg) > 1e-7 ||
        Math.abs(cameraState.near_m - camera.near_m) > 1e-7 ||
        Math.abs(cameraState.far_m - camera.far_m) > 1e-7
      ) {
        throw new Error(`Frozen camera drifted for ${camera.id}`);
      }
      const gl = map.renderer.getContext();
      const submit = [],
        finished = [];
      for (let sample = 0; sample < 50; sample++) {
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const started = performance.now();
        map.renderStaticFrame();
        const submitted = performance.now();
        gl.finish();
        if (sample >= 10) {
          submit.push(submitted - started);
          finished.push(performance.now() - started);
        }
      }
      return {
        camera: cameraState,
        runtime_subject: {
          object_id: camera.subject_object_id,
          derived_glb_sha256: subject[0].userData.derivedGlbSha256,
          mesh_count: meshCount,
          triangles: runtimeTriangles,
          materials: materialRecords,
        },
        renderer: {
          draw_calls: map.renderer.info.render.calls,
          triangles: map.renderer.info.render.triangles,
          textures: map.renderer.info.memory.textures,
        },
        submit_values_ms: submit,
        finished_values_ms: finished,
        reflection_selected_buildings:
          map.localReflections?.selectedBuildings().length ?? 0,
        static_reflection_captures: Number(
          document.querySelector("#city-map").dataset
            .staticReflectionCaptures ?? 0,
        ),
      };
    },
    { camera, activeDerivedSha256 },
  );
  const captureDelta = transition.captures_after - transition.captures_before;
  const expectedCaptureDelta = expectedReflectionCaptureDelta(transition);
  if (
    captureDelta !== expectedCaptureDelta ||
    measured.static_reflection_captures !== transition.captures_after
  ) {
    throw new Error(
      `Reflection transition or steady timing drifted for ${camera.id}-${mood}: expected ${expectedCaptureDelta}, observed ${captureDelta}, selected ${transition.selected_buildings}, target ${transition.target_after}`,
    );
  }
  const screenshot = await page.locator("#city-map canvas").screenshot({
    path: resolve(output, "screenshots", `${label}-${camera.id}-${mood}.png`),
  });
  const cameraStateBytes = Buffer.from(JSON.stringify(measured.camera));
  const result = {
    transition_excluded_from_timing: {
      ...transition,
      expected_capture_delta: expectedCaptureDelta,
      observed_capture_delta: captureDelta,
    },
    ...measured,
    camera_state_sha256: sha256(cameraStateBytes),
    screenshot_sha256: sha256(screenshot),
    frame_samples: {
      submit: summarizeFrameSamples(measured.submit_values_ms),
      finished: summarizeFrameSamples(measured.finished_values_ms),
    },
  };
  delete result.submit_values_ms;
  delete result.finished_values_ms;
  return result;
}

export async function runBuildingCapture(options) {
  await mkdir(resolve(options.output, "screenshots"), { recursive: true });
  const sceneUrl = `${options.origin}/${BUILDING_SCENE_QUERY}`;
  const report = {
    schema: BUILDING_CAPTURE_REPORT_SCHEMA,
    label: options.label,
    origin: options.origin,
    scene_url: sceneUrl,
    transport_security: {
      tls_certificate_verification_bypassed:
        options.allowLocalSelfSigned === true,
      bypass_scope:
        options.allowLocalSelfSigned === true
          ? "explicit loopback HTTPS acceptance origin only"
          : null,
    },
    browser: {
      channel: "chromium",
      launch_args: [...HARDWARE_BROWSER_ARGS],
      software_rasterizer_disabled: true,
    },
    capture_method:
      "production renderer; fixed 1600x900 DPR1 viewport; frozen camera plan; one excluded reflection transition; 10 warmup + 40 gl.finish samples",
    source_asset_license:
      "unknown; user-authorized research assets; no licence conclusion is made",
    acceptance_status: "not-run",
    completed: false,
    console_errors: [],
    page_errors: [],
    request_failures: [],
  };
  let browser = null;
  let page = null;
  let failure = null;
  const requestFailures = new Set();
  try {
    const loaded = await loadPinnedManifest(
      options.origin,
      options.controlledBaseline,
      options.sourceAssets,
      options.allowLocalSelfSigned,
    );
    report.building_variant = loaded.variant;
    const activeSceneBytes = loaded.sceneOverrideBytes ?? loaded.sceneBytes;
    report.scene_document = {
      path: BUILDING_SCENE_PATH,
      served: {
        sha256: sha256(loaded.sceneBytes),
        bytes: loaded.sceneBytes.byteLength,
      },
      active: {
        sha256: sha256(activeSceneBytes),
        bytes: activeSceneBytes.byteLength,
        controlled_override: loaded.sceneOverrideBytes !== null,
      },
    };
    const generatedPlan = buildFrozenCameraPlan(
      loaded.manifest,
      loaded.manifestSha256,
    );
    const plan =
      options.cameraPlanPath === null
        ? generatedPlan
        : validateFrozenCameraPlan(
            JSON.parse(await readFile(options.cameraPlanPath, "utf8")),
            loaded.manifest,
            loaded.manifestSha256,
          );
    validateFrozenCameraPlan(plan, loaded.manifest, loaded.manifestSha256);
    if (
      plan.reference_manifest_sha256 !==
      loaded.variant.served_candidate_manifest_sha256
    ) {
      throw new Error(
        "Camera plan does not pin the active candidate building manifest",
      );
    }
    const planBytes = Buffer.from(`${JSON.stringify(plan, null, 2)}\n`);
    await writeFile(resolve(options.output, "camera-plan.json"), planBytes);
    report.camera_plan = {
      schema: plan.schema,
      sha256: sha256(planBytes),
      supplied: options.cameraPlanPath !== null,
      supplied_path: options.cameraPlanPath,
      reference_manifest_sha256: plan.reference_manifest_sha256,
      cameras: plan.cameras.length,
      moods: plan.moods,
    };
    report.manifest = {
      sha256: loaded.manifestSha256,
      scene_id: loaded.manifest.scene.id,
      buildings: loaded.manifest.buildings.length,
      objects_json_sha256: loaded.manifest.scene.objects_json_sha256,
    };
    const subjectIds = unique(
      plan.cameras.map((camera) => camera.subject_object_id),
    );
    report.geometry_measurements = [];
    for (const objectId of subjectIds) {
      const entry = loaded.manifest.buildings.find(
        (candidate) => candidate.object_id === objectId,
      );
      if (entry === undefined)
        throw new Error(`Missing capture subject ${objectId}`);
      const [sourceBytes, derivedBytes] = await Promise.all([
        readFile(resolve(options.sourceAssets, `${objectId}.glb`)),
        activeAssetBytes(loaded, entry.derived_glb, `${objectId} derived GLB`),
      ]);
      const measurement = measureSourceDerivedGeometry(
        entry,
        sourceBytes,
        derivedBytes,
      );
      if (!measurement.source_vs_derived_geometry_match) {
        throw new Error(
          `${objectId} source/derived geometry measurement failed`,
        );
      }
      report.geometry_measurements.push(measurement);
    }

    browser = await chromium.launch({
      channel: "chromium",
      headless: true,
      timeout: 60_000,
      args: [...HARDWARE_BROWSER_ARGS],
    });
    const context = await browser.newContext({
      viewport: CAPTURE_VIEWPORT,
      deviceScaleFactor: 1,
      ignoreHTTPSErrors: options.allowLocalSelfSigned,
    });
    page = await context.newPage();
    page.setDefaultTimeout(180_000);
    const overrideHits = new Map();
    const documentOverrideHits = new Map();
    let sceneOverrideHits = 0;
    const captureOrigin = new URL(options.origin).origin;
    if (loaded.sceneOverrideBytes !== null) {
      const assetPrefix = `${loaded.baseUrl.pathname}assets/`;
      await page.route(
        (url) =>
          url.origin === captureOrigin && url.pathname === BUILDING_SCENE_PATH,
        async (route) => {
          sceneOverrideHits++;
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: loaded.sceneOverrideBytes,
          });
        },
      );
      await page.route(
        (url) =>
          url.origin === captureOrigin && url.pathname.startsWith(assetPrefix),
        async (route) => {
          const url = new URL(route.request().url());
          const digest = url.pathname.slice(assetPrefix.length);
          const asset = loaded.overrideAssets.get(digest);
          if (asset === undefined) {
            await route.continue();
            return;
          }
          overrideHits.set(digest, (overrideHits.get(digest) ?? 0) + 1);
          await route.fulfill({
            status: 200,
            contentType: asset.mediaType,
            body: asset.bytes,
          });
        },
      );
      await page.route(
        (url) => loaded.overrideDocuments.has(url.href),
        async (route) => {
          const url = route.request().url();
          const document = loaded.overrideDocuments.get(url);
          if (document === undefined) {
            throw new Error(`Missing controlled document override for ${url}`);
          }
          documentOverrideHits.set(
            url,
            (documentOverrideHits.get(url) ?? 0) + 1,
          );
          await route.fulfill({
            status: 200,
            contentType: document.mediaType,
            body: document.bytes,
          });
        },
      );
    }
    const hooks = [];
    await page.route(/\/assets\/app-[^/]+\.js(?:\?.*)?$/, async (route) => {
      const response = await route.fetch();
      const instrumented = instrumentAppBundle(await response.text());
      hooks.push({
        url: route.request().url(),
        source_sha256: instrumented.sourceSha256,
        instrumented_sha256: instrumented.instrumentedSha256,
        match_count: instrumented.matchCount,
      });
      await route.fulfill({ response, body: instrumented.source });
    });
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error")
        report.console_errors.push(message.text());
    });
    page.on("requestfailed", (request) =>
      requestFailures.add(
        `${request.url()} ${request.failure()?.errorText ?? ""}`.trim(),
      ),
    );

    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    const requestUrls = new Map();
    const loads = [];
    const startedAt = performance.now();
    cdp.on("Network.requestWillBeSent", (parameters) => {
      if (!parameters.request.url.startsWith("data:")) {
        requestUrls.set(parameters.requestId, parameters.request.url);
      }
    });
    cdp.on("Network.loadingFailed", (parameters) => {
      const url = requestUrls.get(parameters.requestId) ?? "";
      if (!url.startsWith("data:"))
        requestFailures.add(`${url} ${parameters.errorText}`.trim());
    });
    cdp.on("Network.loadingFinished", (parameters) => {
      const url = requestUrls.get(parameters.requestId);
      if (url !== undefined)
        loads.push({
          at_ms: Math.round(performance.now() - startedAt),
          url,
          encoded_bytes: parameters.encodedDataLength,
        });
    });

    await page.goto(sceneUrl, {
      waitUntil: "domcontentloaded",
      timeout: 180_000,
    });
    await page.waitForFunction(
      () => {
        const data = document.querySelector("#city-map")?.dataset;
        return data?.sceneReady === "true" || data?.sceneError === "true";
      },
      undefined,
      { timeout: 180_000 },
    );
    report.t_scene_ready_ms = Math.round(performance.now() - startedAt);
    report.scene_ready = await page
      .locator("#city-map")
      .evaluate((root) => ({ ...root.dataset }));
    if (report.scene_ready.sceneError === "true") {
      throw new Error(report.scene_ready.sceneErrorMessage ?? "Scene failed");
    }
    await page.waitForFunction(
      (expected) => {
        const data = document.querySelector("#city-map")?.dataset;
        if (data?.sceneError === "true") return true;
        if (Number(data?.buildingRenderFailed ?? 0) > 0) return true;
        const map = window.__aeroBuildingCaptureMap;
        return (
          data?.texturesReady === "true" &&
          data?.skyReady === "true" &&
          Number(data?.buildingRenderLoaded ?? 0) === expected &&
          Number(data?.buildingRenderFailed ?? 0) === 0 &&
          map?.renderStreamer?.progress.active === 0
        );
      },
      loaded.manifest.buildings.length,
      { timeout: 600_000 },
    );
    await page
      .locator("#city-map .city-scene-loading")
      .waitFor({ state: "hidden", timeout: 180_000 });
    report.t_all_buildings_terminal_ms = Math.round(
      performance.now() - startedAt,
    );
    report.scene_terminal = await page
      .locator("#city-map")
      .evaluate((root) => ({ ...root.dataset }));
    if (report.scene_terminal.sceneError === "true") {
      throw new Error(
        report.scene_terminal.sceneErrorMessage ?? "Scene failed",
      );
    }
    if (
      Number(report.scene_terminal.buildingRenderLoaded) !==
        loaded.manifest.buildings.length ||
      Number(report.scene_terminal.buildingRenderFailed) !== 0
    ) {
      throw new Error(
        "Every declared building render must load before C3 capture",
      );
    }
    report.private_route_overrides = {
      enabled: loaded.sceneOverrideBytes !== null,
      scene_hits: sceneOverrideHits,
      asset_hits: [...loaded.overrideAssets.keys()]
        .map((digest) => ({
          sha256: digest,
          hits: overrideHits.get(digest) ?? 0,
        }))
        .sort((left, right) => left.sha256.localeCompare(right.sha256)),
      document_hits: [...loaded.overrideDocuments]
        .map(([url]) => ({
          url: new URL(url).pathname,
          hits: documentOverrideHits.get(url) ?? 0,
        }))
        .sort((left, right) => left.url.localeCompare(right.url)),
    };
    if (
      loaded.sceneOverrideBytes !== null &&
      (sceneOverrideHits !== 1 ||
        report.private_route_overrides.asset_hits.some(
          (asset) => asset.hits < 1,
        ) ||
        report.private_route_overrides.document_hits.some(
          (document) => document.hits < 1,
        ))
    ) {
      throw new Error(
        "Controlled baseline route overrides were not all consumed",
      );
    }
    if (hooks.length !== 1) {
      throw new Error(
        `Expected one instrumented app bundle, observed ${hooks.length}`,
      );
    }
    report.bundle_instrumentation = hooks[0];

    report.renderer_query = await page.evaluate(
      ({ viewport }) => {
        const root = document.querySelector("#city-map");
        const map = window.__aeroBuildingCaptureMap;
        if (root === null || map === undefined || map.root !== root) {
          throw new Error("Production map/root identity check failed");
        }
        const canvas = root.querySelector("canvas");
        if (canvas === null || map.renderer.domElement !== canvas) {
          throw new Error("Production renderer/canvas identity check failed");
        }
        root.style.position = "fixed";
        root.style.inset = "0";
        root.style.width = `${viewport.width}px`;
        root.style.height = `${viewport.height}px`;
        root.style.zIndex = "2147483647";
        map.renderer.setPixelRatio(1);
        map.renderer.setSize(viewport.width, viewport.height, false);
        map.camera.aspect = viewport.width / viewport.height;
        map.camera.updateProjectionMatrix();
        const gl = map.renderer.getContext();
        const debug = gl.getExtension("WEBGL_debug_renderer_info");
        return {
          root_id: root.id,
          exact_map_root_identity: true,
          exact_renderer_canvas_identity: true,
          render_backend: root.dataset.renderBackend,
          webgl_vendor: String(
            gl.getParameter(debug?.UNMASKED_VENDOR_WEBGL ?? gl.VENDOR),
          ),
          webgl_renderer: String(
            gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER),
          ),
          webgl_version: String(gl.getParameter(gl.VERSION)),
          canvas_buffer: [canvas.width, canvas.height],
          canvas_css: [canvas.clientWidth, canvas.clientHeight],
          manifest_scene_id: map.renderStreamer?.manifest.scene.id,
          streamer_progress: { ...map.renderStreamer?.progress },
          building_group_children: map.buildingPresentation?.children.length,
          local_reflection_owners: map.localReflections === null ? 0 : 1,
          reflections_enabled:
            map.workspaceConfig?.environment.reflectionsEnabled ??
            map.presentationOptions.reflectionsEnabled ??
            true,
          reflection_profile: { ...map.localReflections?.constructor.profile },
          textures_ready: root.dataset.texturesReady === "true",
          sky_ready: root.dataset.skyReady === "true",
          loading_overlay_hidden:
            root.querySelector(".city-scene-loading")?.hidden === true,
        };
      },
      { viewport: CAPTURE_VIEWPORT },
    );
    if (
      report.renderer_query.canvas_buffer[0] !== CAPTURE_VIEWPORT.width ||
      report.renderer_query.canvas_buffer[1] !== CAPTURE_VIEWPORT.height ||
      report.renderer_query.canvas_css[0] !== CAPTURE_VIEWPORT.width ||
      report.renderer_query.canvas_css[1] !== CAPTURE_VIEWPORT.height
    ) {
      throw new Error("C3 renderer viewport is not fixed at 1600x900 DPR1");
    }
    if (
      report.renderer_query.manifest_scene_id !== loaded.manifest.scene.id ||
      report.renderer_query.textures_ready !== true ||
      report.renderer_query.sky_ready !== true ||
      report.renderer_query.loading_overlay_hidden !== true ||
      report.renderer_query.streamer_progress.total !==
        loaded.manifest.buildings.length ||
      report.renderer_query.streamer_progress.active !== 0 ||
      report.renderer_query.streamer_progress.loaded !==
        loaded.manifest.buildings.length ||
      report.renderer_query.streamer_progress.failed !== 0 ||
      report.renderer_query.building_group_children !==
        loaded.manifest.buildings.length
    ) {
      throw new Error("C3 scene assets are not fully loaded and idle");
    }
    if (
      report.renderer_query.local_reflection_owners !== 1 ||
      report.renderer_query.reflections_enabled !== true ||
      JSON.stringify(report.renderer_query.reflection_profile) !==
        JSON.stringify(EXPECTED_REFLECTION_PROFILE)
    ) {
      throw new Error("C3 production reflection owner or profile has changed");
    }
    if (
      report.renderer_query.render_backend !== "hardware" &&
      !options.allowSoftware
    ) {
      throw new Error(
        "C3 acceptance requires the hardware renderer; use --allow-software only for diagnostics",
      );
    }

    const readyMs = report.t_scene_ready_ms;
    const isRender = (url) => url.includes("/building-renders/");
    const sum = (filter, until = Number.POSITIVE_INFINITY) =>
      loads
        .filter((entry) => entry.at_ms <= until && filter(entry.url))
        .reduce((total, entry) => total + entry.encoded_bytes, 0);
    const renderLoads = loads.filter((entry) => isRender(entry.url));
    report.network = {
      requests_total: loads.length,
      encoded_bytes_total: sum(() => true),
      encoded_bytes_until_scene_ready: sum(() => true, readyMs),
      building_render_requests: renderLoads.length,
      building_render_encoded_bytes: sum(isRender),
      building_render_encoded_bytes_until_scene_ready: sum(isRender, readyMs),
      building_render_last_finished_at_ms: renderLoads.reduce(
        (latest, entry) => Math.max(latest, entry.at_ms),
        0,
      ),
    };

    report.frames = {};
    for (const camera of plan.cameras)
      for (const mood of plan.moods) {
        const name = `${camera.id}-${mood}`;
        const activeEntry = loaded.manifest.buildings.find(
          (entry) => entry.object_id === camera.subject_object_id,
        );
        if (activeEntry === undefined) {
          throw new Error(
            `Missing active capture subject ${camera.subject_object_id}`,
          );
        }
        report.frames[name] = await captureFrame(
          page,
          options.output,
          options.label,
          camera,
          mood,
          activeEntry.derived_glb.sha256,
        );
      }
    report.terminal_state = await page
      .locator("#city-map")
      .evaluate((root) => ({ ...root.dataset }));
    recordBrowserRequestFailures(report, requestFailures, options.origin);
    if (
      report.page_errors.length > 0 ||
      report.console_errors.length > 0 ||
      report.request_failures.length > 0
    ) {
      throw new Error("Browser diagnostics contain errors or failed requests");
    }
    if (options.matchedReportPath != null) {
      const referenceBytes = await readFile(options.matchedReportPath);
      report.matched_reference = {
        path: options.matchedReportPath,
        sha256: sha256(referenceBytes),
        bytes: referenceBytes.byteLength,
        ...verifyMatchedCaptureReports(
          JSON.parse(referenceBytes.toString("utf8")),
          report,
        ),
      };
    }
    report.completed = true;
    report.acceptance_status =
      report.renderer_query.render_backend === "hardware"
        ? loaded.sceneOverrideBytes === null
          ? options.matchedReportPath == null
            ? "capture-complete-pending-matched-a-b-human-review"
            : "matched-runtime-capture-complete-pending-human-review"
          : "controlled-baseline-capture-complete-pending-matched-a-b-human-review"
        : "diagnostic-only-software-renderer";
    await context.close();
  } catch (cause) {
    failure = cause;
    report.failure = cause instanceof Error ? cause.message : String(cause);
    if (page !== null && !page.isClosed()) {
      try {
        report.failure_state = await page
          .locator("#city-map")
          .evaluate((root) => ({
            dataset: { ...root.dataset },
            streamer_progress: {
              ...window.__aeroBuildingCaptureMap?.renderStreamer?.progress,
            },
          }));
      } catch (diagnosticCause) {
        report.failure_state_error =
          diagnosticCause instanceof Error
            ? diagnosticCause.message
            : String(diagnosticCause);
      }
    }
    recordBrowserRequestFailures(report, requestFailures, options.origin);
    report.acceptance_status = "failed-or-incomplete";
  } finally {
    await browser?.close();
    await writeFile(
      resolve(options.output, "capture-report.json"),
      `${JSON.stringify(report, null, 2)}\n`,
    );
  }
  if (failure !== null) throw failure;
  return report;
}

const invokedPath =
  process.argv[1] === undefined
    ? null
    : pathToFileURL(resolve(process.argv[1])).href;
if (invokedPath === import.meta.url) {
  try {
    const report = await runBuildingCapture(
      parseCaptureArguments(process.argv.slice(2)),
    );
    console.log(
      `CAPTURE COMPLETE (${report.acceptance_status}); review matched A/B frames before acceptance`,
    );
  } catch (cause) {
    console.error(cause instanceof Error ? cause.message : String(cause));
    process.exitCode = 2;
  }
}
