import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

import {
  BUILDING_CAPTURE_PLAN_SCHEMA,
  BUILDING_CAPTURE_REPORT_SCHEMA,
  CAPTURE_CAMERA_IDS,
  CAPTURE_MOODS,
  HARDWARE_BROWSER_ARGS,
  buildControlledBaselineOverride,
  buildFrozenCameraPlan,
  expectedReflectionCaptureDelta,
  isIgnorableBrowserRequestFailure,
  instrumentAppBundle,
  measureSourceDerivedGeometry,
  parseCaptureArguments,
  selectBuildingCaptureSubjects,
  summarizeFrameSamples,
  validateFrozenCameraPlan,
  verifyMatchedCaptureReports,
} from "./capture-building-nearview.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(SCRIPT_DIR, "..");
const REPOSITORY_ROOT = resolve(FRONTEND_ROOT, "..");
const SCENE_PATH = resolve(
  FRONTEND_ROOT,
  "public/city-presentation/building-render-scene-v1.json",
);
const RENDER_ROOT = resolve(
  FRONTEND_ROOT,
  "public/building-renders/shanghai-huangpu-east-v1",
);
const SOURCE_ROOT = resolve(
  REPOSITORY_ROOT,
  "validation/building-render-scaleout-mimo-20260929/assets",
);
const BASELINE_COMMIT = "6dfe79a8ad0ff0cdaece37e9231ee9acce5a8f10";
const BASELINE_MANIFEST = Object.freeze({
  sha256: "61d2bf84ffe9bb2241eb5e68ca098d4c640a408e33c9cdd0881960574a67c42c",
  bytes: 484454,
});

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function glbDocument(bytes) {
  const data = Buffer.from(bytes);
  const jsonLength = data.readUInt32LE(12);
  return JSON.parse(data.subarray(20, 20 + jsonLength).toString("utf8"));
}

function pngDimensions(bytes) {
  const data = Buffer.from(bytes);
  assert.equal(data.subarray(1, 4).toString("ascii"), "PNG");
  assert.equal(data.subarray(12, 16).toString("ascii"), "IHDR");
  return [data.readUInt32BE(16), data.readUInt32BE(20)];
}

test("only Chromium's exact loopback favicon abort is diagnostic", () => {
  const origin = "https://127.0.0.1:5209";
  assert.equal(
    isIgnorableBrowserRequestFailure(
      `${origin}/favicon.ico net::ERR_ABORTED`,
      origin,
    ),
    true,
  );
  assert.equal(
    isIgnorableBrowserRequestFailure(
      `${origin}/assets/favicon.ico net::ERR_ABORTED`,
      origin,
    ),
    false,
  );
  assert.equal(
    isIgnorableBrowserRequestFailure(
      `${origin}/favicon.ico net::ERR_FAILED`,
      origin,
    ),
    false,
  );
  assert.equal(
    isIgnorableBrowserRequestFailure(
      "https://example.test/favicon.ico net::ERR_ABORTED",
      "https://example.test",
    ),
    false,
  );
});

async function currentManifest() {
  const scene = JSON.parse(await readFile(SCENE_PATH, "utf8"));
  const pin = scene.building_render.manifest;
  const bytes = await readFile(resolve(RENDER_ROOT, "assets", pin.sha256));
  assert.equal(bytes.byteLength, pin.size_bytes);
  assert.equal(sha256(bytes), pin.sha256);
  return { scene, manifest: JSON.parse(bytes), bytes, pin };
}

