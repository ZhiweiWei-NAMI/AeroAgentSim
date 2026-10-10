#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const [repoArg, outputArg, baseUrlArg] = process.argv.slice(2);
if (!repoArg || !outputArg) {
  console.error('Usage: node export-bike-only.mjs <AERO_BENCH-root> <output.glb> [frontend-base-url]');
  process.exit(2);
}

const repoRoot = path.resolve(repoArg);
const outputPath = path.resolve(outputArg);
const baseUrl = (baseUrlArg || 'http://127.0.0.1:5174').replace(/\/$/, '');
const frontendRoot = path.join(repoRoot, 'frontend');
const assetRoot = path.join(frontendRoot, 'public/models/incoming/urban-traffic');
const sourceGuid = '347f1e1f374fb2445993538838586103';
const sourceFbx = `/models/incoming/urban-traffic/fbx/${sourceGuid}.fbx`;
const expectedNames = [
  'Bicycle_LOD0', 'Wheel_front_LOD0', 'Wheel_back_LOD0',
  'Pedaly_LOD0', 'Pedal_Left_LOD0', 'Pedal_right_LOD0',
].sort();
await fs.mkdir(path.dirname(outputPath), {recursive: true});

const aliases = JSON.parse(await fs.readFile(path.join(assetRoot, 'texture-aliases.json'), 'utf8'));
const alphaUrl = aliases['bicycle_alpha_d.png'];
if (!alphaUrl) throw new Error('texture-aliases.json has no bicycle_alpha_d.png entry');
const alphaSourcePath = path.join(frontendRoot, 'public', alphaUrl.replace(/^\/+/, ''));
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const temporaryAlphaDir = await fs.mkdtemp(path.join(os.tmpdir(), 'aero-bike-alpha-'));
const bakedAlphaPath = path.join(temporaryAlphaDir, 'baked.png');
execFileSync('python3', [path.join(scriptDir, 'bake-bicycle-alpha.py'), alphaSourcePath, bakedAlphaPath], {stdio: 'inherit'});
const bakedAlphaPng = await fs.readFile(bakedAlphaPath);
const {chromium} = await import(pathToFileURL(path.join(frontendRoot, 'node_modules/playwright/index.mjs')).href);

