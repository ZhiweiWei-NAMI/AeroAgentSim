/** Local, read-only inventory of visual assets. CAD archives are never served. */
import { createReadStream, existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { basename, extname, join } from "node:path";

export const ASSET_CATALOG_PATH = "/asset-library/catalog.json";
const PREVIEW_PREFIX = "/asset-library/preview/";
const PREVIEW_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".bmp"]);
const STYLE_IMAGE_EXTENSIONS = new Set([...PREVIEW_EXTENSIONS, ".svg"]);
const QUARK_RELATIVE_ROOT = "validation/downloaded-assets/quark-drone-models/003 无人机";
const BIGCITY_RELATIVE_PATH = "validation/downloaded-assets/baidu-bigcity/BigCity.unitypackage";
const quarkRoot = frontendRoot => join(frontendRoot, "..", QUARK_RELATIVE_ROOT);

function curatedUavEntries(frontendRoot) {
  const manifest = JSON.parse(readFileSync(join(frontendRoot, "scripts/uav-preview-models.json"), "utf8"));
  if (!Array.isArray(manifest)) throw new Error("UAV preview model manifest must be an array");
  const previewDir = join(quarkRoot(frontendRoot), "0 预览图 格式备注 名称对应");
  const sourceImages = previewIndex(previewDir);
  return manifest.map(item => {
    if (typeof item.id !== "string" || typeof item.archive !== "string" || typeof item.output !== "string"
      || typeof item.title !== "string" || typeof item.subgroup !== "string" || typeof item.exposure !== "number") {
      throw new Error("Invalid UAV preview model entry");
    }
    const thumbnail = `public/models/uav/thumbnails/${item.id}.png`;
    const sourceImage = sourceImages.get(basename(item.archive, extname(item.archive)).toLowerCase());
    return {
      id: `model:${item.id}`, category: "uav", subgroup: item.subgroup, title: item.id === "holybro-x500" ? item.title : `${item.title}（预览）`,
      kind: "model", status: "ready", format: "GLB", model_url: `/models/uav/${item.output}`,
      preview_exposure: item.exposure,
      material_origin: item.source_3dxml ? "统一航空 PBR 涂装；保留 3DXML 图片与原色记录" : "统一航空 PBR 展示涂装（程序生成）",
      preview_url: sizeIfFile(join(frontendRoot, thumbnail)) === null ? null : `/models/uav/thumbnails/${item.id}.png`,
      reference_preview_url: sourceImage ? `${PREVIEW_PREFIX}${encodeURIComponent(sourceImage)}` : null,
      source_path: `public/models/uav/${item.output}`,
      origin_path: `${QUARK_RELATIVE_ROOT}/${item.archive}`,
      note: item.source_3dxml
        ? "源包 3DXML 的部件与 UV 用于统一涂装；原始颜色记录在 GLB，原包图片仍保留。下面的展示图供对照；涂装不代表原厂版本。"
        : item.id === "holybro-x500"
          ? "源包 STL 经自动分区后应用统一航空涂装。该涂装是展示方案，不是原厂贴图；正式运行使用原有 X500 模型。"
          : "源包 STL 经自动分区后应用统一航空涂装，尺寸按包络归一。该涂装是展示方案，不代表原厂配色。",
    };
  });
}

function meshUavEntries(frontendRoot) {
  const manifest = JSON.parse(readFileSync(join(frontendRoot, "scripts/uav-mesh-previews.json"), "utf8"));
  if (!Array.isArray(manifest)) throw new Error("UAV mesh preview manifest must be an array");
  const previews = previewIndex(join(quarkRoot(frontendRoot), "0 预览图 格式备注 名称对应"));
  return manifest.map(item => {
    if (typeof item.id !== "string" || typeof item.archive !== "string" || typeof item.output !== "string"
      || typeof item.title !== "string" || typeof item.subgroup !== "string" || typeof item.note !== "string"
      || typeof item.triangles !== "number" || item.triangles <= 0
      || (item.component_only !== undefined && typeof item.component_only !== "boolean")) {
      throw new Error("Invalid UAV mesh preview entry");
    }
    const sourcePath = `public/models/uav/mesh/${item.output}`;
    if (sizeIfFile(join(frontendRoot, sourcePath)) === null) {
      throw new Error(`Declared UAV mesh preview is missing: ${sourcePath}`);
    }
    const sourceImage = previews.get(basename(item.archive, extname(item.archive)).toLowerCase());
    const reference = sourceImage ? `${PREVIEW_PREFIX}${encodeURIComponent(sourceImage)}` : null;
    return {
      id: item.id, category: "uav", subgroup: item.subgroup, title: item.title,
      kind: "model", status: "ready", format: "GLB", model_url: `/models/uav/mesh/${item.output}`,
      preview_exposure: 0.58, preview_url: reference, reference_preview_url: reference,
      material_origin: item.component_only ? "源 STL 网格；中性灰色预览材质，无原厂涂装" : "源 STL 网格；统一航空 PBR 展示涂装",
      source_path: sourcePath, origin_path: `${QUARK_RELATIVE_ROOT}/${item.archive}`,
      note: item.note, details: { preview_triangles: item.triangles },
    };
  });
}