test("capture arguments require an explicit output and reject ambiguous origins", () => {
  const options = parseCaptureArguments([
    "http://127.0.0.1:5395/",
    "/tmp/aero-c3",
    "candidate-a",
    "--camera-plan",
    "/tmp/plan.json",
    "--source-assets",
    "/tmp/source",
    "--allow-software",
  ]);
  assert.equal(options.origin, "http://127.0.0.1:5395");
  assert.equal(options.output, "/tmp/aero-c3");
  assert.equal(options.label, "candidate-a");
  assert.equal(options.cameraPlanPath, "/tmp/plan.json");
  assert.equal(options.matchedReportPath, null);
  assert.equal(options.sourceAssets, "/tmp/source");
  assert.equal(options.allowSoftware, true);
  assert.equal(options.allowLocalSelfSigned, false);
  assert.equal(options.controlledBaseline, null);
  assert.throws(
    () => parseCaptureArguments(["http://127.0.0.1:5395"]),
    /output directory is required/,
  );
  assert.throws(
    () =>
      parseCaptureArguments(["http://127.0.0.1:5395/?scene=1", "/tmp/aero-c3"]),
    /HTTP\(S\) origin/,
  );
  assert.throws(
    () => parseCaptureArguments(["http://127.0.0.1:5395/path", "/tmp/aero-c3"]),
    /HTTP\(S\) origin/,
  );
  assert.throws(
    () => parseCaptureArguments(["http://127.0.0.1:5395", ""]),
    /must not be empty/,
  );
  assert.throws(
    () =>
      parseCaptureArguments([
        "http://127.0.0.1:5395",
        "/tmp/aero-c3",
        "../escape",
      ]),
    /filename-safe/,
  );
  assert.throws(
    () =>
      parseCaptureArguments([
        "http://127.0.0.1:5395",
        "/tmp/aero-c3",
        "--camera-plan",
      ]),
    /requires a path/,
  );
  assert.throws(
    () =>
      parseCaptureArguments([
        "http://127.0.0.1:5395",
        "/tmp/aero-c3",
        "--controlled-baseline-commit",
        BASELINE_COMMIT,
      ]),
    /must be supplied together/,
  );
  const baseline = parseCaptureArguments([
    "http://127.0.0.1:5395",
    "/tmp/aero-c3",
    "--controlled-baseline-commit",
    BASELINE_COMMIT,
    "--controlled-baseline-manifest",
    `${BASELINE_MANIFEST.sha256}:${BASELINE_MANIFEST.bytes}`,
  ]);
  assert.deepEqual(baseline.controlledBaseline, {
    commit: BASELINE_COMMIT,
    manifest: BASELINE_MANIFEST,
  });
  const matched = parseCaptureArguments([
    "http://127.0.0.1:5395",
    "/tmp/aero-c3",
    "candidate",
    "--matched-report",
    "/tmp/baseline-report.json",
  ]);
  assert.equal(matched.matchedReportPath, "/tmp/baseline-report.json");
  assert.throws(
    () =>
      parseCaptureArguments([
        "http://127.0.0.1:5395",
        "/tmp/aero-c3",
        "--matched-report",
        "/tmp/baseline-report.json",
        "--controlled-baseline-commit",
        BASELINE_COMMIT,
        "--controlled-baseline-manifest",
        `${BASELINE_MANIFEST.sha256}:${BASELINE_MANIFEST.bytes}`,
      ]),
    /cannot also consume/,
  );
  const localHttps = parseCaptureArguments([
    "https://127.0.0.1:5209",
    "/tmp/aero-c3",
    "--allow-local-self-signed",
  ]);
  assert.equal(localHttps.origin, "https://127.0.0.1:5209");
  assert.equal(localHttps.allowLocalSelfSigned, true);
  assert.throws(
    () =>
      parseCaptureArguments([
        "https://example.com",
        "/tmp/aero-c3",
        "--allow-local-self-signed",
      ]),
    /restricted to a loopback HTTPS origin/,
  );
  assert.throws(
    () =>
      parseCaptureArguments([
        "http://127.0.0.1:5209",
        "/tmp/aero-c3",
        "--allow-local-self-signed",
      ]),
    /restricted to a loopback HTTPS origin/,
  );
});

test("the current production bundle has one fail-closed map hook", async () => {
  assert.ok(HARDWARE_BROWSER_ARGS.includes("--use-angle=vulkan"));
  assert.ok(HARDWARE_BROWSER_ARGS.includes("--disable-software-rasterizer"));
  const assetRoot = resolve(FRONTEND_ROOT, "dist/assets");
  const appBundles = (await readdir(assetRoot)).filter((name) =>
    /^app-[A-Za-z0-9_-]+\.js$/.test(name),
  );
  assert.equal(appBundles.length, 1);
  const source = await readFile(resolve(assetRoot, appBundles[0]), "utf8");
  const result = instrumentAppBundle(source);
  assert.equal(result.matchCount, 1);
  assert.notEqual(result.sourceSha256, result.instrumentedSha256);
  assert.equal(
    result.source.split("window.__aeroBuildingCaptureMap=").length,
    2,
  );
  assert.throws(
    () => instrumentAppBundle("const unrelated = true;"),
    /found 0/,
  );
  assert.throws(() => instrumentAppBundle(`${source}\n${source}`), /found 2/);
});

