import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { auditCityAssetLedger } from "./audit-city-asset-ledger.mjs";
import { buildCityAssetLedger } from "./build-city-asset-ledger.mjs";

function write(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, value);
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function addBuildingScene(repoRoot, sceneId, objectId, content = sceneId) {
  const buildingBytes = Buffer.concat([Buffer.from("glTF"), Buffer.from(content)]);
  const derived = digest(buildingBytes);
  const manifestPath = join(repoRoot, `frontend/public/building-renders/${sceneId}/manifest.json`);
  write(join(dirname(manifestPath), `assets/${derived}`), buildingBytes);
  write(manifestPath, JSON.stringify({
    schema_version: "aero-bench.building-render-runtime/v2",
    scene: { id: sceneId },
    buildings: [{
      object_id: objectId,
      glb: { path: `canonical/${objectId}.glb`, sha256: "1".repeat(64), bytes: 20 },
      derived_glb: { path: `assets/${derived}`, sha256: derived, bytes: buildingBytes.length },
    }],
  }));
  return manifestPath;
}

function fixture() {
  const repoRoot = mkdtempSync(join(tmpdir(), "city-asset-ledger-"));
  const publicModelsRoot = join(repoRoot, "frontend/public/models");
  const model = Buffer.from("glTFunknown provenance model");
  write(join(publicModelsRoot, "unmapped.glb"), model);

  const buildingManifestPath = addBuildingScene(repoRoot, "test-scene", "building.test", "building");
  const ledgerPath = join(repoRoot, "candidate.json");
  return { repoRoot, publicModelsRoot, buildingManifestPath, ledgerPath };
}

async function candidate(paths) {
  const ledger = await buildCityAssetLedger(paths);
  write(paths.ledgerPath, `${JSON.stringify(ledger, null, 2)}\n`);
  return ledger;
}

test("unknown provenance and licence remain explicit without blocking an internally consistent audit", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  const ledger = await candidate(paths);
  assert.equal(ledger.entries.length, 1);
  assert.equal(ledger.entries[0].source.status, "unknown");
  assert.equal(ledger.entries[0].license.status, "unknown");
  assert.equal(ledger.entries[0].license.expression, null);
  assert.equal(ledger.entries[0].use.status, "inactive");
  assert.equal(ledger.summary.license_unknown, 2);
  assert.deepEqual(ledger.roots.building_runtime_manifests,
    ["frontend/public/building-renders/test-scene/manifest.json"]);
  assert.equal(ledger.building_runtime[0].scene_id, "test-scene");
  assert.equal(ledger.authorization_basis.purpose, "mentor-authorized internal research");
  assert.equal(ledger.authorization_basis.original_license_unchanged, true);
  assert.equal(ledger.authorization_basis.public_redistribution_authorized, false);
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "PASS", JSON.stringify(report.issues));
});

test("audit fails closed when a public-model GLB is not listed", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  await candidate(paths);
  write(join(paths.publicModelsRoot, "added-after-generation.glb"), "glTFunlisted");
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "FAIL");
  assert(report.issues.some(item => item.code === "UNLISTED_PUBLIC_MODEL"));
});

test("audit fails closed when listed model bytes are tampered", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  await candidate(paths);
  write(join(paths.publicModelsRoot, "unmapped.glb"), "glTFchanged after generation");
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "FAIL");
  assert(report.issues.some(item => item.code === "ASSET_HASH_DRIFT"));
});

test("audit detects an extensionless building GLB that the manifest and ledger omit", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  await candidate(paths);
  write(join(dirname(paths.buildingManifestPath), "assets/unlisted"), Buffer.from("glTFextra"));
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "FAIL");
  assert(report.issues.some(item => item.code === "UNDECLARED_BUILDING_RUNTIME_GLB"));
});

test("unknown licence records cannot carry an invented CC0 expression", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  const ledger = await candidate(paths);
  ledger.entries[0].license.expression = "CC0-1.0";
  write(paths.ledgerPath, `${JSON.stringify(ledger, null, 2)}\n`);
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "FAIL");
  assert(report.issues.some(item => item.code === "INVENTED_LICENSE_EXPRESSION"));
});

test("generator discovers and auditor verifies multiple published building regions", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  addBuildingScene(paths.repoRoot, "second-scene", "building.second", "second building");
  const ledger = await candidate(paths);
  assert.equal(ledger.schema_version, "aero-bench.city-asset-ledger/v2");
  assert.equal(ledger.roots.building_runtime_manifests.length, 2);
  assert.equal(ledger.building_runtime.length, 2);
  assert.deepEqual(ledger.building_runtime.map(row => row.scene_id).sort(), ["second-scene", "test-scene"]);
  assert.equal(ledger.summary.building_runtime_scenes, 2);
  assert.deepEqual(ledger.summary.building_runtime_by_scene.map(row => row.scene_id),
    ["second-scene", "test-scene"]);
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "PASS", JSON.stringify(report.issues));
  assert.equal(report.counts.building_runtime_manifests, 2);
});

test("audit fails when a newly published region is absent from an older ledger", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  await candidate(paths);
  addBuildingScene(paths.repoRoot, "late-scene", "building.late", "late building");
  const report = await auditCityAssetLedger({ repoRoot: paths.repoRoot, ledgerPath: paths.ledgerPath });
  assert.equal(report.status, "FAIL");
  assert(report.issues.some(item => item.code === "UNLISTED_BUILDING_MANIFEST"));
  assert(report.issues.some(item => item.code === "UNLISTED_BUILDING_RUNTIME"));
});

test("generator rejects duplicate building scene identities", async t => {
  const paths = fixture();
  t.after(() => rmSync(paths.repoRoot, { recursive: true, force: true }));
  const duplicatePath = addBuildingScene(paths.repoRoot, "duplicate-directory", "building.other", "other");
  const duplicate = JSON.parse(readFileSync(duplicatePath, "utf8"));
  duplicate.scene.id = "test-scene";
  write(duplicatePath, JSON.stringify(duplicate));
  await assert.rejects(() => buildCityAssetLedger(paths), /Duplicate building runtime scene\.id/);
});