export function classifyUav(filename) {
  const name = filename.toLowerCase();
  if (/vtol|e-?vtol|tilt|vertical.take.off/.test(name)) return "垂直起降";
  if (/helicopter|helicoptor/.test(name)) return "直升机";
  if (/hexacopter|octocopter|tricopter|quadcopter|quad-?rotor|quadrotor|multirotor|multi-rotor|copter|x500|x-500|x8-/.test(name)) return "多旋翼";
  if (/fixed.?wing|airplane|plane|glider|scaneagle|predator|reaper|global.hawk|flying.wing|wingman|bayraktar|akinci/.test(name)) return "固定翼";
  return "待识别";
}

function regularFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).filter(entry => entry.isFile()).map(entry => entry.name);
}

function filesBelow(dir, relativePath = "") {
  if (!existsSync(dir)) return [];
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const relative = relativePath ? `${relativePath}/${entry.name}` : entry.name;
    if (entry.isFile()) found.push(relative);
    if (entry.isDirectory()) found.push(...filesBelow(join(dir, entry.name), relative));
  }
  return found;
}

function previewIndex(dir) {
  return new Map(regularFiles(dir)
    .filter(name => PREVIEW_EXTENSIONS.has(extname(name).toLowerCase()))
    .map(name => [basename(name, extname(name)).toLowerCase(), name]));
}

function sizeIfFile(path) {
  if (!existsSync(path)) return null;
  const stat = statSync(path);
  return stat.isFile() ? stat.size : null;
}

function cadConversion(frontendRoot) {
  const path = join(frontendRoot, "public/models/uav/cad-conversion-report.json");
  if (!existsSync(path)) return { models: [], byArchive: new Map() };
  const report = JSON.parse(readFileSync(path, "utf8"));
  if (report.schema_version !== "aero-bench.cad-conversion/v1" || !Array.isArray(report.entries)) {
    throw new Error("Invalid CAD conversion report");
  }
  const byArchive = new Map(report.entries.map(item => [item.archive, item]));
  const previews = previewIndex(join(quarkRoot(frontendRoot), "0 预览图 格式备注 名称对应"));
  const models = report.entries.filter(item => item.status === "converted").map(item => {
    const sourcePath = item.model_path;
    if (sizeIfFile(join(frontendRoot, sourcePath)) === null) {
      throw new Error(`Declared CAD preview is missing: ${sourcePath}`);
    }
    const id = `cad:${item.archive}`;
    const sourceImage = previews.get(basename(item.archive, extname(item.archive)).toLowerCase());
    const reference = sourceImage ? `${PREVIEW_PREFIX}${encodeURIComponent(sourceImage)}` : null;
    return {
      id, category: "uav", subgroup: classifyUav(item.archive), title: `${basename(item.archive, ".zip")}（CAD 预览）`,
      kind: "model", status: "ready", format: "GLB", model_url: `/${sourcePath.replace(/^public\//, "")}`,
      preview_exposure: 0.58, preview_url: reference, reference_preview_url: reference,
      material_origin: item.source_colors_preserved
        ? `源 STEP 的 ${item.source_color_count} 种面色；PBR 光泽度为预览设置`
        : "STEP/IGES 曲面网格化；统一航空 PBR 展示涂装",
      source_path: sourcePath, origin_path: `${QUARK_RELATIVE_ROOT}/${item.archive}`,
      note: `源包 ${item.selected_member} 已转成可旋转 GLB；整机完整性尚未确认。${item.source_surface_only ? "源 CAD 是开放曲面。" : ""}${item.source_style_note ? "源 STEP 包含颜色样式，本次 GLB 未保留。" : ""}${item.source_color_note ? `${item.source_color_note}。` : ""}${item.preview_quality_note ? `${item.preview_quality_note}。` : ""}${item.source_colors_preserved ? "随包展示图只供对照。" : "源包图片用于对照，涂装不代表原厂配色。"}`,
      details: { source_faces: item.cad_faces, source_solids: item.cad_solids, preview_triangles: item.preview_triangles },
    };
  });
  return { models, byArchive };
}

