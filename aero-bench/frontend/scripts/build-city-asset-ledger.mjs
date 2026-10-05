#!/usr/bin/env node

/**
 * Build a review candidate for the city GLB asset ledger.
 *
 * This program inventories current files. It does not establish a licence and it
 * never publishes source archives. The independent audit program is responsible
 * for detecting files or metadata that drift after this candidate is written.
 */
import { createHash } from "node:crypto";
import { createReadStream, existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const LEDGER_SCHEMA = "aero-bench.city-asset-ledger/v2";

const BUILDING_RENDER_ROOT = "frontend/public/building-renders";
const DEFAULT_OUTPUT = "frontend/asset-library-ledger.json";
const UNKNOWN_LICENSE_NOTE =
  "No licence declaration was found in the referenced project metadata; redistribution rights are not asserted.";

const METADATA_PATHS = [
  "frontend/public/models/uav/conversion-inventory.json",
  "frontend/public/models/uav/cad-conversion-report.json",
  "frontend/scripts/uav-preview-models.json",
  "frontend/scripts/uav-mesh-previews.json",
  "frontend/assets/incoming/quark/uav/curated.json",
  "frontend/public/models/incoming/furniture/manifest.json",
  "frontend/public/models/incoming/furniture/source-inventory.json",
  "frontend/public/models/incoming/manifest.json",
  "frontend/scripts/incoming-reviewed-models.json",
  "frontend/public/models/city-runtime/conversion-report.json",
  "frontend/public/models/city-runtime/uav-lod-report.json",
];

function posix(path) {
  return path.split(sep).join("/");
}

function repoPath(repoRoot, absolutePath) {
  const value = posix(relative(repoRoot, absolutePath));
  if (value === "" || value === ".." || value.startsWith("../")) {
    throw new Error(`Path is outside the repository: ${absolutePath}`);
  }
  return value;
}

function json(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function jsonIfPresent(repoRoot, path) {
  const absolute = join(repoRoot, path);
  return existsSync(absolute) ? json(absolute) : null;
}

function filesBelow(root, accept = () => true) {
  if (!existsSync(root)) return [];
  const found = [];
  const visit = directory => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile() && accept(path)) found.push(path);
    }
  };
  visit(root);
  return found.sort((left, right) => posix(left).localeCompare(posix(right)));
}