test("the real 414-building manifest produces one strict frozen camera matrix", async () => {
  const { manifest, pin } = await currentManifest();
  const subjects = selectBuildingCaptureSubjects(manifest);
  assert.equal(subjects.facade.object_id, "building.way.372180502.component.0");
  assert.equal(subjects.roof.object_id, "building.way.165909803.component.0");
  const first = buildFrozenCameraPlan(manifest, pin.sha256);
  const second = buildFrozenCameraPlan(manifest, pin.sha256);
  assert.deepEqual(first, second);
  assert.equal(first.schema, BUILDING_CAPTURE_PLAN_SCHEMA);
  assert.deepEqual(
    first.cameras.map((camera) => camera.id),
    CAPTURE_CAMERA_IDS,
  );
  assert.equal(validateFrozenCameraPlan(first, manifest, pin.sha256), first);

  const stalePin = structuredClone(first);
  stalePin.cameras[0].subject_source_sha256 = "0".repeat(64);
  assert.throws(
    () => validateFrozenCameraPlan(stalePin, manifest, pin.sha256),
    /stale source geometry pins/,
  );
  const baselineVariant = structuredClone(manifest);
  delete baselineVariant.art_detail;
  for (const entry of baselineVariant.buildings) {
    delete entry.art_detail;
    entry.derived_glb.sha256 = "1".repeat(64);
  }
  assert.deepEqual(
    Object.values(selectBuildingCaptureSubjects(baselineVariant)).map(
      (entry) => entry.object_id,
    ),
    Object.values(selectBuildingCaptureSubjects(manifest)).map(
      (entry) => entry.object_id,
    ),
  );
  assert.equal(
    validateFrozenCameraPlan(first, baselineVariant, "2".repeat(64)),
    first,
  );
  const shifted = structuredClone(first);
  shifted.cameras[0].position[0] += 1;
  assert.throws(
    () => validateFrozenCameraPlan(shifted, manifest, pin.sha256),
    /source-derived C3 matrix/,
  );
  const reordered = structuredClone(first);
  [reordered.cameras[0], reordered.cameras[1]] = [
    reordered.cameras[1],
    reordered.cameras[0],
  ];
  assert.throws(
    () => validateFrozenCameraPlan(reordered, manifest, pin.sha256),
    /ordered C3/,
  );
  const badProjection = structuredClone(first);
  badProjection.cameras[0].near_m = badProjection.cameras[0].far_m;
  assert.throws(
    () => validateFrozenCameraPlan(badProjection, manifest, pin.sha256),
    /projection or view direction/,
  );
  for (const camera of first.cameras) {
    const containing = manifest.buildings.filter(
      (entry) =>
        entry.object_id !== camera.subject_object_id &&
        camera.position[0] >= entry.envelope.min_e &&
        camera.position[0] <= entry.envelope.max_e &&
        -camera.position[2] >= entry.envelope.min_n &&
        -camera.position[2] <= entry.envelope.max_n &&
        camera.position[1] >= entry.envelope.base_up &&
        camera.position[1] <= entry.envelope.top_up,
    );
    assert.deepEqual(
      containing,
      [],
      `${camera.id} must be valid for production reflection recentering`,
    );
  }
});