function conversionNote(result) {
  if (!result) return "源包已下载；待检查网格、单位和材质。";
  if (result.status === "converted") return `已从 ${result.selected_member} 转为 GLB；整机完整性尚未确认。`;
  if (result.status === "existing_preview") return "已有可旋转的 STL 或 3DXML 预览。";
  if (result.archive === "atmos-uav-project-1 2 STP.zip" && result.status === "neutral_components_require_assembly")
    return "ATMOS TWIN EAGLE.stp 有装配根和实体；浏览器预览网格仍在质量验收，尚未入库。";
  if (result.status === "neutral_components_require_assembly") return "包内中性 CAD 是分散零件，缺少可验证的总装位置；不能直接拼成整机。";
  if (result.archive === "large-uav-wip 3dm.zip" && result.status === "native_cad_requires_export")
    return "外层和 RAR 内 3DM 均已核对；BODY.3dm 的曲面没有缓存网格，本机缺少可用的 3DM 曲面网格化工具。";
  if (result.status === "native_cad_requires_export") return "包内没有可直接导入的中性 CAD 或网格；原生 CAD 需由相应软件导出 STEP 或 GLB。";
  if (result.status === "mesh_without_neutral_cad") return "包内有网格，需核对是否为完整机体及装配位置。";
  if (result.status === "queued") return "STEP/IGES 批量转换排队中。";
  if (result.status === "conversion_failed") return `CAD 导入失败：${result.attempt_errors?.[0]?.error ?? "未知错误"}`;
  if (result.status === "conversion_timeout") return `CAD 转换超时（${result.timeout_seconds} 秒）。`;
  if (result.status === "conversion_process_failed") return "CAD 转换进程失败，详见转换报告。";
  return "源包需要进一步检查；详见转换报告。";
}

function archiveEntries(frontendRoot, convertedByArchive, conversionByArchive = new Map()) {
  const manifest = JSON.parse(readFileSync(join(frontendRoot, "assets/incoming/quark/uav/manifest.json"), "utf8"));
  const expected = new Map();
  for (const item of [...manifest.files, ...manifest.blocked]) {
    if (!/^archives\/[^/\\]+\.zip$/i.test(item.path)) continue;
    expected.set(basename(item.path), item.size);
  }
  const archiveDir = quarkRoot(frontendRoot);
  for (const name of regularFiles(archiveDir)) {
    if (extname(name).toLowerCase() === ".zip" && !expected.has(name)) expected.set(name, null);
  }
  const previewDir = join(archiveDir, "0 预览图 格式备注 名称对应");
  const previews = previewIndex(previewDir);
  const entries = [];
  for (const [name, expectedBytes] of expected) {
    const actualBytes = sizeIfFile(join(archiveDir, name));
    const status = actualBytes === null ? "pending"
      : expectedBytes !== null && actualBytes !== expectedBytes ? "partial" : "downloaded";
    const previewName = previews.get(name.replace(/\.zip$/i, "").toLowerCase());
    const convertedModelId = convertedByArchive.get(name) ?? null;
    entries.push({
      id: `quark:${name}`, category: "uav", subgroup: classifyUav(name), title: basename(name, ".zip"),
      kind: "archive", status, format: "CAD ZIP", bytes: actualBytes ?? expectedBytes,
      preview_url: previewName ? `${PREVIEW_PREFIX}${encodeURIComponent(previewName)}` : null,
      converted_model_id: convertedModelId,
      source_path: `${QUARK_RELATIVE_ROOT}/${name}`,
      note: convertedModelId ? convertedModelId === "mesh:reconnaissance-hellfire-component"
          ? "仅包内 Hellfire 弹体组件可旋转查看；飞机整机仍需从原生 CAD 导出。"
        : conversionByArchive.get(name)?.status === "mesh_without_neutral_cad"
          ? "已从源 STL 转为可旋转的 GLB 预览；完整性和材质来源见对应模型说明。"
          : conversionByArchive.has(name) ? conversionNote(conversionByArchive.get(name)) : "此源包已有可旋转的 GLB 预览；右侧可直接打开。"
        : status === "downloaded" ? conversionNote(conversionByArchive.get(name))
        : status === "partial" ? "文件大小与清单不符，下载尚未完成或文件有误。" : "源包待下载；仅可查看预览图。",
    });
  }
  const archiveByStem = new Map([...expected.keys()].map(name => [basename(name, extname(name)).toLowerCase(), name]));
  for (const image of regularFiles(previewDir).filter(name => PREVIEW_EXTENSIONS.has(extname(name).toLowerCase()))) {
    const archiveName = archiveByStem.get(basename(image, extname(image)).toLowerCase());
    const convertedModelId = archiveName ? convertedByArchive.get(archiveName) ?? null : null;
    entries.push({
      id: `quark-image:${image}`, category: "uav", subgroup: classifyUav(image), title: basename(image, extname(image)),
      kind: "image", status: "available", format: extname(image).slice(1).toUpperCase(), bytes: sizeIfFile(join(previewDir, image)),
      preview_url: `${PREVIEW_PREFIX}${encodeURIComponent(image)}`,
      converted_model_id: convertedModelId,
      source_path: `${QUARK_RELATIVE_ROOT}/0 预览图 格式备注 名称对应/${image}`,
      note: archiveName ? `源包 ${archiveName} 对应的展示图。` : "源包目录中的独立展示图；没有对应的同名 ZIP。",
    });
  }
  return entries;
}