async function sha256(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

async function identity(repoRoot, path) {
  return {
    path: repoPath(repoRoot, path),
    sha256: await sha256(path),
    bytes: statSync(path).size,
  };
}

function pointerReference(path, pointer, fields = {}) {
  return { metadata_path: path, pointer, ...fields };
}

function addReference(map, path, status, reference) {
  const current = map.get(path) ?? { status, references: [] };
  if (current.status === "unknown" || status === "partial") current.status = status;
  current.references.push(reference);
  map.set(path, current);
}

function buildSourceIndex(repoRoot) {
  const sources = new Map();

  const inventoryPath = "frontend/public/models/uav/conversion-inventory.json";
  const inventory = jsonIfPresent(repoRoot, inventoryPath);
  for (const [index, entry] of (inventory?.entries ?? []).entries()) {
    for (const model of entry.models ?? []) {
      const path = model.path?.startsWith("frontend/") ? model.path : `frontend/${model.path}`;
      addReference(sources, path, entry.source_completeness === "verified" ? "documented" : "partial",
        pointerReference(inventoryPath, `/entries/${index}`, {
          relation: "conversion-inventory",
          source_archive: entry.archive ?? null,
          selected_member: entry.selected_member ?? null,
          model_origin: model.origin ?? null,
          source_completeness: entry.source_completeness ?? "unknown",
        }));
    }
  }

  const cadPath = "frontend/public/models/uav/cad-conversion-report.json";
  const cad = jsonIfPresent(repoRoot, cadPath);
  for (const [index, entry] of (cad?.entries ?? []).entries()) {
    if (!entry.model_path) continue;
    const path = entry.model_path.startsWith("frontend/") ? entry.model_path : `frontend/${entry.model_path}`;
    addReference(sources, path, "partial", pointerReference(cadPath, `/entries/${index}`, {
      relation: "conversion-report",
      source_archive: entry.archive ?? null,
      selected_member: entry.selected_member ?? null,
      source_selection: entry.source_selection ?? null,
    }));
  }

  const previewPath = "frontend/scripts/uav-preview-models.json";
  const previews = jsonIfPresent(repoRoot, previewPath) ?? [];
  for (const [index, entry] of previews.entries()) {
    if (!entry.output) continue;
    addReference(sources, `frontend/public/models/uav/${entry.output}`, "partial",
      pointerReference(previewPath, `/${index}`, {
        relation: "preview-source",
        source_archive: entry.archive ?? null,
        selected_member: entry.member ?? entry.source_3dxml ?? null,
      }));
  }

  const meshPath = "frontend/scripts/uav-mesh-previews.json";
  const meshes = jsonIfPresent(repoRoot, meshPath) ?? [];
  for (const [index, entry] of meshes.entries()) {
    if (!entry.output) continue;
    addReference(sources, `frontend/public/models/uav/mesh/${entry.output}`, "partial",
      pointerReference(meshPath, `/${index}`, {
        relation: "mesh-preview-source",
        source_archive: entry.archive ?? null,
        selected_member: entry.source_member ?? null,
      }));
  }

  const curatedPath = "frontend/assets/incoming/quark/uav/curated.json";
  const curated = jsonIfPresent(repoRoot, curatedPath);
  if (curated?.web_model) {
    const path = curated.web_model.startsWith("frontend/") ? curated.web_model : `frontend/${curated.web_model}`;
    addReference(sources, path, "documented", pointerReference(curatedPath, "", {
      relation: "curated-source",
      source_archive: curated.source_archive ?? null,
      source_archive_sha256: curated.source_archive_sha256 ?? null,
      selected_member: curated.source_member ?? null,
      source_share: curated.source_share ?? null,
    }));
  }

  const furniturePath = "frontend/public/models/incoming/furniture/manifest.json";
  const furniture = jsonIfPresent(repoRoot, furniturePath);
  const furnitureInventoryPath = "frontend/public/models/incoming/furniture/source-inventory.json";
  const furnitureInventory = jsonIfPresent(repoRoot, furnitureInventoryPath);
  const furnitureInventoryById = new Map((furnitureInventory?.assets ?? []).map((entry, index) => [entry.id, index]));
  for (const [index, entry] of (furniture?.entries ?? []).entries()) {
    if (!entry.source_path?.toLowerCase().endsWith(".glb")) continue;
    const path = entry.source_path.startsWith("frontend/") ? entry.source_path : `frontend/${entry.source_path}`;
    addReference(sources, path, "documented", pointerReference(furniturePath, `/entries/${index}`, {
      relation: "converted-furniture-source",
      source_archive: furniture.source_archive ?? null,
      origin_path: entry.origin_path ?? null,
    }));
    const assetId = entry.id?.split(":").at(-1);
    if (furnitureInventoryById.has(assetId)) {
      addReference(sources, path, "documented",
        pointerReference(furnitureInventoryPath, `/assets/${furnitureInventoryById.get(assetId)}`, {
          relation: "source-inventory",
        }));
    }
  }

  const incomingPath = "frontend/public/models/incoming/manifest.json";
  const incoming = jsonIfPresent(repoRoot, incomingPath);
  for (const [index, entry] of (incoming?.entries ?? []).entries()) {
    if (!entry.source_path?.toLowerCase().endsWith(".glb")) continue;
    const path = entry.source_path.startsWith("frontend/") ? entry.source_path : `frontend/${entry.source_path}`;
    addReference(sources, path, "documented", pointerReference(incomingPath, `/entries/${index}`, {
      relation: "reviewed-conversion-source",
      origin_path: entry.origin_path ?? null,
      package_path: entry.package_path ?? null,
    }));
  }
  const reviewedPath = "frontend/scripts/incoming-reviewed-models.json";
  const reviewed = jsonIfPresent(repoRoot, reviewedPath) ?? [];
  for (const [index, entry] of reviewed.entries()) {
    if (!entry.source_path?.toLowerCase().endsWith(".glb")) continue;
    const path = entry.source_path.startsWith("frontend/") ? entry.source_path : `frontend/${entry.source_path}`;
    addReference(sources, path, "documented",
      pointerReference(reviewedPath, `/${index}`, { relation: "review-approval" }));
  }

  const conversionPath = "frontend/public/models/city-runtime/conversion-report.json";
  const conversion = jsonIfPresent(repoRoot, conversionPath) ?? [];
  for (const [index, entry] of conversion.entries()) {
    if (!entry.model) continue;
    const path = `frontend/public/models/city-runtime/${entry.model}`;
    const sourceRoot = ["Car_6-preview.glb", "Taxi_1-day-preview.glb", "Police_1-day-preview.glb",
      "Bicycle_Man_34-bike-only.glb"].includes(entry.model)
      ? "frontend/public/models/incoming/urban-traffic/glb"
      : entry.model === "casual27_m_highpoly_walk.glb"
        ? "frontend/public/models/incoming/citizens/glb"
        : "frontend/public/models/uav";
    addReference(sources, path, "documented", pointerReference(conversionPath, `/${index}`, {
      relation: "runtime-optimization",
      source_path: `${sourceRoot}/${entry.model}`,
      source_sha256: entry.source_sha256 ?? null,
      source_bytes: entry.source_bytes ?? null,
    }));
  }

  const lodPath = "frontend/public/models/city-runtime/uav-lod-report.json";
  const lod = jsonIfPresent(repoRoot, lodPath);
  for (const [index, entry] of (lod?.models ?? []).entries()) {
    if (entry.source) {
      addReference(sources, `frontend/public/models/city-runtime/${entry.source}`, "documented",
        pointerReference(lodPath, `/models/${index}`, {
          relation: "lod-source",
          source_sha256: entry.source_sha256 ?? null,
          source_bytes: entry.source_bytes ?? null,
        }));
    }
    if (entry.lod) {
      addReference(sources, `frontend/public/models/city-runtime/${entry.lod}`, "documented",
        pointerReference(lodPath, `/models/${index}`, {
          relation: "generated-lod",
          source_path: `frontend/public/models/city-runtime/${entry.source}`,
          source_sha256: entry.source_sha256 ?? null,
          source_bytes: entry.source_bytes ?? null,
        }));
    }
  }
  return sources;
}

function lineNumber(text, index) {
  let line = 1;
  for (let cursor = 0; cursor < index; cursor++) if (text.charCodeAt(cursor) === 10) line++;
  return line;
}

function buildActiveUseIndex(repoRoot) {
  const uses = new Map();
  const add = (url, reference) => {
    const list = uses.get(url) ?? [];
    if (!list.some(item => JSON.stringify(item) === JSON.stringify(reference))) list.push(reference);
    uses.set(url, list);
  };
  const sourceRoot = join(repoRoot, "frontend/src");
  const sceneRoot = join(repoRoot, "frontend/public/city-presentation");
  const candidates = [
    ...filesBelow(sourceRoot, path => extname(path) === ".ts" && !path.endsWith(".test.ts")),
    ...filesBelow(sceneRoot, path => extname(path) === ".json"),
  ];
  const pattern = /\/models\/[A-Za-z0-9_./-]+\.glb/g;
  for (const path of candidates) {
    const text = readFileSync(path, "utf8");
    for (const match of text.matchAll(pattern)) {
      add(match[0], {
        kind: path.endsWith(".json") ? "scene-data" : "runtime-code",
        reference_path: repoPath(repoRoot, path),
        line: lineNumber(text, match.index),
      });
    }
  }
  return uses;
}

function isAssetLibraryModel(path) {
  return path.startsWith("frontend/public/models/uav/cad/")
    || path.startsWith("frontend/public/models/uav/mesh/")
    || (path.startsWith("frontend/public/models/uav/") && path.endsWith("-preview.glb"))
    || path.startsWith("frontend/public/models/incoming/");
}

function publicUrl(path) {
  const prefix = "frontend/public";
  if (!path.startsWith(`${prefix}/`)) throw new Error(`Public asset path is outside ${prefix}: ${path}`);
  return path.slice(prefix.length);
}

function useForModel(path, activeUses) {
  const url = publicUrl(path);
  const references = [...(activeUses.get(url) ?? [])];
  if (isAssetLibraryModel(path)) {
    references.push({
      kind: "asset-library-preview",
      reference_path: "frontend/scripts/asset-library.mjs",
      detail: "The generated asset-library catalog exposes this GLB as a browser model.",
    });
  }
  if (references.length > 0) return { status: "active", references };
  return {
    status: "inactive",
    references: [],
    reason: "No production TypeScript, city-presentation JSON, or generated asset-library entry references this public GLB.",
  };
}

function licenceUnknown() {
  return { status: "unknown", expression: null, references: [], note: UNKNOWN_LICENSE_NOTE };
}

function sourceFor(path, sourceIndex) {
  return sourceIndex.get(path) ?? {
    status: "unknown",
    references: [],
    note: "No provenance record for this GLB was found in the inspected project metadata.",
  };
}

async function buildModelRows(repoRoot, publicModelsRoot, sourceIndex, activeUses) {
  const files = filesBelow(publicModelsRoot, path => extname(path).toLowerCase() === ".glb");
  const entries = [];
  for (const absolutePath of files) {
    const path = repoPath(repoRoot, absolutePath);
    entries.push({
      path,
      public_url: publicUrl(path),
      sha256: await sha256(absolutePath),
      bytes: statSync(absolutePath).size,
      source: sourceFor(path, sourceIndex),
      license: licenceUnknown(),
      use: useForModel(path, activeUses),
    });
  }
  return entries;
}

async function buildBuildingRows(repoRoot, manifestPath, manifest) {
  if (!Array.isArray(manifest.buildings)) throw new Error("Building runtime manifest has no buildings array");
  const sceneRoot = dirname(manifestPath);
  const sceneId = manifest.scene?.id;
  if (typeof sceneId !== "string" || sceneId.length === 0) {
    throw new Error(`Building runtime manifest has no scene.id: ${manifestPath}`);
  }
  const rows = [];
  for (const [index, building] of manifest.buildings.entries()) {
    if (!building.object_id || !building.derived_glb?.path) {
      throw new Error(`Invalid building runtime row at /buildings/${index}`);
    }
    const absolutePath = join(sceneRoot, building.derived_glb.path);
    if (!existsSync(absolutePath)) throw new Error(`Building derived GLB is missing: ${absolutePath}`);
    const path = repoPath(repoRoot, absolutePath);
    rows.push({
      scene_id: sceneId,
      object_id: building.object_id,
      path,
      public_url: publicUrl(path),
      sha256: await sha256(absolutePath),
      bytes: statSync(absolutePath).size,
      source: {
        status: "documented",
        canonical_glb: {
          manifest_declared_path: building.glb?.path ?? null,
          sha256: building.glb?.sha256 ?? null,
          bytes: building.glb?.bytes ?? null,
          publication: "source-only-not-served",
        },
        references: [pointerReference(repoPath(repoRoot, manifestPath), `/buildings/${index}`, {
          relation: "verified-derived-building-glb",
        })],
      },
      license: licenceUnknown(),
      use: {
        status: "active",
        references: [{
          kind: "building-render-runtime",
          reference_path: "frontend/src/city-building-renders.ts",
          detail: "The verified building streamer loads derived_glb records from the scene manifest.",
        }],
      },
    });
  }
  return { sceneId, manifestPath, rows };
}

function discoverBuildingManifestPaths(repoRoot) {
  const root = join(repoRoot, BUILDING_RENDER_ROOT);
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => join(root, entry.name, "manifest.json"))
    .filter(path => existsSync(path) && statSync(path).isFile())
    .sort((left, right) => posix(left).localeCompare(posix(right)));
}