test("captured facade and roof subjects retain pinned source geometry and envelopes", async () => {
  const { manifest } = await currentManifest();
  const subjects = selectBuildingCaptureSubjects(manifest);
  for (const entry of [subjects.facade, subjects.roof]) {
    const source = await readFile(
      resolve(SOURCE_ROOT, `${entry.object_id}.glb`),
    );
    const derived = await readFile(
      resolve(RENDER_ROOT, entry.derived_glb.path),
    );
    const measurement = measureSourceDerivedGeometry(entry, source, derived);
    assert.equal(measurement.source_vs_derived_geometry_match, true);
    assert.equal(
      measurement.exact_common_accessors.every((item) => item.byte_identical),
      true,
    );
    assert.equal(measurement.roof_transform.horizontal_max_error_m, 0);
    assert.ok(measurement.roof_transform.drop_max_error_m <= 1e-5);
    assert.ok(measurement.source_envelope_max_error_m <= 1e-3);
    assert.ok(measurement.derived_envelope_max_error_m <= 1e-3);

    const changed = Buffer.from(derived);
    changed[changed.length - 1] ^= 1;
    assert.equal(
      measureSourceDerivedGeometry(entry, source, changed)
        .source_vs_derived_geometry_match,
      false,
    );
  }
});

test("the private baseline override verifies retained bytes and re-pins only strict source bindings", async () => {
  const { scene, manifest, bytes, pin } = await currentManifest();
  const original = JSON.stringify(manifest);
  const roadBindingDocuments = new Map(
    await Promise.all(
      ["road", "effective_fixtures", "traffic", "flight"].map(async (kind) => [
        kind,
        await readFile(
          resolve(
            FRONTEND_ROOT,
            "public",
            scene.road_assets[kind].url.slice(1),
          ),
        ),
      ]),
    ),
  );
  const candidateGlbBytes = new Map(
    await Promise.all(
      Object.values(selectBuildingCaptureSubjects(manifest)).map(
        async (entry) => [
          entry.object_id,
          await readFile(resolve(RENDER_ROOT, entry.derived_glb.path)),
        ],
      ),
    ),
  );
  const override = await buildControlledBaselineOverride(
    {
      scene,
      sceneBytes: await readFile(SCENE_PATH),
      sceneOverrideBytes: null,
      manifest,
      manifestBytes: bytes,
      manifestSha256: pin.sha256,
      baseUrl: new URL(scene.building_render.base_url, "http://127.0.0.1:5395"),
      overrideAssets: new Map(),
      overrideDocuments: new Map(),
      roadBindingDocuments,
      candidateGlbBytes,
      variant: { kind: "served-candidate" },
    },
    { commit: BASELINE_COMMIT, manifest: BASELINE_MANIFEST },
    SOURCE_ROOT,
  );
  assert.equal(JSON.stringify(manifest), original);
  assert.equal(override.variant.kind, "controlled-subject-baseline");
  assert.equal(override.variant.baseline_commit, BASELINE_COMMIT);
  assert.equal(override.variant.retained_geometry_measurements.length, 2);
  assert.equal(override.overrideAssets.size, 9);
  assert.equal(override.overrideDocuments.size, 4);
  assert.equal(override.variant.repinned_source_bindings.length, 4);
  assert.equal(sha256(override.manifestBytes), override.manifestSha256);
  assert.equal(
    override.scene.building_render.manifest.sha256,
    override.manifestSha256,
  );
  assert.equal(
    override.scene.building_render.manifest.size_bytes,
    override.manifestBytes.byteLength,
  );
  for (const [digest, asset] of override.overrideAssets) {
    assert.equal(sha256(asset.bytes), digest);
  }
  for (const record of override.variant.repinned_source_bindings) {
    const ref = override.scene.road_assets[record.kind];
    const document = override.overrideDocuments.get(
      new URL(record.url, "http://127.0.0.1:5395").href,
    );
    assert.ok(document);
    assert.equal(sha256(document.bytes), ref.sha256);
    assert.equal(document.bytes.byteLength, ref.size_bytes);
    const payload = JSON.parse(document.bytes.toString("utf8"));
    assert.equal(
      payload.source_context.building_render_manifest_sha256,
      override.manifestSha256,
    );
    if (record.kind === "traffic") {
      assert.equal(
        payload.visual_obstacle_basis.effective_fixture_geometry_sha256,
        override.scene.road_assets.effective_fixtures.sha256,
      );
    }
  }
  const activeSubjects = selectBuildingCaptureSubjects(override.manifest);
  const wrappers = new Map(
    override.variant.controlled_glb_wrappers.map((item) => [
      item.object_id,
      item,
    ]),
  );
  assert.equal(
    wrappers.get(activeSubjects.facade.object_id).retained_sha256,
    "98dc93696f8bd668e055c39a06ea11e4651fd445ae40a94235749a4fb2be9a47",
  );
  assert.equal(
    wrappers.get(activeSubjects.roof.object_id).retained_sha256,
    "497f37e10d0cbc4d503bbcdcccb587c94794bda0aca09bd3c9f47c8dc5b501d2",
  );
  for (const entry of Object.values(activeSubjects)) {
    const wrapper = wrappers.get(entry.object_id);
    assert.equal(entry.derived_glb.sha256, wrapper.controlled_sha256);
    assert.equal(wrapper.bin_byte_identical, true);
    assert.equal(wrapper.retained_bin_sha256, wrapper.controlled_bin_sha256);
    assert.equal(
      wrapper.presentation_template_sha256,
      manifest.buildings.find((item) => item.object_id === entry.object_id)
        .derived_glb.sha256,
    );
    const controlled = glbDocument(
      override.overrideAssets.get(entry.derived_glb.sha256).bytes,
    );
    const retainedProfile = controlled.materials.find(
      (material) => material.extras?.authoredFacadePaneProfile !== undefined,
    )?.extras.authoredFacadePaneProfile;
    assert.deepEqual(retainedProfile.tile_size_px, [240, 240]);
    assert.deepEqual(retainedProfile.source_crop_px, [0, 0, 240, 240]);
    assert.equal(
      wrapper.material_profile_adaptations[0].policy,
      "translate the verified candidate crop-relative pane profile into the retained full source texture",
    );
    assert.ok(
      controlled.materials.some(
        (material) =>
          material.extras?.authoredBuildingMaterialRole === "opaque-roof",
      ),
    );
  }
  const changed = override.manifest.buildings.filter(
    (entry, index) =>
      entry.derived_glb.sha256 !== manifest.buildings[index].derived_glb.sha256,
  );
  assert.deepEqual(
    changed.map((entry) => entry.object_id),
    [activeSubjects.roof.object_id, activeSubjects.facade.object_id].sort(),
  );
  const plan = buildFrozenCameraPlan(manifest, pin.sha256);
  assert.equal(
    validateFrozenCameraPlan(plan, override.manifest, override.manifestSha256),
    plan,
  );
  const vite = await createServer({
    root: FRONTEND_ROOT,
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "silent",
  });
  try {
    const runtime = await vite.ssrLoadModule("/src/city-building-renders.ts");
    const paneRuntime = await vite.ssrLoadModule(
      "/src/city-facade-pane-profile.ts",
    );
    const roadRuntime = await vite.ssrLoadModule("/src/city-road-assets.ts");
    const parsed = runtime.parseBuildingRenderManifest(override.manifest);
    assert.equal(parsed.buildings.length, 414);
    assert.equal(parsed.textures.length, 51);
    for (const entry of Object.values(activeSubjects)) {
      const asset = override.overrideAssets.get(entry.derived_glb.sha256);
      const bytes = asset.bytes.buffer.slice(
        asset.bytes.byteOffset,
        asset.bytes.byteOffset + asset.bytes.byteLength,
      );
      assert.doesNotThrow(() =>
        runtime.validateBuildingRenderGlb(bytes, entry, parsed),
      );
      const document = glbDocument(asset.bytes);
      const profile = paneRuntime.readCityFacadePaneProfile(
        document.materials.find(
          (material) =>
            material.extras?.authoredFacadePaneProfile !== undefined,
        ).extras.authoredFacadePaneProfile,
      );
      for (const image of document.images) {
        const digest = /^assets\/([0-9a-f]{64})$/.exec(image.uri)?.[1];
        assert.ok(digest);
        assert.deepEqual(
          pngDimensions(override.overrideAssets.get(digest).bytes),
          profile.tile_size_px,
        );
      }
    }
    const payloads = Object.fromEntries(
      override.variant.repinned_source_bindings.map((record) => {
        const document = override.overrideDocuments.get(
          new URL(record.url, "http://127.0.0.1:5395").href,
        );
        return [record.kind, JSON.parse(document.bytes.toString("utf8"))];
      }),
    );
    const packPin = scene.mesh_pack.manifest;
    const packManifest = JSON.parse(
      await readFile(
        resolve(
          FRONTEND_ROOT,
          "public",
          scene.mesh_pack.base_url.slice(1),
          "assets",
          packPin.sha256,
        ),
        "utf8",
      ),
    );
    await assert.doesNotReject(() =>
      roadRuntime.verifyCityRoadAssetBindings(
        override.scene.road_assets,
        { manifest: packManifest, manifestSha256: packPin.sha256 },
        parsed,
        payloads,
        override.manifestSha256,
      ),
    );
  } finally {
    await vite.close();
  }
});