function cityEntries(frontendRoot) {
  const result = [];
  const packRoot = join(frontendRoot, "public/osm2world/packs");
  for (const name of regularDirectories(packRoot)) {
    const manifestPath = join(packRoot, name, "manifest.json");
    if (!existsSync(manifestPath)) continue;
    const data = JSON.parse(readFileSync(manifestPath, "utf8"));
    result.push({
      id: `city:${name}`, category: "city", subgroup: "OSM2World 场景包", title: name,
      kind: "scene", status: "available", format: "mesh pack",
      source_path: `public/osm2world/packs/${name}/manifest.json`,
      details: { meshes: data.original_mesh_count, batches: data.batches.length, objects: data.objects.length, textures: Object.keys(data.textures).length },
      note: "城市网格包；单独查看时需要场景加载器和相应 OSM 数据。",
    });
  }
  const textureRoot = join(frontendRoot, "public/osm2world/style/textures");
  for (const name of ["根目录", ...regularDirectories(textureRoot)]) {
    const directory = name === "根目录" ? textureRoot : join(textureRoot, name);
    const files = name === "根目录" ? regularFiles(directory) : filesBelow(directory);
    if (!files.length) continue;
    const sample = files.find(file => PREVIEW_EXTENSIONS.has(extname(file).toLowerCase()));
    result.push({
      id: `texture:${name}`, category: "city", subgroup: "纹理目录", title: name,
      kind: "texture", status: "available", format: "texture set", source_path: `public/osm2world/style/textures${name === "根目录" ? "" : `/${name}`}`,
      preview_url: sample ? `/osm2world/style/textures/${name === "根目录" ? "" : `${encodeURIComponent(name)}/`}${sample.split("/").map(encodeURIComponent).join("/")}` : null,
      details: { files: files.length }, note: "纹理目录，预览图为目录中的一张图片。",
    });
  }
  for (const relative of filesBelow(textureRoot).filter(file => STYLE_IMAGE_EXTENSIONS.has(extname(file).toLowerCase()))) {
    const encoded = relative.split("/").map(encodeURIComponent).join("/");
    const url = `/osm2world/style/textures/${encoded}`;
    const roadSurface = ["Asphalt010", "PavingStones072", "Concrete034"].some(name => relative.startsWith(`cc0textures/${name}/`));
    const subgroup = roadSurface ? "OSM2World · 路面材质"
      : relative.startsWith("road_marking_") ? "OSM2World · 道路标线"
      : relative.startsWith("signs-") ? "OSM2World · 道路标识"
      : relative.startsWith("cc0textures/") || relative.startsWith("custom/") ? "OSM2World · PBR 贴图"
        : "OSM2World · 常规纹理";
    result.push({
      id: `osm-texture:${relative}`, category: "city", subgroup, title: relative,
      kind: "texture", status: "available", format: extname(relative).slice(1).toUpperCase(),
      bytes: sizeIfFile(join(textureRoot, relative)), source_path: `public/osm2world/style/textures/${relative}`,
      preview_url: url, image_url: url,
      note: roadSurface
        ? "当前城市底图使用这组路面材质的颜色、法线和 ORM 贴图；位移图仅供查看，前端未启用。"
        : "OSM2World 样式中的原始纹理或标识图；可作为通用涂装的参考，具体贴法取决于模型 UV。",
    });
  }
  const baiduPath = join(frontendRoot, "..", BIGCITY_RELATIVE_PATH);
  const packageSize = sizeIfFile(baiduPath);
  result.push({
    id: "city:bigcity-unity", category: "city", subgroup: "Unity 源包", title: "BigCity.unitypackage",
    kind: "package", status: packageSize === 716606878 ? "downloaded" : "partial",
    format: "Unity package", bytes: packageSize,
    source_path: BIGCITY_RELATIVE_PATH,
    note: "本地 Unity 源包。下方分别列出包内 FBX、场景、纹理和其它资源。",
  });
  const bigcityManifest = JSON.parse(readFileSync(join(frontendRoot, "public/models/bigcity/manifest.json"), "utf8"));
  for (const item of bigcityManifest.entries) {
    const isModel = item.format === "FBX";
    const isScene = item.format === "UNITY";
    const hasImage = Boolean(item.image_url || item.preview_url);
    result.push({
      id: `bigcity:${item.guid}`, category: "city", subgroup: item.subgroup, title: item.title,
      kind: isModel ? "model" : isScene ? "scene" : hasImage ? "texture" : "package",
      status: item.model_url ? "ready" : "available", format: item.format, bytes: item.bytes,
      model_url: item.model_url, image_url: item.image_url, preview_url: item.preview_url,
      preview_exposure: isScene ? 1 : 1.2,
      material_origin: item.model_url ? "源 FBX 材质和可匹配的原包纹理；Unity 效果未完整还原" : undefined,
      source_path: `${BIGCITY_RELATIVE_PATH}#${item.package_path}`,
      note: isScene ? "浏览器显示源包 Scene.FBX 几何体；Unity 场景的灯光和后处理未导入。"
        : isModel ? "可旋转的原始 FBX 几何预览；Unity 材质和烘焙光照未完整还原。"
          : item.format === "EXR" ? "Unity 烘焙光照 EXR；显示包内缩略图，原始数据保存在源包中。"
            : "来自 BigCity.unitypackage 的原始资源；图片显示为浏览器预览版。",
    });
  }
  return result;
}

