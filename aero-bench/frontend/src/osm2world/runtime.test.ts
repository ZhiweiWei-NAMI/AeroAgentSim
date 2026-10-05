// @vitest-environment node
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { OSM2WORLD_RUNTIME_MODULE, OSM2WORLD_STYLE_BASE, OSM2WORLD_STYLE_PROPERTIES, resolveTextureUrl } from "./runtime";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "../..");

describe("OSM2World runtime contract", () => {
  it("maps OSM2World texture paths onto the offline style directory", () => {
    expect(resolveTextureUrl("./textures/cc0textures/Plaster002/Plaster002_Color.jpg")).toBe(
      `${OSM2WORLD_STYLE_BASE}/textures/cc0textures/Plaster002/Plaster002_Color.jpg`,
    );
    expect(resolveTextureUrl("/styles/default/textures/Grass-2048.JPG")).toBe(`${OSM2WORLD_STYLE_BASE}/textures/Grass-2048.JPG`);
    expect(resolveTextureUrl("/osm2world/style/textures/arbaro_tree_broad_leaved.png")).toBe(
      "/osm2world/style/textures/arbaro_tree_broad_leaved.png",
    );
    expect(resolveTextureUrl(null)).toBeNull();
  });

  it("rejects remote and escaping material URLs", () => {
    for (const path of ["https://example.com/texture.png", "//example.com/texture.png", "../texture.png", "/private/texture.png"]) {
      expect(() => resolveTextureUrl(path)).toThrow();
    }
  });

  it("ships the official converter module and style entry", () => {
    expect(existsSync(join(frontendRoot, "public", OSM2WORLD_RUNTIME_MODULE))).toBe(true);
    expect(existsSync(join(frontendRoot, "public", OSM2WORLD_STYLE_PROPERTIES))).toBe(true);
    const properties = readFileSync(join(frontendRoot, "public", OSM2WORLD_STYLE_PROPERTIES), "utf8");
    expect(properties).toMatch(/useBillboards\s*=\s*true/);
    expect(properties).toMatch(/createTerrain\s*=\s*true/);
    expect(properties).toMatch(/material_BUILDING_DEFAULT_texture0_color_file=/);
    expect(properties).toMatch(/material_ROOF_DEFAULT_texture0_/);
  });

  it("keeps the OSM2World materials that produce the official city look", () => {
    const textures = join(frontendRoot, "public/osm2world/style/textures");
    for (const relative of [
      "cc0textures/Plaster002/Plaster002_Color.jpg",
      "cc0textures/RoofingTiles010/RoofingTiles010_Color.jpg",
      "cc0textures/Asphalt010/Asphalt010_Color.png",
      "cc0textures/Ground003/Ground003_Color.jpg",
      "custom/Windows/Windows_Color.png",
      "arbaro_tree_broad_leaved.png",
      "Grass-2048.JPG",
    ]) {
      const path = join(textures, relative);
      expect(existsSync(path), path).toBe(true);
      expect(statSync(path).size, path).toBeGreaterThan(2000);
    }
  });
});
