import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import { assetLibraryMiddleware, buildAssetLibraryCatalog, classifyUav } from "./asset-library.mjs";

function fixture() {
  const workspace = mkdtempSync(join(tmpdir(), "aero-asset-library-"));
  const root = join(workspace, "frontend");
  const write = (relative, value) => {
    const path = join(relative.startsWith("validation/") ? workspace : root, relative);
    mkdirSync(join(path, ".."), { recursive: true });
    writeFileSync(path, value);
  };
  write("public/models/uav/holybro-x500-textured-preview.glb", "glb");
  write("public/models/uav/scaneagle-preview.glb", "glb");
  write("public/models/uav/vtol-fixedwing-preview.glb", "glb");
  write("scripts/uav-preview-models.json", JSON.stringify([
    { id: "holybro-x500", title: "X500", subgroup: "多旋翼", archive: "x500.zip", output: "holybro-x500-textured-preview.glb", exposure: 1 },
    { id: "scaneagle-preview", title: "ScanEagle", subgroup: "固定翼", archive: "quadrotor.zip", source_3dxml: "aircraft.3DXML", output: "scaneagle-preview.glb", exposure: 1 },
    { id: "vtol-fixedwing-preview", title: "VTOL", subgroup: "垂直起降", archive: "vtol-plane.zip", output: "vtol-fixedwing-preview.glb", exposure: 1 },
  ]));
  write("scripts/uav-mesh-previews.json", "[]");
  write("public/osm2world/packs/test-pack/manifest.json", JSON.stringify({ original_mesh_count: 2, batches: [{}], objects: [{}, {}], textures: { a: {}, b: {} } }));
  write("public/osm2world/style/textures/cc0textures/roof/sample.jpg", "image");
  write("public/osm2world/style/textures/cc0textures/Metal002/Metal002_Color.jpg", "color");
  write("public/osm2world/style/textures/cc0textures/Metal002/Metal002_Normal.jpg", "normal");
  write("public/osm2world/style/textures/cc0textures/Metal002/Metal002_ORM.jpg", "orm");
  write("assets/incoming/quark/uav/manifest.json", JSON.stringify({
    files: [{ path: "archives/quadrotor.zip", size: 3 }, { path: "archives/x500.zip", size: 3 }],
    blocked: [{ path: "archives/vtol-plane.zip", size: 9 }],
  }));
  write("validation/downloaded-assets/quark-drone-models/003 无人机/quadrotor.zip", "zip");
  write("validation/downloaded-assets/quark-drone-models/003 无人机/x500.zip", "zip");
  write("validation/downloaded-assets/quark-drone-models/003 无人机/0 预览图 格式备注 名称对应/quadrotor.jpg", "image");
  write("public/models/bigcity/manifest.json", JSON.stringify({ entries: [{ guid: "building", title: "Building", package_path: "Assets/Package/FBX/Building.FBX", format: "FBX", subgroup: "BigCity · 建筑", bytes: 8, model_url: "/models/bigcity/fbx/building.fbx" }] }));
  return { root, write, workspace };
}

function request(middleware, path) {
  return new Promise((resolve, reject) => {
    const response = new PassThrough();
    const headers = {};
    let body = "";
    response.setHeader = (key, value) => { headers[key] = value; };
    response.on("data", chunk => { body += chunk.toString(); });
    response.on("error", reject);
    response.on("finish", () => resolve({ status: response.statusCode, headers, body }));
    middleware({ method: "GET", url: path }, response, () => reject(new Error("unexpected fallthrough")));
  });
}

