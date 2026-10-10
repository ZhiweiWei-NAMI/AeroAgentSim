// @vitest-environment jsdom
import * as THREE from "three";
import { afterEach, describe, expect, it } from "vitest";
import { createOutOfExtentGround, mappedGroundBounds, outsideMappedExtentDistance,
  OUT_OF_EXTENT_FADE_METRES, PublicTraceMap } from "./map";
import { setLanguage } from "./i18n";
import type { LoadedMeshPack } from "./osm2world/pack-loader";

const extent = { west: -20, east: 60, south: -30, north: 90 };
const pack = { manifest: { extent, source: { sha256: "b".repeat(64) } },
  manifestSha256: "a".repeat(64) } as unknown as LoadedMeshPack;
const methods = PublicTraceMap.prototype as unknown as {
  configureMappedGround(pack: LoadedMeshPack): void;
  clearMappedGround(): void;
  refreshMappedExtentLegend(): void;
};
function fixture() {
  const host = document.createElement("section"); host.className = "map";
  host.innerHTML = '<div id="city-map"></div><div class="map-legend"><div class="legend-title">图例</div></div>';
  document.body.append(host);
  const horizon = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial());
  horizon.rotation.x = -Math.PI / 2;
  const map = { root: host.querySelector<HTMLElement>("#city-map")!, horizon,
    outOfExtentGround: createOutOfExtentGround(), mappedGroundIdentity: null,
    refreshMappedExtentLegend: methods.refreshMappedExtentLegend };
  return { map, host };
}
afterEach(() => { document.body.replaceChildren(); setLanguage("zh"); });

describe("verified extent horizon", () => {
  it("maps north/south to negative renderer Z", () => {
    expect(mappedGroundBounds(extent).toArray()).toEqual([-20, 60, -90, 30]);
  });

  it.each([[0, 0], [-20, -90], [60, 30], [-20, 0], [60, 0], [0, -90], [0, 30]])(
    "keeps in-extent points and boundaries at zero distance (%s,%s)", (x, z) => {
      expect(outsideMappedExtentDistance(x!, z!, mappedGroundBounds(extent))).toBe(0);
    });

  it.each([[-23, 0, 3], [65, 0, 5], [0, -97, 7], [0, 41, 11], [63, -94, 5]])(
    "measures distance to the nearest boundary, including corners (%s,%s)", (x, z, distance) => {
      expect(outsideMappedExtentDistance(x!, z!, mappedGroundBounds(extent))).toBe(distance);
    });

  it("bounds neutral ground exactly and keeps source identities separate from surface classes", () => {
    const { map, host } = fixture(); methods.configureMappedGround.call(map, pack);
    map.horizon.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(map.horizon);
    expect(bounds.min.x).toBeCloseTo(extent.west); expect(bounds.max.x).toBeCloseTo(extent.east);
    expect(bounds.min.z).toBeCloseTo(-extent.north); expect(bounds.max.z).toBeCloseTo(-extent.south);
    expect(map.horizon.position.y).toBe(-0.025);
    expect(map.horizon.userData.surfaceClassification).toBe("source-unclassified-in-mapped-extent");
    expect(map.outOfExtentGround.userData.surfaceClassification).toBe("out-of-mapped-extent");
    expect(map.outOfExtentGround.userData.groundCoverId).toBeUndefined();
    const note = host.querySelector<HTMLElement>('[data-role="mapped-extent-note"]')!;
    expect(note.textContent).toContain("地图范围外"); expect(note.textContent).toContain("源数据未分类");
    expect(note.dataset.manifestSha256).toBe(pack.manifestSha256);
    expect(note.dataset.sourceSha256).toBe(pack.manifest.source.sha256);
    expect(note.title).toContain(pack.manifestSha256);
    expect(map.outOfExtentGround.material.userData.mappedExtentUniforms.uMappedBounds.value.toArray())
      .toEqual([-20, 60, -90, 30]);
  });

  it("updates the existing note for language and region changes without duplicate legends", () => {
    const { map, host } = fixture(); methods.configureMappedGround.call(map, pack);
    setLanguage("en"); methods.refreshMappedExtentLegend.call(map);
    expect(host.querySelector('[data-role="mapped-extent-note"]')?.textContent).toContain("Outside mapped extent");
    methods.configureMappedGround.call(map, { ...pack,
      manifest: { ...pack.manifest, extent: { west: 100, east: 200, south: 300, north: 400 } } });
    expect(host.querySelectorAll('[data-role="mapped-extent-note"]')).toHaveLength(1);
    expect(map.horizon.position.toArray()).toEqual([150, -0.025, -350]);
  });

  it("clears both support surfaces and provenance when a scene is unmounted", () => {
    const { map, host } = fixture(); methods.configureMappedGround.call(map, pack);
    methods.clearMappedGround.call(map);
    expect(map.horizon.visible).toBe(false); expect(map.outOfExtentGround.visible).toBe(false);
    expect(map.mappedGroundIdentity).toBeNull(); expect(map.root.dataset.outOfExtentGround).toBeUndefined();
    expect(host.querySelector('[data-role="mapped-extent-note"]')).toBeNull();
  });

  it("keeps the provenance note inside an existing collapsed viewer disclosure", () => {
    const { map, host } = fixture();
    const legend = host.querySelector(".map-legend")!;
    const content = document.createElement("div"); content.className = "viewer-chrome-content";
    content.inert = true; legend.append(content);
    methods.configureMappedGround.call(map, pack);
    expect(content.querySelector('[data-role="mapped-extent-note"]')).not.toBeNull();
    expect(legend.querySelectorAll(':scope > [data-role="mapped-extent-note"]')).toHaveLength(0);
  });

  it("preserves boundary clipping and haze uniforms across active fog changes", () => {
    const { map } = fixture(); methods.configureMappedGround.call(map, pack);
    const ground = map.outOfExtentGround;
    const shader = { uniforms: {}, vertexShader: THREE.ShaderLib.basic.vertexShader,
      fragmentShader: THREE.ShaderLib.basic.fragmentShader };
    ground.material.onBeforeCompile(shader as unknown as Parameters<typeof ground.material.onBeforeCompile>[0], {} as THREE.WebGLRenderer);
    expect(shader.fragmentShader).toContain("if (boundaryDistance == 0.0) discard;");
    expect(shader.fragmentShader).toContain("smoothstep(0.0, uBoundaryFadeMetres, boundaryDistance)");
    const scene = new THREE.Scene();
    for (const color of [0xa9bccd, 0x5c5462, 0x0a111d, 0x829ba8]) {
      scene.fog = new THREE.Fog(color, 20, 100);
      ground.onBeforeRender({} as THREE.WebGLRenderer, scene, new THREE.Camera(), ground.geometry, ground.material, new THREE.Group());
      const u = ground.material.userData.mappedExtentUniforms;
      expect(ground.userData.activeFogColor).toBe(scene.fog.color.getHexString());
      expect(u.uMappedBounds.value.toArray()).toEqual([-20, 60, -90, 30]);
      expect(u.uBoundaryFadeMetres.value).toBe(OUT_OF_EXTENT_FADE_METRES);
    }
    scene.fog = null;
    expect(() => ground.onBeforeRender({} as THREE.WebGLRenderer, scene,
      new THREE.Camera(), ground.geometry, ground.material, new THREE.Group())).toThrow(/active horizon fog/);
    expect(ground.material.map).toBeNull();
  });
});
