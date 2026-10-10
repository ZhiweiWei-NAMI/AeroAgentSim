import { chromium } from "playwright";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = resolve(process.argv[2] ?? join(frontend, "public/osm2world/shanghai-hongqiao.osm.json"));
const output = resolve(process.argv[3] ?? join(frontend, "public/osm2world/packs/shanghai"));
const origin = new URL(process.argv[4] ?? "http://127.0.0.1:4175");
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const source = await readFile(sourcePath);
const orderingHelper = await readFile(join(frontend, "scripts/osm2world-pack-order.mjs"));
const provenance = JSON.parse(await readFile(join(frontend, "public/osm2world/runtime-provenance.json"), "utf8"));
// The shared WorldPackage frame is authoritative. OSM2World derives a local
// origin from node bounds, so packed vertices are translated into the exact
// Shanghai reference frame before they are persisted. This preserves the
// official converter output while making mesh, Gazebo, SUMO, and ns-3 frames
// agree at the byte-bound manifest boundary.
const declaredOrigin = {
  latitude_deg: Number(process.argv[5] ?? 31.2304),
  longitude_deg: Number(process.argv[6] ?? 121.4737),
};
if (!Number.isFinite(declaredOrigin.latitude_deg) || Math.abs(declaredOrigin.latitude_deg) > 90
    || !Number.isFinite(declaredOrigin.longitude_deg) || Math.abs(declaredOrigin.longitude_deg) > 180) {
  throw Error("Pack origin must be valid latitude and longitude degrees");
}
const runtime = await readFile(join(frontend, "public/osm2world/osm2world-core-web.mjs"));
const patch = await readFile(join(frontend, "../tools/osm2world/web-integration.patch"));
if (digest(runtime) !== provenance.runtime_sha256 || digest(patch) !== provenance.patch_sha256) throw Error("runtime/patch provenance mismatch; rebuild before publishing");
await mkdir(dirname(output), { recursive: true });
await mkdir(output); // Never overwrite a previously published pack.
await mkdir(join(output, "assets"));
const storedFiles = new Map();
const store = async bytes => {
  const sha256 = digest(bytes);
  if (!storedFiles.has(sha256)) {
    await writeFile(join(output, "assets", sha256), bytes, { flag: "wx" });
    storedFiles.set(sha256, bytes.length);
  }
  return { sha256, size_bytes: bytes.length };
};
const browser = await chromium.launch({ headless: true });
let manifest;
const diagnostics = [];
try {
  const page = await browser.newPage();
  page.on("console", message => { if (/^(ERROR|WARNING)\[/.test(message.text())) diagnostics.push(message.text()); });
  page.on("pageerror", error => diagnostics.push(error.message));
  await page.route("**/pack-builder", route => route.fulfill({ contentType: "text/html",
    body: '<!doctype html><title>Official OSM2World pack publication</title><script type="importmap">{"imports":{"three":"/node_modules/three/build/three.module.js"}}</script>' }));
  await page.route("**/pack-order.mjs", route => route.fulfill({
    contentType: "text/javascript", body: orderingHelper,
  }));
  await page.route("**/pack-source.json", route => route.fulfill({ contentType: "application/json", body: source }));
  await page.exposeFunction("publishMeshBytes", async encoded => store(Buffer.from(encoded, "base64")));
  await page.goto(new URL("/pack-builder", origin).href);
  manifest = await page.evaluate(async (declaredOrigin) => {
    const THREE = await import("/node_modules/three/build/three.module.js");
    const { mergeGeometries } = await import("/node_modules/three/examples/jsm/utils/BufferGeometryUtils.js");
    const { convertOsmJson, resolveTextureUrl } = await import("/src/osm2world/runtime.ts");
    const { parseOsmJson } = await import("/src/osm2world/source.ts");
    const { converterOrigin, osmExtent, projectGeographic } = await import("/src/osm2world/projection.ts");
    const { meshGeometry, meshIdentity } = await import("/src/osm2world/mesh.ts");
    const { compareBatchEntries, compareMeshRecords } = await import("/pack-order.mjs");
    const osm = parseOsmJson(await (await fetch("/pack-source.json")).json());
    const meshes = await convertOsmJson(JSON.stringify(osm));
    const converterOriginValue = converterOrigin(osm);
    const originShift = projectGeographic(
      converterOriginValue.latitude_deg, converterOriginValue.longitude_deg, declaredOrigin);
    const elements = new Map(osm.elements.map(element => [`${element.type[0]}${element.id}`, element]));
    const batches = new Map();
    for (const mesh of meshes) {
      const geometry = meshGeometry(mesh);
      const positions = geometry.attributes.position.array;
      for (let index = 0; index < positions.length; index += 3) {
        positions[index] += originShift.east;
        // OSM2World's MetricMapProjection uses south-positive Z.
        positions[index + 2] -= originShift.north;
      }
      geometry.attributes.position.needsUpdate = true;
      const identity = meshIdentity(mesh, elements);
      const color = mesh.color();
      const material = {
        color: [color[0], color[1], color[2]],
        base_color_texture: resolveTextureUrl(mesh.baseColorTexture()),
        normal_texture: resolveTextureUrl(mesh.normalTexture()),
        orm_texture: resolveTextureUrl(mesh.ormTexture()),
        opacity_texture: resolveTextureUrl(mesh.opacityTexture()),
        transparent: mesh.transparency(), clamp: mesh.clampTextures(),
      };
      const key = JSON.stringify([identity.layer, material]);
      let batch = batches.get(key);
      if (!batch) { batch = { layer: identity.layer, material, records: [] }; batches.set(key, batch); }
      batch.records.push({
        target: identity.target,
        geometry,
        arrays: [geometry.attributes.position.array, geometry.attributes.normal.array,
          geometry.attributes.uv.array, geometry.index.array],
      });
    }
    const packed = [];
    for (const [, batch] of [...batches.entries()].sort(compareBatchEntries)) {
      batch.records.sort(compareMeshRecords);
      const geometries = batch.records.map(record => record.geometry);
      const geometry = mergeGeometries(geometries);
      if (geometry === null) throw Error("official geometry cannot be batched");
      let triangles = 0;
      const ranges = batch.records.map(record => {
        triangles += record.geometry.index.count / 3;
        return { end: triangles, target: record.target };
      });
      const arrays = [geometry.attributes.position.array, geometry.attributes.normal.array, geometry.attributes.uv.array, new Uint32Array(geometry.index.array)];
      const buffer = new Uint8Array(arrays.reduce((sum, array) => sum + array.byteLength, 0));
      let offset = 0;
      for (const array of arrays) { buffer.set(new Uint8Array(array.buffer, array.byteOffset, array.byteLength), offset); offset += array.byteLength; }
      if (buffer.length > 64 * 1024 * 1024) throw Error("mesh batch exceeds bounded pack chunk; spatially partition the source before publication");
      let binary = "";
      for (let start = 0; start < buffer.length; start += 32768) binary += String.fromCharCode(...buffer.subarray(start, start + 32768));
      const file = await globalThis.publishMeshBytes(btoa(binary));
      packed.push({ file, vertices: geometry.attributes.position.count, indices: geometry.index.count,
        layer: batch.layer, material: batch.material, ranges });
      for (const part of geometries) part.dispose();
      geometry.dispose();
    }
    return {
      projection: { name: "MetricMapProjection", axes: "east-up-south", origin: declaredOrigin },
      coordinate_contract: {
        schema_version: "aero-bench.osm2world-source-coordinates/v1",
        recipe: "source-node-bounds-local-Mercator-then-declared-origin-translation",
        converter_origin: converterOriginValue,
        stored_translation_xz_m: [originShift.east, -originShift.north],
        earth_circumference_m: 40075016.686,
        native_point_quantization_m: 0.001,
        storage: "source-mesh-float32-converter-coordinates-then-declared-origin-translation",
      },
      extent: osmExtent(osm, declaredOrigin), original_mesh_count: meshes.length, batches: packed,
      objects: osm.elements.filter(element => element.tags !== undefined).map(element => ({ id: `${element.type[0]}${element.id}`, tags: element.tags })),
    };
  }, declaredOrigin);
  if (diagnostics.length) throw Error(`Official conversion reported diagnostics: ${diagnostics.join("; ")}`);
  const paths = new Set(["/osm2world/style/textures/sky/DaySkyHDRI041B_4K_TONEMAPPED.jpg"]);
  for (const batch of manifest.batches) for (const key of ["base_color_texture", "normal_texture", "orm_texture", "opacity_texture"]) if (batch.material[key] !== null) paths.add(batch.material[key]);
  const textures = {};
  for (const path of paths) {
    if (!path.startsWith("/osm2world/style/textures/") || path.split("/").includes("..")) throw Error("texture escapes bundled style");
    textures[path] = await store(await readFile(join(frontend, "public", path.slice(1))));
  }
  const properties = await readFile(join(frontend, "public/osm2world/style/standard.properties"));
  const configSource = await readFile(join(frontend, "src/osm2world/runtime.ts"));
  const producerSource = await readFile(fileURLToPath(import.meta.url));
  const projectionSource = await readFile(join(frontend, "src/osm2world/projection.ts"));
  manifest = {
    schema_version: "aero-bench.osm2world-mesh-pack/v1",
    source: await store(source),
    generator: { runtime_sha256: provenance.runtime_sha256, patch_sha256: provenance.patch_sha256, revision: provenance.revision, config_sha256: digest(Buffer.concat([properties, configSource])) },
    ...manifest, textures,
  };
  manifest.coordinate_contract.source_json_sha256 = manifest.source.sha256;
  manifest.coordinate_contract.producer_sha256 = digest(producerSource);
  manifest.coordinate_contract.projection_helper_sha256 = digest(projectionSource);
  const encoded = Buffer.from(JSON.stringify(manifest) + "\n");
  // Validate the exact artifact, not an independently inferred manifest.
  await page.evaluate(async value => { const { parseMeshPack } = await import("/src/osm2world/pack.ts"); parseMeshPack(value); }, manifest);
  await store(encoded);
  await writeFile(join(output, "manifest.json"), encoded, { flag: "wx" });
  await writeFile(join(output, "manifest.lock.json"), JSON.stringify({ sha256: digest(encoded), size_bytes: encoded.length }) + "\n", { flag: "wx" });
  console.log(JSON.stringify({ output, manifest_sha256: digest(encoded), bytes: [...storedFiles.values()].reduce((a, b) => a + b, 0), meshes: manifest.original_mesh_count, batches: manifest.batches.length }, null, 2));
} finally {
  await browser.close();
}
