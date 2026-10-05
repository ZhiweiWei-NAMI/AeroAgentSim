/** Build private, immutable capture bundles with read-only map inspection hooks. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "vite";

export const CAPTURE_BUILD_SCHEMA = "aero-bench.native-capture-build/v1";
const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hooks = new Map([
  [resolve(frontendRoot, "src/city-studio.ts"), "__aeroStudioMap"],
  [resolve(frontendRoot, "src/app.ts"), "__aeroVisualMap"],
]);
const digest = bytes => createHash("sha256").update(bytes).digest("hex");

export function instrumentMapSource(source, globalName) {
  assert(["__aeroStudioMap", "__aeroVisualMap"].includes(globalName), "Unknown capture hook");
  const token = "this.map = new PublicTraceMap(";
  assert.equal(source.split(token).length, 2, "Capture map hook must match exactly once");
  const appHook = globalName === "__aeroVisualMap" ? "window.__aeroCaptureApp = this;\n" : "";
  return `window.__aeroNativeCaptureBuild = ${JSON.stringify(CAPTURE_BUILD_SCHEMA)};\n`
    + source.replace(token, `${appHook}window.${globalName} = this.map = new PublicTraceMap(`);
}

export async function buildFrozenCapture(outputDir) {
  const absolute = resolve(outputDir);
  assert(absolute.startsWith(resolve(frontendRoot, "../validation/platform-plan-20261001/W4-NATIVE") + "/"),
    "Private capture builds must stay within the W4 evidence lane");
  await assert.rejects(access(absolute), { code: "ENOENT" }, "Never overwrite a frozen capture build");
  const transformed = [];
  const sourceHashes = new Map();
  const startedAt = new Date().toISOString();
  const result = await build({
    root: frontendRoot,
    configFile: resolve(frontendRoot, "vite.config.ts"),
    plugins: [{
      name: "w4-read-only-capture-inspection",
      enforce: "pre",
      transform(source, id) {
        if (id.startsWith(resolve(frontendRoot, "src") + "/")) {
          sourceHashes.set(relative(frontendRoot, id), digest(source));
        }
        const hook = hooks.get(id);
        if (hook === undefined) return null;
        transformed.push(relative(frontendRoot, id));
        return { code: instrumentMapSource(source, hook), map: null };
      },
    }],
    build: { outDir: absolute, emptyOutDir: false },
  });
  assert.deepEqual(transformed.sort(), ["src/app.ts", "src/city-studio.ts"]);
  const bundles = (Array.isArray(result) ? result : [result]).flatMap(item => item.output)
    .map(item => item.fileName);
  const artifacts = await Promise.all([...new Set(bundles)].sort().map(async file => {
    const bytes = await readFile(resolve(absolute, file));
    return { path: file, sizeBytes: bytes.length, sha256: digest(bytes) };
  }));
  const receipt = { schemaVersion: CAPTURE_BUILD_SCHEMA, startedAt,
    completedAt: new Date().toISOString(), scope: "private-frozen-capture-only",
    hooks: transformed, sourceHashes: Object.fromEntries([...sourceHashes].sort()), artifacts };
  await mkdir(absolute, { recursive: true });
  await writeFile(resolve(absolute, "capture-build-receipt.json"), JSON.stringify(receipt, null, 2) + "\n");
  return receipt;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  assert(process.argv[2], "Provide a new private build directory");
  const receipt = await buildFrozenCapture(process.argv[2]);
  process.stdout.write(JSON.stringify({ schemaVersion: receipt.schemaVersion,
    artifacts: receipt.artifacts.length, sources: Object.keys(receipt.sourceHashes).length }) + "\n");
}