function incomingEntries(frontendRoot) {
  const path = join(frontendRoot, "public/models/incoming/manifest.json");
  if (!existsSync(path)) return [];
  const manifest = JSON.parse(readFileSync(path, "utf8"));
  if (manifest.schema_version !== "aero-bench.incoming-assets/v1" || !Array.isArray(manifest.entries)) {
    throw new Error("Invalid incoming asset manifest");
  }
  return manifest.entries.map(item => {
    if (typeof item.id !== "string" || !["vehicle", "character", "city"].includes(item.category)
      || typeof item.subgroup !== "string" || typeof item.title !== "string"
      || typeof item.source_path !== "string" || typeof item.note !== "string") {
      throw new Error(`Invalid incoming asset entry: ${item.id}`);
    }
    if (item.model_url && sizeIfFile(join(frontendRoot, item.source_path)) === null) {
      throw new Error(`Declared incoming model is missing: ${item.source_path}`);
    }
    return item;
  });
}

function furnitureEntries(frontendRoot) {
  const path = join(frontendRoot, "public/models/incoming/furniture/manifest.json");
  if (!existsSync(path)) return [];
  const manifest = JSON.parse(readFileSync(path, "utf8"));
  if (manifest.schema_version !== "aero-bench.c2239/v1" || !Array.isArray(manifest.entries)) {
    throw new Error("Invalid C2239 furniture manifest");
  }
  for (const item of manifest.entries) {
    if (item.category !== "city" || item.kind !== "model" || item.status !== "ready"
      || typeof item.source_path !== "string" || typeof item.model_url !== "string"
      || sizeIfFile(join(frontendRoot, item.source_path)) === null) {
      throw new Error(`Invalid C2239 furniture entry: ${item.id}`);
    }
  }
  return manifest.entries;
}

function regularDirectories(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name).sort();
}

