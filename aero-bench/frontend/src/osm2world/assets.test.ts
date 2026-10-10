// @vitest-environment node
import { describe, expect, it } from "vitest";
import { sceneAsset, loadSceneAsset } from "./assets";
import { AssetResolver } from "../asset-resolver";
import { publicTrace } from "../testing/trace-v3-fixture";
import type { PublicScenario } from "../generated/aero-bench-contracts";

function scenario(): PublicScenario {
  const base = (publicTrace() as { scenario: PublicScenario }).scenario;
  return { ...base, layers: [...base.layers, { layer_id: "layer.osm", kind: "osm_scene", asset_id: base.assets[0]!.asset_id, visibility: "public", default_visible: true }], assets: base.assets.map((asset, i) => i === 0 ? { ...asset, media_type: "application/json" } : asset) };
}

describe("declared OSM scene assets", () => {
  it("selects only the explicitly declared OSM layer", () => {
    const input = scenario();
    expect(sceneAsset(input)?.asset_id).toBe(input.assets[0]!.asset_id);
    expect(sceneAsset({ ...input, layers: [] })).toBeNull();
    expect(() => sceneAsset({ ...input, layers: [...input.layers, input.layers.at(-1)!] })).toThrow(/one/);
  });

  it("loads the same hash-verified OSM bytes through replay and authenticated transport", async () => {
    const payload = JSON.stringify({ version: 0.6, elements: [{ type: "node", id: 1, lat: 31, lon: 121 }] });
    const bytes = new TextEncoder().encode(payload);
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), byte => byte.toString(16).padStart(2, "0")).join("");
    const input = scenario();
    const asset = { ...input.assets[0]!, sha256: digest, replay_path: `assets/${digest}`, size_bytes: bytes.length };
    const declared = { ...input, assets: [asset, ...input.assets.slice(1)] };
    for (const transport of ["replay", "authenticated"]) {
      const resolver = new AssetResolver({ baseHref: "http://localhost/", fetch: async url => {
        expect(String(url)).toBe(`http://localhost/assets/${digest}`);
        expect(["replay", "authenticated"]).toContain(transport);
        return new Response(bytes);
      } });
      expect((await loadSceneAsset(declared, resolver)).elements).toEqual([{ type: "node", id: 1, lat: 31, lon: 121, tags: undefined }]);
      resolver.dispose();
    }
  });

  it("does not parse or publish asset bytes on digest mismatch", async () => {
    const input = scenario();
    const asset = input.assets[0]!;
    const resolver = new AssetResolver({ baseHref: "http://localhost/", fetch: async () => new Response(new Uint8Array(asset.size_bytes)), digest: async () => "e".repeat(64), createObjectUrl: () => { throw new Error("unverified bytes published"); } });
    await expect(loadSceneAsset(input, resolver)).rejects.toThrow(/digest/);
    resolver.dispose();
  });
});