export async function buildCityAssetLedger(options = {}) {
  const scriptRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const repoRoot = resolve(options.repoRoot ?? scriptRoot);
  const publicModelsRoot = resolve(options.publicModelsRoot ?? join(repoRoot, "frontend/public/models"));
  const buildingManifestPaths = (options.buildingManifestPaths ?? discoverBuildingManifestPaths(repoRoot))
    .map(path => resolve(path))
    .sort((left, right) => posix(left).localeCompare(posix(right)));
  if (!existsSync(publicModelsRoot)) throw new Error(`Public model root is missing: ${publicModelsRoot}`);
  if (buildingManifestPaths.length === 0) throw new Error("No published building runtime manifests were found");
  if (new Set(buildingManifestPaths).size !== buildingManifestPaths.length) {
    throw new Error("Building runtime manifest paths must be unique");
  }
  for (const path of buildingManifestPaths) {
    if (!existsSync(path)) throw new Error(`Building runtime manifest is missing: ${path}`);
  }

  const sourceIndex = buildSourceIndex(repoRoot);
  const activeUses = buildActiveUseIndex(repoRoot);
  const entries = await buildModelRows(repoRoot, publicModelsRoot, sourceIndex, activeUses);
  const buildingScenes = [];
  const sceneIds = new Set();
  const buildingPaths = new Set();
  for (const manifestPath of buildingManifestPaths) {
    const scene = await buildBuildingRows(repoRoot, manifestPath, json(manifestPath));
    if (sceneIds.has(scene.sceneId)) throw new Error(`Duplicate building runtime scene.id: ${scene.sceneId}`);
    sceneIds.add(scene.sceneId);
    for (const row of scene.rows) {
      if (buildingPaths.has(row.path)) throw new Error(`Duplicate building runtime asset path: ${row.path}`);
      buildingPaths.add(row.path);
    }
    buildingScenes.push(scene);
  }
  const buildingRuntime = buildingScenes.flatMap(scene => scene.rows);
  const metadataPaths = [...METADATA_PATHS, ...buildingManifestPaths.map(path => repoPath(repoRoot, path))]
    .filter((path, index, all) => all.indexOf(path) === index && existsSync(join(repoRoot, path)));
  const metadataSources = [];
  for (const path of metadataPaths) metadataSources.push(await identity(repoRoot, join(repoRoot, path)));

  const all = [...entries, ...buildingRuntime];
  const active = all.filter(entry => entry.use.status === "active").length;
  const documented = all.filter(entry => entry.source.status === "documented").length;
  const partial = all.filter(entry => entry.source.status === "partial").length;
  const unknownSource = all.filter(entry => entry.source.status === "unknown").length;
  const publicModelBytes = entries.reduce((sum, entry) => sum + entry.bytes, 0);
  const buildingBytes = buildingRuntime.reduce((sum, entry) => sum + entry.bytes, 0);
  return {
    schema_version: LEDGER_SCHEMA,
    generated_by: "frontend/scripts/build-city-asset-ledger.mjs",
    scope: {
      public_model_glbs: "Every *.glb below frontend/public/models.",
      building_runtime_glbs:
        "Every extensionless derived_glb declared by every published building runtime manifest; canonical source GLBs remain outside public serving.",
      archives: "Source archives are provenance references only and are never public URLs.",
    },
    authorization_basis: {
      status: "user-declared",
      purpose: "mentor-authorized internal research",
      confirmed_date_utc: "2026-10-01",
      evidence: "Confirmed by the user in the current project session.",
      original_license_unchanged: true,
      public_redistribution_authorized: false,
      note: "This internal-research authorization does not supply an absent original licence or authorize public redistribution.",
    },
    roots: {
      public_models: repoPath(repoRoot, publicModelsRoot),
      building_runtime_manifests: buildingManifestPaths.map(path => repoPath(repoRoot, path)),
    },
    metadata_sources: metadataSources,
    entries,
    building_runtime: buildingRuntime,
    summary: {
      public_model_glbs: entries.length,
      public_model_bytes: publicModelBytes,
      building_runtime_glbs: buildingRuntime.length,
      building_runtime_bytes: buildingBytes,
      building_runtime_scenes: buildingScenes.length,
      building_runtime_by_scene: buildingScenes.map(scene => ({
        scene_id: scene.sceneId,
        manifest_path: repoPath(repoRoot, scene.manifestPath),
        glbs: scene.rows.length,
        bytes: scene.rows.reduce((sum, row) => sum + row.bytes, 0),
      })),
      total_glbs: all.length,
      active_uses: active,
      inactive_or_unreferenced: all.length - active,
      source_documented: documented,
      source_partial: partial,
      source_unknown: unknownSource,
      license_declared: 0,
      license_unknown: all.length,
    },
  };
}

function args(argv) {
  const parsed = {};
  for (const argument of argv) {
    const match = /^--([^=]+)=(.*)$/.exec(argument);
    if (!match) throw new Error(`Expected --name=value, received ${argument}`);
    parsed[match[1]] = match[2];
  }
  return parsed;
}

async function main() {
  const parsed = args(process.argv.slice(2));
  const repoRoot = resolve(parsed["repo-root"] ?? resolve(dirname(fileURLToPath(import.meta.url)), "../.."));
  const output = resolve(repoRoot, parsed.output ?? DEFAULT_OUTPUT);
  const ledger = await buildCityAssetLedger({
    repoRoot,
    publicModelsRoot: parsed["public-models-root"]
      ? resolve(repoRoot, parsed["public-models-root"])
      : undefined,
    buildingManifestPaths: parsed["building-manifests"]
      ? parsed["building-manifests"].split(",").filter(Boolean).map(path => resolve(repoRoot, path))
      : undefined,
  });
  await mkdir(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(ledger, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ output: repoPath(repoRoot, output), summary: ledger.summary }, null, 2)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
    process.exitCode = 1;
  });
}