function replaceAlphaImage(glb, png) {
  if (glb.toString('ascii', 0, 4) !== 'glTF' || glb.readUInt32LE(4) !== 2) throw new Error('Expected GLB v2');
  let offset = 12, jsonBytes, binBytes;
  while (offset < glb.length) {
    const length = glb.readUInt32LE(offset), type = glb.readUInt32LE(offset + 4);
    offset += 8;
    const chunk = glb.subarray(offset, offset + length);
    offset += length;
    if (type === 0x4e4f534a) jsonBytes = chunk;
    if (type === 0x004e4942) binBytes = chunk;
  }
  if (!jsonBytes || !binBytes) throw new Error('GLB missing JSON or BIN chunk');
  const doc = JSON.parse(jsonBytes.toString('utf8'));
  const alphaMaterial = doc.materials.find(m => m.name === 'Bicycle_alpha');
  const textureIndex = alphaMaterial?.pbrMetallicRoughness?.baseColorTexture?.index;
  const imageIndex = doc.textures?.[textureIndex]?.source;
  const image = doc.images?.[imageIndex];
  const view = doc.bufferViews?.[image?.bufferView];
  if (!view || view.buffer !== 0) throw new Error('Cannot locate embedded Bicycle_alpha texture');
  const imageOffset = view.byteOffset || 0;
  if (imageOffset + view.byteLength !== binBytes.length) throw new Error('Expected Bicycle_alpha PNG at the end of the GLB BIN chunk');
  view.byteLength = png.length;
  doc.buffers[0].byteLength = imageOffset + png.length;
  const newBin = Buffer.concat([binBytes.subarray(0, imageOffset), png]);
  const paddedBin = Buffer.concat([newBin, Buffer.alloc((4 - newBin.length % 4) % 4)]);
  const json = Buffer.from(JSON.stringify(doc));
  const paddedJson = Buffer.concat([json, Buffer.alloc((4 - json.length % 4) % 4, 0x20)]);
  const totalLength = 12 + 8 + paddedJson.length + 8 + paddedBin.length;
  const header = Buffer.alloc(12); header.write('glTF'); header.writeUInt32LE(2, 4); header.writeUInt32LE(totalLength, 8);
  const jsonHeader = Buffer.alloc(8); jsonHeader.writeUInt32LE(paddedJson.length); jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  const binHeader = Buffer.alloc(8); binHeader.writeUInt32LE(paddedBin.length); binHeader.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([header, jsonHeader, paddedJson, binHeader, paddedBin]);
}

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('requestfailed', request => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
  await page.exposeFunction('writeBikeGlb', async base64 => {
    const exported = Buffer.from(base64, 'base64');
    const finalGlb = replaceAlphaImage(exported, bakedAlphaPng);
    await fs.writeFile(outputPath, finalGlb);
    return {bytes: finalGlb.length};
  });
  await page.route(`**/${path.basename(outputPath)}`, async route => route.fulfill({
    status: 200,
    contentType: 'model/gltf-binary',
    body: await fs.readFile(outputPath),
  }));
  await page.goto(`${baseUrl}/asset-library.html`, {waitUntil: 'domcontentloaded'});
  const bakedDataUrl = `data:image/png;base64,${bakedAlphaPng.toString('base64')}`;
  const audit = await page.evaluate(async ({sourceFbx, bakedDataUrl, expectedNames, outputName}) => {
    const THREE = await import('/node_modules/.vite/deps/three.js');
    const {FBXLoader} = await import('/node_modules/three/examples/jsm/loaders/FBXLoader.js');
    const {GLTFExporter} = await import('/node_modules/three/examples/jsm/exporters/GLTFExporter.js');
    const {GLTFLoader} = await import('/node_modules/three/examples/jsm/loaders/GLTFLoader.js');
    const aliases = await (await fetch('/models/incoming/urban-traffic/texture-aliases.json')).json();
    const manager = new THREE.LoadingManager();
    manager.setURLModifier(url => {
      const name = decodeURIComponent(url.replaceAll('\\', '/').split('/').pop() || '').toLowerCase();
      return aliases[name] || url;
    });
    const texturesLoaded = new Promise((resolve, reject) => {
      manager.onLoad = resolve;
      manager.onError = url => reject(new Error(`Texture failed: ${url}`));
    });
    const model = await new FBXLoader(manager).loadAsync(sourceFbx);
    await Promise.race([texturesLoaded, new Promise((_, reject) => setTimeout(() => reject(new Error('Texture loading timed out')), 20000))]);
    const meshes = [];
    model.traverse(node => { if (node.isMesh) meshes.push(node); });
    const selected = meshes.filter(node => node.name.endsWith('_LOD0') && !node.isSkinnedMesh && !/^Man_/i.test(node.name));
    const names = selected.map(node => node.name).sort();
    if (JSON.stringify(names) !== JSON.stringify(expectedNames)) throw new Error(`Unexpected bicycle LOD0 meshes: ${names.join(', ')}`);
    const keep = new Set();
    for (const mesh of selected) for (let node = mesh; node; node = node.parent) { keep.add(node); if (node === model) break; }
    const prune = node => { for (const child of [...node.children]) { if (keep.has(child)) prune(child); else node.remove(child); } };
    prune(model);
    model.name = 'Bicycle_Man_34_Bike_LOD0_Only_Rider_Excluded';
    model.userData = {description: '仅自行车 LOD0，不包含骑手网格。'};
    const image = new Image(); image.src = bakedDataUrl; await image.decode();
    const bakedMapCache = new Map();
    const converted = new Map();
    const close = (a, b) => Math.abs(a - b) < 1e-8;
    const sameTransform = (a, b) => {
      a.updateMatrix(); b.updateMatrix();
      return a.channel === b.channel && a.flipY === b.flipY && a.wrapS === b.wrapS && a.wrapT === b.wrapT &&
        ['x', 'y'].every(k => close(a.offset[k], b.offset[k]) && close(a.repeat[k], b.repeat[k]) && close(a.center[k], b.center[k])) &&
        close(a.rotation, b.rotation) && a.matrix.elements.every((v, i) => close(v, b.matrix.elements[i]));
    };
    const getBakedMap = old => {
      if (bakedMapCache.has(old)) return bakedMapCache.get(old);
      if (!old.map || !old.alphaMap || !sameTransform(old.map, old.alphaMap)) throw new Error(`map/alphaMap mismatch on ${old.name}`);
      const baked = new THREE.CanvasTexture(image);
      baked.name = `${old.map.name || old.name}_baked_AxG`;
      for (const key of ['colorSpace', 'flipY', 'channel', 'wrapS', 'wrapT', 'magFilter', 'minFilter', 'anisotropy', 'generateMipmaps', 'premultiplyAlpha', 'unpackAlignment']) baked[key] = old.map[key];
      baked.offset.copy(old.map.offset); baked.repeat.copy(old.map.repeat); baked.center.copy(old.map.center); baked.rotation = old.map.rotation;
      baked.matrixAutoUpdate = old.map.matrixAutoUpdate; baked.matrix.copy(old.map.matrix);
      baked.userData = {...old.map.userData, mimeType: 'image/png'}; baked.needsUpdate = true;
      bakedMapCache.set(old, baked); return baked;
    };
    const standard = old => {
      if (converted.has(old)) return converted.get(old);
      const map = old.alphaMap ? getBakedMap(old) : old.map || null;
      const material = new THREE.MeshStandardMaterial({
        name: old.name || 'SourceMaterial', color: old.color?.clone?.() || new THREE.Color(0xffffff), map,
        transparent: old.transparent, opacity: old.opacity, alphaTest: old.alphaTest, side: old.side,
        roughness: old.roughness ?? Math.sqrt(2 / ((old.shininess ?? 30) + 2)), metalness: old.metalness ?? 0,
        vertexColors: old.vertexColors, emissive: old.emissive?.clone?.() || new THREE.Color(0),
        emissiveMap: old.emissiveMap || null, normalMap: old.normalMap || null,
        normalScale: old.normalScale?.clone?.(),
      });
      for (const key of ['map', 'emissiveMap', 'normalMap']) if (material[key]) {
        material[key].userData = {...material[key].userData, mimeType: 'image/png'};
        material[key].needsUpdate = true;
      }
      converted.set(old, material); return material;
    };
    let vertices = 0, triangles = 0;
    for (const mesh of selected) {
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(standard) : standard(mesh.material);
      mesh.castShadow = true; mesh.receiveShadow = true; mesh.frustumCulled = false;
      vertices += mesh.geometry.attributes.position.count;
      triangles += mesh.geometry.index ? mesh.geometry.index.count / 3 : mesh.geometry.attributes.position.count / 3;
    }
    model.updateMatrixWorld(true);
    const sourceBounds = new THREE.Box3().setFromObject(model);
    const scene = new THREE.Scene(); scene.add(model);
    const glb = await new GLTFExporter().parseAsync(scene, {binary: true, onlyVisible: true, maxTextureSize: 2048, trs: false});
    let binary = ''; const bytes = new Uint8Array(glb);
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, Math.min(i + 0x8000, bytes.length)));
    const saved = await window.writeBikeGlb(btoa(binary));
    const result = await new GLTFLoader().loadAsync(`/${outputName}`);
    const json = result.parser.json;
    const meshNodes = (json.nodes || []).filter(node => node.mesh !== undefined).map(node => node.name).sort();
    const runtimeMeshes = []; result.scene.traverse(node => { if (node.isMesh) runtimeMeshes.push(node); });
    const riderNodes = (json.nodes || []).filter(node => /^Man_/i.test(node.name || '') || node.skin !== undefined);
    const resultBounds = new THREE.Box3().setFromObject(result.scene);
    const delta = Math.max(...sourceBounds.min.toArray().map((v, i) => Math.abs(v - resultBounds.min.toArray()[i])), ...sourceBounds.max.toArray().map((v, i) => Math.abs(v - resultBounds.max.toArray()[i])));
    const externalUris = [...(json.images || []), ...(json.buffers || [])].filter(item => item.uri).map(item => item.uri);
    if (JSON.stringify(meshNodes) !== JSON.stringify(expectedNames) || riderNodes.length || (json.skins || []).length || externalUris.length || delta > 1e-5) {
      throw new Error('GLB round-trip validation failed');
    }
    return {sourceGuid: '347f1e1f374fb2445993538838586103', meshNodes, vertices, triangles, primitiveCount: (json.meshes || []).reduce((n, mesh) => n + mesh.primitives.length, 0), skinCount: (json.skins || []).length, textureCount: (json.textures || []).length, embeddedImageCount: (json.images || []).length, externalUris, boundsDelta: delta, savedBytes: saved.bytes, browserErrors: []};
  }, {sourceFbx, bakedDataUrl, expectedNames, outputName: path.basename(outputPath)});
  audit.browserErrors = errors;
  if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);
  if (process.env.AERO_BIKE_AUDIT) await fs.writeFile(process.env.AERO_BIKE_AUDIT, JSON.stringify(audit, null, 2));
  const digest = createHash('sha256').update(await fs.readFile(outputPath)).digest('hex');
  console.log(JSON.stringify({...audit, outputPath, sha256: digest}, null, 2));
} finally {
  await browser.close();
  await fs.rm(temporaryAlphaDir, {recursive: true, force: true});
}
