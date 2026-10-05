#!/usr/bin/env node

/** Independently audit a generated city GLB asset ledger against current bytes. */
import { createHash } from "node:crypto";
import { createReadStream, existsSync, openSync, closeSync, readFileSync, readSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const LEDGER_SCHEMA = "aero-bench.city-asset-ledger/v2";
const BUILDING_RENDER_ROOT = "frontend/public/building-renders";
const DEFAULT_LEDGER = "frontend/asset-library-ledger.json";
const DEFAULT_REPORT = "validation/codex-takeover-20261001/F/asset-ledger/audit-report.json";
const SHA256 = /^[0-9a-f]{64}$/;
const SOURCE_STATUSES = new Set(["documented", "partial", "unknown"]);
const LICENSE_STATUSES = new Set(["declared", "unknown"]);
const USE_STATUSES = new Set(["active", "inactive"]);

function posix(path) {
  return path.split(sep).join("/");
}

function repoPath(repoRoot, absolutePath) {
  return posix(relative(repoRoot, absolutePath));
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

function discoverBuildingManifestPaths(repoRoot) {
  const root = join(repoRoot, BUILDING_RENDER_ROOT);
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => join(root, entry.name, "manifest.json"))
    .filter(path => existsSync(path) && statSync(path).isFile())
    .sort((left, right) => posix(left).localeCompare(posix(right)));
}

async function sha256(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

function isGlb(path) {
  if (statSync(path).size < 4) return false;
  const descriptor = openSync(path, "r");
  try {
    const magic = Buffer.alloc(4);
    readSync(descriptor, magic, 0, 4, 0);
    return magic.toString("ascii") === "glTF";
  } finally {
    closeSync(descriptor);
  }
}

function issue(issues, code, message, path = null) {
  issues.push({ code, message, ...(path ? { path } : {}) });
}

function validateSource(issues, row, label) {
  if (!row.source || !SOURCE_STATUSES.has(row.source.status)) {
    issue(issues, "INVALID_SOURCE_STATUS", `${label} needs a documented, partial, or unknown source status.`, row.path);
    return;
  }
  if (!Array.isArray(row.source.references)) {
    issue(issues, "INVALID_SOURCE_REFERENCES", `${label} source references must be an array.`, row.path);
    return;
  }
  if (row.source.status !== "unknown" && row.source.references.length === 0) {
    issue(issues, "MISSING_SOURCE_REFERENCE", `${label} has claimed provenance without a metadata reference.`, row.path);
  }
  for (const reference of row.source.references) {
    if (typeof reference?.metadata_path !== "string" || typeof reference?.pointer !== "string") {
      issue(issues, "INVALID_SOURCE_REFERENCE", `${label} has a source reference without metadata_path and pointer.`, row.path);
    }
    if (typeof reference?.public_url === "string") {
      issue(issues, "SOURCE_ARCHIVE_PUBLISHED", `${label} source metadata must not expose an archive as a public URL.`, row.path);
    }
  }
}

function validateLicense(issues, row, label) {
  if (!row.license || !LICENSE_STATUSES.has(row.license.status)) {
    issue(issues, "INVALID_LICENSE_STATUS", `${label} needs a declared or unknown licence status.`, row.path);
    return;
  }
  if (!Array.isArray(row.license.references)) {
    issue(issues, "INVALID_LICENSE_REFERENCES", `${label} licence references must be an array.`, row.path);
  }
  if (row.license.status === "unknown") {
    if (row.license.expression !== null) {
      issue(issues, "INVENTED_LICENSE_EXPRESSION", `${label} has an expression despite an unknown licence status.`, row.path);
    }
    if (typeof row.license.note !== "string" || row.license.note.length === 0) {
      issue(issues, "UNEXPLAINED_UNKNOWN_LICENSE", `${label} does not explain its unknown licence status.`, row.path);
    }
  } else if (typeof row.license.expression !== "string" || row.license.expression.length === 0
      || row.license.references.length === 0) {
    issue(issues, "UNSUPPORTED_DECLARED_LICENSE", `${label} declares a licence without an expression and reference.`, row.path);
  }
}

function validateUse(issues, row, label) {
  if (!row.use || !USE_STATUSES.has(row.use.status) || !Array.isArray(row.use.references)) {
    issue(issues, "INVALID_USE", `${label} needs an active or inactive use record and a references array.`, row.path);
    return;
  }
  if (row.use.status === "active" && row.use.references.length === 0) {
    issue(issues, "MISSING_ACTIVE_USE", `${label} is marked active without a use reference.`, row.path);
  }
  if (row.use.status === "inactive" && (typeof row.use.reason !== "string" || row.use.reason.length === 0)) {
    issue(issues, "UNEXPLAINED_INACTIVE_USE", `${label} is inactive without a reason.`, row.path);
  }
  for (const reference of row.use.references) {
    if (typeof reference?.kind !== "string" || typeof reference?.reference_path !== "string") {
      issue(issues, "INVALID_USE_REFERENCE", `${label} has a use reference without kind and reference_path.`, row.path);
    }
  }
}

function validateCommonRow(issues, row, label) {
  if (typeof row.path !== "string" || row.path.length === 0) {
    issue(issues, "INVALID_ASSET_PATH", `${label} has no repository-relative path.`);
  }
  if (typeof row.public_url !== "string" || !row.public_url.startsWith("/")) {
    issue(issues, "INVALID_PUBLIC_URL", `${label} has no root-relative public URL.`, row.path);
  }
  if (!SHA256.test(row.sha256 ?? "")) issue(issues, "INVALID_SHA256", `${label} has an invalid SHA-256.`, row.path);
  if (!Number.isSafeInteger(row.bytes) || row.bytes < 0) {
    issue(issues, "INVALID_BYTE_COUNT", `${label} has an invalid byte count.`, row.path);
  }
  validateSource(issues, row, label);
  validateLicense(issues, row, label);
  validateUse(issues, row, label);
}

async function verifyCurrentFile(issues, repoRoot, row, label) {
  const path = resolve(repoRoot, row.path);
  const relativePath = repoPath(repoRoot, path);
  if (relativePath === ".." || relativePath.startsWith("../")) {
    issue(issues, "ASSET_OUTSIDE_REPOSITORY", `${label} resolves outside the repository.`, row.path);
    return;
  }
  if (!existsSync(path) || !statSync(path).isFile()) {
    issue(issues, "LEDGER_FILE_MISSING", `${label} is not a current file.`, row.path);
    return;
  }
  const bytes = statSync(path).size;
  if (bytes !== row.bytes) {
    issue(issues, "ASSET_SIZE_DRIFT", `${label} byte count changed from ${row.bytes} to ${bytes}.`, row.path);
  }
  const digest = await sha256(path);
  if (digest !== row.sha256) {
    issue(issues, "ASSET_HASH_DRIFT", `${label} SHA-256 changed from ${row.sha256} to ${digest}.`, row.path);
  }
}

async function verifyMetadataSources(issues, repoRoot, sources) {
  if (!Array.isArray(sources)) {
    issue(issues, "INVALID_METADATA_SOURCES", "metadata_sources must be an array.");
    return;
  }
  const seen = new Set();
  for (const source of sources) {
    if (typeof source?.path !== "string" || seen.has(source.path)) {
      issue(issues, "INVALID_METADATA_SOURCE_PATH", "Metadata source paths must be unique strings.", source?.path);
      continue;
    }
    seen.add(source.path);
    const path = resolve(repoRoot, source.path);
    if (!existsSync(path) || !statSync(path).isFile()) {
      issue(issues, "METADATA_SOURCE_MISSING", "A ledger metadata source is missing.", source.path);
      continue;
    }
    if (statSync(path).size !== source.bytes) {
      issue(issues, "METADATA_SIZE_DRIFT", "A ledger metadata source changed size.", source.path);
    }
    if (await sha256(path) !== source.sha256) {
      issue(issues, "METADATA_HASH_DRIFT", "A ledger metadata source changed SHA-256.", source.path);
    }
  }
}

function compareSets(issues, actual, declared, extraCode, missingCode, label) {
  for (const path of actual) {
    if (!declared.has(path)) issue(issues, extraCode, `${label} is absent from the ledger.`, path);
  }
  for (const path of declared) {
    if (!actual.has(path)) issue(issues, missingCode, `${label} is declared but absent from the current inventory.`, path);
  }
}

async function auditPublicModels(issues, repoRoot, ledger) {
  const root = resolve(repoRoot, ledger.roots.public_models);
  if (!existsSync(root)) {
    issue(issues, "PUBLIC_MODEL_ROOT_MISSING", "The public model root does not exist.", ledger.roots.public_models);
    return { actual: 0, declared: ledger.entries?.length ?? 0 };
  }
  const actualPaths = new Set(filesBelow(root, path => extname(path).toLowerCase() === ".glb")
    .map(path => repoPath(repoRoot, path)));
  const declaredPaths = new Set();
  if (!Array.isArray(ledger.entries)) {
    issue(issues, "INVALID_MODEL_ENTRIES", "entries must be an array.");
    return { actual: actualPaths.size, declared: 0 };
  }
  for (const [index, row] of ledger.entries.entries()) {
    const label = `entries[${index}]`;
    validateCommonRow(issues, row, label);
    if (declaredPaths.has(row.path)) issue(issues, "DUPLICATE_ASSET_PATH", `${label} duplicates an asset path.`, row.path);
    declaredPaths.add(row.path);
    if (!row.path?.startsWith(`${ledger.roots.public_models}/`)) {
      issue(issues, "MODEL_OUTSIDE_DECLARED_ROOT", `${label} is outside roots.public_models.`, row.path);
    }
    const expectedUrl = row.path?.startsWith("frontend/public/") ? row.path.slice("frontend/public".length) : null;
    if (expectedUrl !== row.public_url) issue(issues, "PUBLIC_URL_DRIFT", `${label} public URL does not match its path.`, row.path);
    await verifyCurrentFile(issues, repoRoot, row, label);
    const absolutePath = resolve(repoRoot, row.path);
    if (existsSync(absolutePath) && statSync(absolutePath).isFile() && !isGlb(absolutePath)) {
      issue(issues, "INVALID_GLB_MAGIC", `${label} does not start with the binary glTF magic.`, row.path);
    }
  }
  compareSets(issues, actualPaths, declaredPaths, "UNLISTED_PUBLIC_MODEL", "STALE_PUBLIC_MODEL_ENTRY", "Public model GLB");
  return { actual: actualPaths.size, declared: declaredPaths.size };
}

async function auditBuildingRuntime(issues, repoRoot, ledger) {
  const discoveredManifestPaths = discoverBuildingManifestPaths(repoRoot);
  const discoveredManifestRelatives = new Set(discoveredManifestPaths.map(path => repoPath(repoRoot, path)));
  const declaredManifestRelatives = new Set(ledger.roots.building_runtime_manifests);
  compareSets(issues, discoveredManifestRelatives, declaredManifestRelatives,
    "UNLISTED_BUILDING_MANIFEST", "STALE_BUILDING_MANIFEST", "Building runtime manifest");
  const expected = new Map();
  const expectedScenes = [];
  const sceneIds = new Set();
  const publicGlbs = new Set();
  for (const manifestPath of discoveredManifestPaths) {
    const manifestRelative = repoPath(repoRoot, manifestPath);
    let manifest;
    try {
      manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    } catch (error) {
      issue(issues, "INVALID_BUILDING_MANIFEST", `Cannot parse the building runtime manifest: ${error.message}`,
        manifestRelative);
      continue;
    }
    const sceneId = manifest.scene?.id;
    if (typeof sceneId !== "string" || sceneId.length === 0) {
      issue(issues, "INVALID_BUILDING_SCENE_ID", "A building manifest has no scene.id.", manifestRelative);
      continue;
    }
    if (sceneIds.has(sceneId)) {
      issue(issues, "DUPLICATE_BUILDING_SCENE_ID", `Multiple building manifests declare scene.id ${sceneId}.`, manifestRelative);
    }
    sceneIds.add(sceneId);
    const sceneRoot = dirname(manifestPath);
    const sceneExpected = new Set();
    let sceneBytes = 0;
    for (const [index, building] of (manifest.buildings ?? []).entries()) {
      if (!building.object_id || !building.derived_glb?.path) {
        issue(issues, "INVALID_BUILDING_MANIFEST_ROW", `Building manifest row ${index} lacks object_id or derived_glb.`,
          manifestRelative);
        continue;
      }
      const path = repoPath(repoRoot, join(sceneRoot, building.derived_glb.path));
      if (expected.has(path)) {
        issue(issues, "DUPLICATE_BUILDING_ASSET_DECLARATION", "Multiple building manifests declare the same public asset.", path);
        continue;
      }
      expected.set(path, { building, index, sceneId, manifestRelative });
      sceneExpected.add(path);
      sceneBytes += building.derived_glb.bytes ?? 0;
    }
    const scenePublicGlbs = new Set(filesBelow(join(sceneRoot, "assets"), isGlb)
      .map(path => repoPath(repoRoot, path)));
    for (const path of scenePublicGlbs) {
      publicGlbs.add(path);
      if (!sceneExpected.has(path)) {
        issue(issues, "UNDECLARED_BUILDING_RUNTIME_GLB",
          "An extensionless public GLB is absent from its building manifest.", path);
      }
    }
    expectedScenes.push({
      scene_id: sceneId,
      manifest_path: manifestRelative,
      glbs: sceneExpected.size,
      bytes: sceneBytes,
    });
  }
  const rows = Array.isArray(ledger.building_runtime) ? ledger.building_runtime : [];
  if (!Array.isArray(ledger.building_runtime)) issue(issues, "INVALID_BUILDING_ENTRIES", "building_runtime must be an array.");
  const declared = new Set();
  const objectKeys = new Set();
  for (const [index, row] of rows.entries()) {
    const label = `building_runtime[${index}]`;
    validateCommonRow(issues, row, label);
    if (declared.has(row.path)) issue(issues, "DUPLICATE_BUILDING_PATH", `${label} duplicates a path.`, row.path);
    const objectKey = `${row.scene_id}\u0000${row.object_id}`;
    if (objectKeys.has(objectKey)) issue(issues, "DUPLICATE_BUILDING_ID", `${label} duplicates a scene/object identity.`, row.path);
    declared.add(row.path);
    objectKeys.add(objectKey);
    const expectedRow = expected.get(row.path);
    if (expectedRow && expectedRow.sceneId !== row.scene_id) {
      issue(issues, "BUILDING_SCENE_ID_DRIFT", `${label} scene_id differs from the manifest.`, row.path);
    }
    if (expectedRow && expectedRow.building.object_id !== row.object_id) {
      issue(issues, "BUILDING_OBJECT_ID_DRIFT", `${label} object_id differs from the manifest.`, row.path);
    }
    if (expectedRow && (expectedRow.building.derived_glb.bytes !== row.bytes
        || expectedRow.building.derived_glb.sha256 !== row.sha256)) {
      issue(issues, "BUILDING_DECLARATION_DRIFT", `${label} differs from the manifest's derived_glb identity.`, row.path);
    }
    if (expectedRow && (row.source?.canonical_glb?.manifest_declared_path !== expectedRow.building.glb?.path
        || row.source?.canonical_glb?.sha256 !== expectedRow.building.glb?.sha256
        || row.source?.canonical_glb?.bytes !== expectedRow.building.glb?.bytes)) {
      issue(issues, "BUILDING_SOURCE_DRIFT", `${label} canonical source identity differs from the manifest.`, row.path);
    }
    const expectedUrl = row.path?.startsWith("frontend/public/") ? row.path.slice("frontend/public".length) : null;
    if (expectedUrl !== row.public_url) issue(issues, "PUBLIC_URL_DRIFT", `${label} public URL does not match its path.`, row.path);
    await verifyCurrentFile(issues, repoRoot, row, label);
    const absolutePath = resolve(repoRoot, row.path);
    if (existsSync(absolutePath) && statSync(absolutePath).isFile() && !isGlb(absolutePath)) {
      issue(issues, "INVALID_GLB_MAGIC", `${label} does not start with the binary glTF magic.`, row.path);
    }
  }
  compareSets(issues, new Set(expected.keys()), declared,
    "UNLISTED_BUILDING_RUNTIME", "STALE_BUILDING_RUNTIME_ENTRY", "Building runtime GLB");
  return {
    actual: expected.size,
    declared: declared.size,
    detected_public_glbs: publicGlbs.size,
    manifests: discoveredManifestPaths.length,
    byScene: expectedScenes,
  };
}

function validateSummary(issues, ledger, counts) {
  const expected = {
    public_model_glbs: counts.publicModels.declared,
    building_runtime_glbs: counts.buildings.declared,
    building_runtime_scenes: counts.buildings.manifests,
    total_glbs: counts.publicModels.declared + counts.buildings.declared,
  };
  for (const [name, value] of Object.entries(expected)) {
    if (ledger.summary?.[name] !== value) {
      issue(issues, "SUMMARY_COUNT_DRIFT", `summary.${name} is ${ledger.summary?.[name]}, expected ${value}.`);
    }
  }
  if (JSON.stringify(ledger.summary?.building_runtime_by_scene) !== JSON.stringify(counts.buildings.byScene)) {
    issue(issues, "SUMMARY_BUILDING_SCENE_DRIFT", "Summary per-scene building counts do not match the published manifests.");
  }
  const all = [...(ledger.entries ?? []), ...(ledger.building_runtime ?? [])];
  const unknownLicenses = all.filter(row => row.license?.status === "unknown").length;
  const declaredLicenses = all.filter(row => row.license?.status === "declared").length;
  if (ledger.summary?.license_unknown !== unknownLicenses || ledger.summary?.license_declared !== declaredLicenses) {
    issue(issues, "SUMMARY_LICENSE_DRIFT", "Summary licence counts do not match the ledger rows.");
  }
}

function validateAuthorizationBasis(issues, ledger) {
  const basis = ledger.authorization_basis;
  if (!basis || basis.status !== "user-declared"
      || basis.purpose !== "mentor-authorized internal research"
      || !/^\d{4}-\d{2}-\d{2}$/.test(basis.confirmed_date_utc ?? "")
      || basis.original_license_unchanged !== true
      || basis.public_redistribution_authorized !== false
      || typeof basis.evidence !== "string" || basis.evidence.length === 0
      || typeof basis.note !== "string" || basis.note.length === 0) {
    issue(issues, "INVALID_AUTHORIZATION_BASIS",
      "The ledger must distinguish user-declared internal-research authorization from original licensing and public redistribution.");
  }
}

export async function auditCityAssetLedger(options = {}) {
  const scriptRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const repoRoot = resolve(options.repoRoot ?? scriptRoot);
  const ledgerPath = resolve(options.ledgerPath ?? join(repoRoot, DEFAULT_LEDGER));
  const issues = [];
  if (!existsSync(ledgerPath)) {
    issue(issues, "LEDGER_MISSING", "The candidate ledger does not exist.", repoPath(repoRoot, ledgerPath));
    return { schema_version: "aero-bench.city-asset-ledger-audit/v2", status: "FAIL", issues };
  }
  let ledger;
  try {
    ledger = JSON.parse(readFileSync(ledgerPath, "utf8"));
  } catch (error) {
    issue(issues, "INVALID_LEDGER_JSON", `Cannot parse the candidate ledger: ${error.message}`, repoPath(repoRoot, ledgerPath));
    return { schema_version: "aero-bench.city-asset-ledger-audit/v2", status: "FAIL", issues };
  }
  if (ledger.schema_version !== LEDGER_SCHEMA) {
    issue(issues, "INVALID_LEDGER_SCHEMA", `Expected ${LEDGER_SCHEMA}, received ${ledger.schema_version}.`);
  }
  validateAuthorizationBasis(issues, ledger);
  if (!ledger.roots || typeof ledger.roots.public_models !== "string"
      || !Array.isArray(ledger.roots.building_runtime_manifests)
      || ledger.roots.building_runtime_manifests.length === 0
      || ledger.roots.building_runtime_manifests.some(path => typeof path !== "string")
      || new Set(ledger.roots.building_runtime_manifests).size !== ledger.roots.building_runtime_manifests.length) {
    issue(issues, "INVALID_LEDGER_ROOTS", "Ledger roots are absent or invalid.");
    return {
      schema_version: "aero-bench.city-asset-ledger-audit/v2",
      status: "FAIL",
      ledger: repoPath(repoRoot, ledgerPath),
      issues,
    };
  }
  await verifyMetadataSources(issues, repoRoot, ledger.metadata_sources);
  const counts = {
    publicModels: await auditPublicModels(issues, repoRoot, ledger),
    buildings: await auditBuildingRuntime(issues, repoRoot, ledger),
  };
  validateSummary(issues, ledger, counts);
  return {
    schema_version: "aero-bench.city-asset-ledger-audit/v2",
    status: issues.length === 0 ? "PASS" : "FAIL",
    ledger: repoPath(repoRoot, ledgerPath),
    authorization_basis: ledger.authorization_basis,
    counts: {
      public_model_glbs_on_disk: counts.publicModels.actual,
      public_model_rows: counts.publicModels.declared,
      building_manifest_glbs: counts.buildings.actual,
      building_runtime_rows: counts.buildings.declared,
      building_runtime_manifests: counts.buildings.manifests,
      building_runtime_by_scene: counts.buildings.byScene,
      building_public_glbs_detected: counts.buildings.detected_public_glbs ?? 0,
      issues: issues.length,
    },
    issues,
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
  const ledgerPath = resolve(repoRoot, parsed.ledger ?? DEFAULT_LEDGER);
  const reportPath = resolve(repoRoot, parsed.report ?? DEFAULT_REPORT);
  const report = await auditCityAssetLedger({ repoRoot, ledgerPath });
  await mkdir(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ report: repoPath(repoRoot, reportPath), ...report.counts,
    status: report.status }, null, 2)}\n`);
  if (report.status !== "PASS") process.exitCode = 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
    process.exitCode = 1;
  });
}
