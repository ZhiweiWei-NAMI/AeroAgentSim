const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

const BASE = process.env.AERO_ASSET_BASE || 'http://127.0.0.1:5173';
const EVIDENCE = process.env.AERO_EXPORT_EVIDENCE || '/tmp/aero-vehicle-car-6';
const OUTPUT = path.resolve(__dirname, '../public/models/incoming/urban-traffic/glb/Car_6-preview.glb');
const MAPPING = JSON.parse(fs.readFileSync(path.join(__dirname, 'incoming-car6-material-map.json'), 'utf8'));
const MODEL = '/models/incoming/urban-traffic/fbx/314e4aab42e9d0b4c8f78c56bf020eec.fbx';

function toArrayBuffer(buffer) {
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

function inspectGlbJson(buffer) {
  const view = new DataView(toArrayBuffer(buffer));
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2) {
    throw new Error('Saved file does not have a GLB v2 header');
  }
  let offset = 12;
  const totalLength = view.getUint32(8, true);
  if (totalLength !== view.byteLength) throw new Error(`GLB length header ${totalLength} != file size ${view.byteLength}`);
  while (offset + 8 <= totalLength) {
    const chunkLength = view.getUint32(offset, true);
    const chunkType = view.getUint32(offset + 4, true);
    offset += 8;
    if (chunkType === 0x4e4f534a) {
      const jsonText = new TextDecoder().decode(new Uint8Array(view.buffer, offset, chunkLength)).trim();
      return JSON.parse(jsonText);
    }
    offset += chunkLength;
  }
  throw new Error('GLB JSON chunk missing');
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-webgl', '--no-proxy-server'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 900 }, deviceScaleFactor: 1 });
    page.on('pageerror', error => console.error(`PAGEERROR: ${error.message}`));
    await page.goto(`${BASE}/asset-library.html`, { waitUntil: 'domcontentloaded' });

    const result = await page.evaluate(async ({ modelUrl, mapping }) => {
      const [THREE, { FBXLoader }, { GLTFExporter }, { GLTFLoader }] = await Promise.all([
        import('/node_modules/three/build/three.module.js'),
        import('/node_modules/three/examples/jsm/loaders/FBXLoader.js'),
        import('/node_modules/three/examples/jsm/exporters/GLTFExporter.js'),
        import('/node_modules/three/examples/jsm/loaders/GLTFLoader.js'),
      ]);
      const manifest = await fetch('/models/incoming/manifest.json').then(response => {
        if (!response.ok) throw new Error(`Manifest HTTP ${response.status}`);
        return response.json();
      });
      const imageByName = new Map(manifest.entries
        .filter(item => item.image_url)
        .map(item => [item.package_path.split('/').at(-1), item.image_url]));
      const aliases = await fetch('/models/incoming/urban-traffic/texture-aliases.json').then(response => response.json());
      const manager = new THREE.LoadingManager();
      manager.setURLModifier(url => {
        const file = decodeURIComponent(url.replaceAll('\\', '/').split('/').pop().split(/[?#]/)[0]).toLowerCase();
        return aliases[file] ?? url;
      });
      const root = await new FBXLoader(manager).loadAsync(modelUrl);
      const loader = new THREE.TextureLoader();
      const loadedTextureRoles = [];
      const texture = async (name, role = 'color') => {
        if (!name) return null;
        const url = imageByName.get(name);
        if (!url) throw new Error(`Source texture missing from manifest: ${name}`);
        const loaded = await loader.loadAsync(url);
        loaded.colorSpace = role === 'normal' ? THREE.NoColorSpace : THREE.SRGBColorSpace;
        loadedTextureRoles.push({ name, role, colorSpace: loaded.colorSpace });
        return loaded;
      };
      const specs = mapping.materials;
      const lightMap = await texture(specs.Taxi_Light_D.albedo, 'color');
      const materials = {
        Taxi_Body_empty: new THREE.MeshStandardMaterial({
          name: 'Taxi_Body_empty',
          color: new THREE.Color(...specs.Taxi_Body_empty.color.slice(0, 3)),
          map: await texture(specs.Taxi_Body_empty.albedo, 'color'),
          normalMap: await texture(specs.Taxi_Body_empty.normal, 'normal'),
          normalScale: new THREE.Vector2(specs.Taxi_Body_empty.normal_scale, specs.Taxi_Body_empty.normal_scale),
          roughness: specs.Taxi_Body_empty.roughness,
          metalness: specs.Taxi_Body_empty.metalness,
        }),
        Taxi_Light_D: new THREE.MeshStandardMaterial({
          name: 'Taxi_Light_D', map: lightMap,
          normalMap: await texture(specs.Taxi_Light_D.normal, 'normal'),
          emissiveMap: lightMap,
          emissive: new THREE.Color(1, 1, 1),
          emissiveIntensity: Math.max(...specs.Taxi_Light_D.emissive_color),
          roughness: specs.Taxi_Light_D.roughness,
        }),
        glass: new THREE.MeshStandardMaterial({
          name: 'glass', color: new THREE.Color(...specs.glass.color.slice(0, 3)),
          roughness: 0, metalness: 1, transparent: true,
          opacity: specs.glass.color[3], side: THREE.DoubleSide,
        }),
        glass_light: new THREE.MeshStandardMaterial({
          name: 'glass_light', color: new THREE.Color(...specs.glass_light.color.slice(0, 3)),
          roughness: 0, metalness: 0.5, transparent: true,
          opacity: specs.glass_light.color[3], side: THREE.DoubleSide,
        }),
        disk_sht6: new THREE.MeshStandardMaterial({
          name: 'disk_sht6', map: await texture(specs.disk_sht6.albedo, 'color'),
          normalMap: await texture(specs.disk_sht6.normal, 'normal'), roughness: 0.167,
        }),
        Tire_1: new THREE.MeshStandardMaterial({
          name: 'Tire_1', map: await texture(specs.Tire_1.albedo, 'color'),
          normalMap: await texture(specs.Tire_1.normal, 'normal'), roughness: 1, metalness: 0.451,
        }),
      };
      const groupRemaps = [];
      const hiddenLodMeshes = [];
      root.traverse(mesh => {
        if (!mesh.isMesh) return;
        if (mesh.name.endsWith('LOD1')) {
          mesh.visible = false;
          hiddenLodMeshes.push(mesh.name);
          return;
        }
        const assignedNames = mapping.renderers[mesh.name];
        if (!assignedNames) throw new Error(`No Prefab Renderer assignment for visible LOD mesh ${mesh.name}`);
        const assignedMaterials = assignedNames.map(name => {
          if (!materials[name]) throw new Error(`No source material loaded for ${name}`);
          return materials[name];
        });
        const sourceOrder = [...new Set(mesh.geometry.groups.map(group => group.materialIndex))];
        if (sourceOrder.length !== assignedMaterials.length) {
          throw new Error(`${mesh.name}: ${sourceOrder.length} used FBX material groups but Prefab assigns ${assignedMaterials.length} materials`);
        }
        for (const group of mesh.geometry.groups) group.materialIndex = sourceOrder.indexOf(group.materialIndex);
        mesh.material = assignedMaterials;
        groupRemaps.push({ mesh: mesh.name, sourceGroupOrder: sourceOrder, prefabMaterialOrder: assignedNames });
      });

      root.updateMatrixWorld(true);
      const originalBounds = new THREE.Box3().setFromObject(root);
      const originalSize = originalBounds.getSize(new THREE.Vector3());
      root.scale.multiplyScalar(3.1 / Math.max(originalSize.x, originalSize.y, originalSize.z));
      root.updateMatrixWorld(true);
      const scaledBounds = new THREE.Box3().setFromObject(root);
      root.position.sub(scaledBounds.getCenter(new THREE.Vector3()));
      root.updateMatrixWorld(true);

      const stats = target => {
        const meshNames = [];
        const objectNames = [];
        const materialsByName = {};
        let triangles = 0;
        target.traverse(child => {
          if (child.name) objectNames.push(child.name);
          if (!child.isMesh || !child.visible) return;
          meshNames.push(child.name);
          triangles += (child.geometry.index?.count ?? child.geometry.attributes.position.count) / 3;
          for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
            materialsByName[material.name] = {
              colorMap: Boolean(material.map),
              colorMapSpace: material.map?.colorSpace ?? null,
              normalMap: Boolean(material.normalMap),
              normalMapSpace: material.normalMap?.colorSpace ?? null,
              emissiveMap: Boolean(material.emissiveMap),
              emissiveColor: material.emissive?.toArray() ?? null,
              emissiveIntensity: material.emissiveIntensity ?? null,
              roughness: material.roughness,
            };
          }
        });
        return {
          meshNames,
          objectNames,
          triangles,
          materials: materialsByName,
          bounds: new THREE.Box3().setFromObject(target).getSize(new THREE.Vector3()).toArray(),
        };
      };
      const sourceStats = stats(root);
      const raw = await new GLTFExporter().parseAsync(root, { binary: true, onlyVisible: true });
      if (!(raw instanceof ArrayBuffer)) throw new Error('GLTFExporter returned non-binary output');
      const loaded = await new Promise((resolve, reject) =>
        new GLTFLoader().parse(raw, '', value => resolve(value.scene), reject));
      const roundtripStats = stats(loaded);
      if (sourceStats.meshNames.length !== 5) {
        throw new Error(`Expected five source LOD0 renderer meshes, got ${sourceStats.meshNames.length}`);
      }
      if (sourceStats.triangles !== roundtripStats.triangles) {
        throw new Error(`GLB round-trip changed triangles: ${sourceStats.triangles} -> ${roundtripStats.triangles}`);
      }
      const missingRenderers = groupRemaps.map(item => item.mesh).filter(name => !roundtripStats.objectNames.includes(name));
      if (missingRenderers.length) throw new Error(`GLTFLoader round-trip lost source renderer nodes: ${missingRenderers.join(', ')}`);
      if (loadedTextureRoles.filter(item => item.role === 'color').length !== 4
        || loadedTextureRoles.filter(item => item.role === 'normal').length !== 4) {
        throw new Error('Expected four sRGB color textures and four linear normal maps');
      }
      for (const [name, role] of [['Taxi_Body_empty', 'normalMap'], ['Taxi_Light_D', 'normalMap'], ['disk_sht6', 'normalMap'], ['Tire_1', 'normalMap'], ['Taxi_Light_D', 'emissiveMap']]) {
        if (!roundtripStats.materials[name]?.[role]) throw new Error(`GLTFLoader round-trip lost ${name}.${role}`);
      }
      for (const name of ['Taxi_Body_empty', 'Taxi_Light_D', 'disk_sht6', 'Tire_1']) {
        if (!roundtripStats.materials[name]?.colorMap) throw new Error(`GLTFLoader round-trip lost ${name} color texture`);
      }

      const canvas = document.createElement('canvas');
      canvas.id = 'car6-glb-roundtrip';
      canvas.width = 1500;
      canvas.height = 900;
      canvas.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;z-index:99999';
      document.body.append(canvas);
      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      renderer.setPixelRatio(1);
      renderer.setSize(1500, 900, false);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.25;
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x101d2a);
      scene.add(new THREE.HemisphereLight(0xd9efff, 0x324353, 1.6));
      const key = new THREE.DirectionalLight(0xffffff, 2.5);
      key.position.set(4, 7, 5);
      scene.add(key);
      const fill = new THREE.DirectionalLight(0x7ac9e8, 1.1);
      fill.position.set(-5, 2, -4);
      scene.add(fill);
      const grid = new THREE.GridHelper(7, 14, 0x3b6376, 0x274152);
      grid.position.y = -1.36;
      scene.add(grid);
      scene.add(loaded);
      const camera = new THREE.PerspectiveCamera(42, 1500 / 900, 0.01, 100);
      camera.position.set(4.6, 2.6, 5.4);
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
      await new Promise(resolve => setTimeout(resolve, 300));
      renderer.render(scene, camera);

      const bytes = new Uint8Array(raw);
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return {
        base64: btoa(binary),
        sourceStats,
        roundtripStats,
        hiddenLodMeshes,
        groupRemaps,
        loadedTextureRoles,
      };
    }, { modelUrl: MODEL, mapping: MAPPING });

    const glb = Buffer.from(result.base64, 'base64');
    const json = inspectGlbJson(glb);
    if (json.images?.length !== 8 || json.images.some(image => !Number.isInteger(image.bufferView))) {
      throw new Error(`Expected eight embedded GLB images; found ${json.images?.length ?? 0}`);
    }
    if (json.meshes?.length !== 5) throw new Error(`Expected five glTF mesh definitions (LOD0 source renderers); found ${json.meshes?.length ?? 0}`);
    fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
    fs.mkdirSync(EVIDENCE, { recursive: true });
    fs.writeFileSync(OUTPUT, glb);
    await page.locator('#car6-glb-roundtrip').screenshot({ path: `${EVIDENCE}/Car_6-glb-roundtrip.png` });
    const report = {
      inputModel: MODEL,
      output: 'Car_6-preview.glb',
      outputBytes: glb.byteLength,
      glbAsset: json.asset,
      embeddedImageCount: json.images.length,
      imagesEmbeddedAsBufferViews: json.images.every(image => Number.isInteger(image.bufferView)),
      gltfMeshDefinitionCount: json.meshes.length,
      sourceStats: result.sourceStats,
      roundtripStats: result.roundtripStats,
      hiddenLodMeshes: result.hiddenLodMeshes,
      groupRemaps: result.groupRemaps,
      loadedTextureRoles: result.loadedTextureRoles,
      screenshot: 'Car_6-glb-roundtrip.png',
    };
    fs.writeFileSync(`${EVIDENCE}/glb-roundtrip-report.json`, `${JSON.stringify(report, null, 2)}\n`);
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
})();