test("catalog reports actual downloads and separates browser models from source packages", () => {
  const { root, write, workspace } = fixture();
  try {
    const first = buildAssetLibraryCatalog(root);
    assert.equal(first.summary.browser_models, 4);
    assert.equal(first.entries.filter(entry => entry.category === "vehicle").length, 0);
    assert.equal(first.summary.uav_archives_downloaded, 2);
    assert.equal(first.summary.uav_archives_pending, 1);
    assert.equal(first.summary.uav_preview_images, 1);
    assert.equal(first.summary.texture_images, 4);
    assert.equal(first.entries.find(entry => entry.id === "osm-texture:cc0textures/roof/sample.jpg").image_url,
      "/osm2world/style/textures/cc0textures/roof/sample.jpg");
    assert.deepEqual(first.finishes.find(finish => finish.id === "cc0textures:Metal002"), {
      id: "cc0textures:Metal002", label: "Metal002", family: "cc0textures",
      color_url: "/osm2world/style/textures/cc0textures/Metal002/Metal002_Color.jpg",
      normal_url: "/osm2world/style/textures/cc0textures/Metal002/Metal002_Normal.jpg",
      orm_url: "/osm2world/style/textures/cc0textures/Metal002/Metal002_ORM.jpg",
      source_path: "public/osm2world/style/textures/cc0textures/Metal002",
    });
    assert.equal(first.entries.find(entry => entry.id === "quark-image:quadrotor.jpg").converted_model_id, "model:scaneagle-preview");
    assert.equal(first.entries.find(entry => entry.id === "model:scaneagle-preview").reference_preview_url, "/asset-library/preview/quadrotor.jpg");
    assert.equal(first.entries.find(entry => entry.id === "model:scaneagle-preview").material_origin, "统一航空 PBR 涂装；保留 3DXML 图片与原色记录");
    assert.equal(first.entries.find(entry => entry.id === "model:holybro-x500").material_origin, "统一航空 PBR 展示涂装（程序生成）");
    assert.equal(first.entries.find(entry => entry.id === "model:holybro-x500").reference_preview_url, null);
    assert.equal(first.entries.find(entry => entry.id === "city:test-pack").details.textures, 2);
    assert.equal(first.entries.find(entry => entry.id === "texture:cc0textures").details.files, 4);
    assert.equal(first.entries.find(entry => entry.id === "bigcity:building").model_url, "/models/bigcity/fbx/building.fbx");
    assert.match(first.entries.find(entry => entry.id === "bigcity:building").material_origin, /源 FBX 材质/);
    assert.equal(first.entries.find(entry => entry.id === "quark:vtol-plane.zip").kind, "archive");
    assert.equal(first.entries.find(entry => entry.id === "quark:quadrotor.zip").converted_model_id, "model:scaneagle-preview");
    write("validation/downloaded-assets/quark-drone-models/003 无人机/vtol-plane.zip", "partial");
    const second = buildAssetLibraryCatalog(root);
    assert.equal(second.summary.uav_archives_pending, 0);
    assert.equal(second.summary.uav_archives_partial, 1);
    write("validation/downloaded-assets/quark-drone-models/003 无人机/vtol-plane.zip", "123456789");
    assert.equal(buildAssetLibraryCatalog(root).summary.uav_archives_downloaded, 3);
    write("public/models/uav/mesh/extra.glb", "glb");
    write("scripts/uav-mesh-previews.json", JSON.stringify([{
      id: "mesh:extra", archive: "extra.zip", title: "Extra", subgroup: "多旋翼", output: "extra.glb",
      triangles: 12, note: "源 STL 预览", source_member: "extra.stl",
    }]));
    write("validation/downloaded-assets/quark-drone-models/003 无人机/extra.zip", "zip");
    const withMesh = buildAssetLibraryCatalog(root);
    assert.equal(withMesh.entries.find(entry => entry.id === "mesh:extra").model_url, "/models/uav/mesh/extra.glb");
    assert.equal(withMesh.entries.find(entry => entry.id === "quark:extra.zip").converted_model_id, "mesh:extra");
    const componentArchive = "reconnaissance-attack-aircraft-1 6 STL X_T.zip";
    write("public/models/uav/mesh/hellfire.glb", "glb");
    write("validation/downloaded-assets/quark-drone-models/003 无人机/" + componentArchive, "zip");
    write("scripts/uav-mesh-previews.json", JSON.stringify([
      { id: "mesh:extra", archive: "extra.zip", title: "Extra", subgroup: "多旋翼", output: "extra.glb",
        triangles: 12, note: "源 STL 预览", source_member: "extra.stl" },
      { id: "mesh:reconnaissance-hellfire-component", archive: componentArchive, title: "Hellfire 弹体", subgroup: "机载设备与部件",
        output: "hellfire.glb", triangles: 33, note: "仅为弹体组件，不是飞机机体", component_only: true },
    ]));
    const withComponent = buildAssetLibraryCatalog(root);
    assert.match(withComponent.entries.find(entry => entry.id === "mesh:reconnaissance-hellfire-component").material_origin, /中性灰色/);
    assert.match(withComponent.entries.find(entry => entry.id === `quark:${componentArchive}`).note, /飞机整机仍需/);
    write("public/models/uav/cad/recon-full.glb", "glb");
    write("public/models/uav/cad-conversion-report.json", JSON.stringify({
      schema_version: "aero-bench.cad-conversion/v1",
      entries: [{ archive: componentArchive, status: "converted", model_path: "public/models/uav/cad/recon-full.glb",
        selected_member: "airframe.step" }],
    }));
    const withFullCad = buildAssetLibraryCatalog(root);
    const archive = withFullCad.entries.find(entry => entry.id === `quark:${componentArchive}`);
    assert.equal(archive.converted_model_id, `cad:${componentArchive}`);
    assert.match(archive.note, /airframe.step/);
    assert.doesNotMatch(archive.note, /仅包内 Hellfire/);
  } finally { rmSync(workspace, { recursive: true, force: true }); }
});

test("preview endpoint serves only allowlisted images, never CAD archives", async () => {
  const { root, workspace } = fixture();
  try {
    const middleware = assetLibraryMiddleware(root);
    const image = await request(middleware, "/asset-library/preview/quadrotor.jpg");
    assert.equal(image.status, 200);
    assert.equal(image.body, "image");
    assert.equal((await request(middleware, "/asset-library/preview/%2E%2E%2Farchives%2Fquadrotor.zip")).status, 404);
    assert.equal((await request(middleware, "/asset-library/preview/quadrotor.zip")).status, 404);
    const catalog = await request(middleware, "/asset-library/catalog.json");
    assert.equal(catalog.status, 200);
    assert.equal(JSON.parse(catalog.body).summary.uav_archives_downloaded, 2);
  } finally { rmSync(workspace, { recursive: true, force: true }); }
});

test("filename categories are hints and prioritize VTOL before rotor or fixed wing", () => {
  assert.equal(classifyUav("uav-vtol-fixedwing-with-tilt-system.zip"), "垂直起降");
  assert.equal(classifyUav("quadcopter-1.zip"), "多旋翼");
  assert.equal(classifyUav("scaneagle-uav.zip"), "固定翼");
  assert.equal(classifyUav("unidentified-uav.zip"), "待识别");
});