test("frame summaries use the recorded sample count and nearest-rank p90", () => {
  const samples = Array.from({ length: 40 }, (_, index) => index);
  assert.deepEqual(summarizeFrameSamples(samples), {
    count: 40,
    median_ms: 19.5,
    p90_ms: 35,
    min_ms: 0,
    max_ms: 39,
    values_ms: samples,
  });
  assert.throws(() => summarizeFrameSamples([]), /must not be empty/);
  assert.throws(() => summarizeFrameSamples([1, -1]), /nonnegative/);
  assert.equal(
    expectedReflectionCaptureDelta({ target_after: "retained-target" }),
    1,
  );
  assert.equal(expectedReflectionCaptureDelta({ target_after: null }), 0);
});

test("matched reports require the same current renderer, sources and cameras", () => {
  const candidateManifest = "a".repeat(64);
  const frames = Object.fromEntries(
    CAPTURE_CAMERA_IDS.flatMap((camera) =>
      CAPTURE_MOODS.map((mood) => [
        `${camera}-${mood}`,
        { camera_state_sha256: `${camera}-${mood}` },
      ]),
    ),
  );
  const common = {
    schema: BUILDING_CAPTURE_REPORT_SCHEMA,
    label: "baseline",
    completed: true,
    camera_plan: { sha256: "b".repeat(64) },
    scene_document: {
      served: { sha256: "1".repeat(64), bytes: 2048 },
    },
    manifest: {
      scene_id: "scene",
      objects_json_sha256: "c".repeat(64),
    },
    bundle_instrumentation: { source_sha256: "d".repeat(64) },
    renderer_query: {
      render_backend: "hardware",
      webgl_vendor: "vendor",
      webgl_renderer: "renderer",
      webgl_version: "version",
      canvas_buffer: [1600, 900],
      canvas_css: [1600, 900],
      local_reflection_owners: 1,
      reflections_enabled: true,
      reflection_profile: {
        probeSizePx: 256,
        reflectiveRadiusM: 75,
        captureFarM: 140,
        maxReflectiveBuildings: 12,
        cubeFacesPerRefresh: 6,
      },
    },
    frames,
    geometry_measurements: [
      {
        object_id: "building.way.1.component.0",
        source: { sha256: "e".repeat(64), bytes: 1024 },
      },
    ],
  };
  const baseline = {
    ...structuredClone(common),
    building_variant: {
      kind: "controlled-subject-baseline",
      served_candidate_manifest_sha256: candidateManifest,
    },
    private_route_overrides: { enabled: true },
  };
  const candidate = {
    ...structuredClone(common),
    label: "candidate",
    building_variant: {
      kind: "served-candidate",
      served_candidate_manifest_sha256: candidateManifest,
    },
    manifest: { ...common.manifest, sha256: candidateManifest },
    private_route_overrides: { enabled: false },
  };
  assert.deepEqual(verifyMatchedCaptureReports(baseline, candidate), {
    baseline_label: "baseline",
    candidate_manifest_sha256: candidateManifest,
    application_bundle_sha256: "d".repeat(64),
    camera_plan_sha256: "b".repeat(64),
    matched_frames: 12,
    runtime_identity_match: true,
  });
  const changedBundle = structuredClone(candidate);
  changedBundle.bundle_instrumentation.source_sha256 = "f".repeat(64);
  assert.throws(
    () => verifyMatchedCaptureReports(baseline, changedBundle),
    /application bundle/,
  );
  const changedCamera = structuredClone(candidate);
  changedCamera.frames["roof-far-dusk"].camera_state_sha256 = "changed";
  assert.throws(
    () => verifyMatchedCaptureReports(baseline, changedCamera),
    /camera state/,
  );
});