function finishPresets(frontendRoot) {
  const textureRoot = join(frontendRoot, "public/osm2world/style/textures");
  const results = [];
  for (const family of ["cc0textures", "custom"]) {
    for (const folder of regularDirectories(join(textureRoot, family))) {
      const files = regularFiles(join(textureRoot, family, folder));
      const base = folder.toLowerCase();
      const findMap = suffix => files.find(file =>
        PREVIEW_EXTENSIONS.has(extname(file).toLowerCase())
        && basename(file, extname(file)).toLowerCase() === `${base}_${suffix}`);
      const color = findMap("color") ?? findMap("color_transparent");
      if (!color) continue;
      const url = file => file ? `/osm2world/style/textures/${family}/${encodeURIComponent(folder)}/${encodeURIComponent(file)}` : null;
      results.push({
        id: `${family}:${folder}`, label: folder, family,
        color_url: url(color), normal_url: url(findMap("normal")), orm_url: url(findMap("orm")),
        source_path: `public/osm2world/style/textures/${family}/${folder}`,
      });
    }
  }
  return results;
}

export function buildAssetLibraryCatalog(frontendRoot) {
  const modelEntries = [...curatedUavEntries(frontendRoot), ...meshUavEntries(frontendRoot)];
  const cad = cadConversion(frontendRoot);
  const convertedByArchive = new Map([...modelEntries, ...cad.models]
    .filter(entry => entry.origin_path)
    .map(entry => [basename(entry.origin_path), entry.id]));
  const entries = [...modelEntries, ...cad.models, ...incomingEntries(frontendRoot), ...furnitureEntries(frontendRoot), ...cityEntries(frontendRoot), ...archiveEntries(frontendRoot, convertedByArchive, cad.byArchive)];
  for (const entry of modelEntries) {
    if (sizeIfFile(join(frontendRoot, entry.source_path)) === null) {
      throw new Error(`Declared browser model is missing: ${entry.source_path}`);
    }
  }
  return {
    schema_version: "aero-bench.asset-library/v1",
    entries,
    finishes: finishPresets(frontendRoot),
    summary: {
      browser_models: entries.filter(entry => entry.kind === "model" && entry.status === "ready").length,
      uav_archives_downloaded: entries.filter(entry => entry.kind === "archive" && entry.status === "downloaded").length,
      uav_archives_pending: entries.filter(entry => entry.kind === "archive" && entry.status === "pending").length,
      uav_archives_partial: entries.filter(entry => entry.kind === "archive" && entry.status === "partial").length,
      uav_preview_images: regularFiles(join(quarkRoot(frontendRoot), "0 预览图 格式备注 名称对应"))
        .filter(name => PREVIEW_EXTENSIONS.has(extname(name).toLowerCase())).length,
      texture_images: entries.filter(entry => entry.kind === "texture" && entry.image_url).length,
      city_packs: entries.filter(entry => entry.kind === "scene").length,
    },
  };
}

export function assetLibraryMiddleware(frontendRoot) {
  return function assetLibrary(req, res, next) {
    const path = (req.url ?? "").split("?")[0];
    if (req.method !== "GET" && req.method !== "HEAD") return next();
    if (path === ASSET_CATALOG_PATH) {
      try {
        const body = JSON.stringify(buildAssetLibraryCatalog(frontendRoot));
        res.statusCode = 200;
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.end(req.method === "HEAD" ? undefined : body);
      } catch (error) {
        res.statusCode = 500;
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.end(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
      }
      return;
    }
    if (!path?.startsWith(PREVIEW_PREFIX)) return next();
    let name;
    try { name = decodeURIComponent(path.slice(PREVIEW_PREFIX.length)); } catch { name = ""; }
    const previewDir = join(quarkRoot(frontendRoot), "0 预览图 格式备注 名称对应");
    if (!name || name !== basename(name) || name.includes("\\") || !PREVIEW_EXTENSIONS.has(extname(name).toLowerCase()) || !regularFiles(previewDir).includes(name)) {
      res.statusCode = 404;
      res.end();
      return;
    }
    const absolute = join(previewDir, name);
    res.statusCode = 200;
    res.setHeader("Content-Type", extname(name).toLowerCase() === ".png" ? "image/png" : extname(name).toLowerCase() === ".webp" ? "image/webp" : extname(name).toLowerCase() === ".bmp" ? "image/bmp" : "image/jpeg");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Length", statSync(absolute).size);
    if (req.method === "HEAD") return res.end();
    createReadStream(absolute).pipe(res);
  };
}

export function assetLibraryPlugin(frontendRoot) {
  const middleware = assetLibraryMiddleware(frontendRoot);
  return {
    name: "aero-asset-library",
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}
