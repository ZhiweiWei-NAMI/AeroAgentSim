// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { applyVegetationWind, createVegetationWindDepthMaterial, createVegetationWindUniforms,
  setVegetationWind, VEGETATION_WIND_PROFILES, VEGETATION_WIND_SATURATION_MPS,
  windStrengthFor } from "./city-vegetation-wind";

const EPS = 1e-12;
function angleDelta(a: number, b: number): number {
  return Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));
}

describe("vegetation wind uniforms", () => {
  it("keeps zero wind at zero strength regardless of heading", () => {
    const u = createVegetationWindUniforms();
    setVegetationWind(u, 0, 137, 3.5);
    expect(u.uWindStrength.value).toBe(0);
    expect(u.uTime.value).toBe(3.5);
    expect(u.uWindDir.value.length()).toBeCloseTo(1, 10);
  });
  it("maps the clockwise-from-north heading to (sin d, -cos d) in city XZ", () => {
    const u = createVegetationWindUniforms();
    setVegetationWind(u, 5, 0, 0);
    expect(u.uWindDir.value.x).toBeCloseTo(0, 12); expect(u.uWindDir.value.y).toBeCloseTo(-1, 12);
    setVegetationWind(u, 5, 90, 0);
    expect(u.uWindDir.value.x).toBeCloseTo(1, 12); expect(u.uWindDir.value.y).toBeCloseTo(0, 12);
    setVegetationWind(u, 5, 270, 0);
    expect(u.uWindDir.value.x).toBeCloseTo(-1, 12); expect(u.uWindDir.value.y).toBeCloseTo(0, 12);
    setVegetationWind(u, 5, 45, 0);
    expect(u.uWindDir.value.x).toBeCloseTo(Math.SQRT1_2, 12);
    expect(u.uWindDir.value.y).toBeCloseTo(-Math.SQRT1_2, 12);
  });
  it("stays monotone, saturates without overshoot, and documents the rate", () => {
    expect(windStrengthFor(0)).toBe(0);
    expect(VEGETATION_WIND_SATURATION_MPS).toBeGreaterThan(0);
    let previous = -1;
    for (let mps = 0; mps <= 60; mps += 0.5) {
      const strength = windStrengthFor(mps);
      expect(strength).toBeGreaterThan(previous);
      expect(strength).toBeGreaterThanOrEqual(0);
      expect(strength).toBeLessThan(1);
      previous = strength;
    }
    expect(windStrengthFor(VEGETATION_WIND_SATURATION_MPS)).toBeCloseTo(1 - Math.E ** -1, 12);
    expect(windStrengthFor(100)).toBeGreaterThan(0.999999);
  });
  it("publishes the saturating strength through setVegetationWind", () => {
    const u = createVegetationWindUniforms();
    setVegetationWind(u, 12, 0, 0);
    expect(u.uWindStrength.value).toBe(windStrengthFor(12));
  });
  it.each([
    ["NaN speed", [NaN, 10, 0]], ["negative speed", [-0.1, 10, 0]],
    ["NaN direction", [5, NaN, 0]], ["infinite time", [5, 10, Infinity]],
  ])("rejects invalid wind input %s", (_label, args) => {
    expect(() => setVegetationWind(createVegetationWindUniforms(),
      ...(args as [number, number, number]))).toThrow(RangeError);
  });
  it("accepts negative headings and large values because direction is periodic", () => {
    const u = createVegetationWindUniforms();
    setVegetationWind(u, 4, -90, 0);
    const base = createVegetationWindUniforms();
    setVegetationWind(base, 4, 270, 0);
    expect(angleDelta(Math.atan2(u.uWindDir.value.y, u.uWindDir.value.x),
      Math.atan2(base.uWindDir.value.y, base.uWindDir.value.x))).toBeLessThan(EPS);
  });
});

describe("vegetation wind shader patch", () => {
  it("inserts the uniforms and wind function while keeping begin_vertex", () => {
    const material = new THREE.MeshStandardMaterial({ name: "wind-target" });
    const u = createVegetationWindUniforms();
    applyVegetationWind(material, u, "tree");
    expect(typeof material.onBeforeCompile).toBe("function");
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: "void main() {\n\t#include <common>\n\t#include <begin_vertex>\n}",
      fragmentShader: "void main() {}" };
    (material.onBeforeCompile as NonNullable<THREE.Material["onBeforeCompile"]>)(shader as never,
      {} as never);
    expect(shader.uniforms.uTime).toBe(u.uTime);
    expect(shader.uniforms.uWindDir).toBe(u.uWindDir);
    expect(shader.uniforms.uWindStrength).toBe(u.uWindStrength);
    expect(shader.vertexShader).toContain("#include <begin_vertex>");
    expect(shader.vertexShader).toContain("uniform float uTime;");
    expect(shader.vertexShader).toContain("uniform vec2 uWindDir;");
    expect(shader.uniforms.uWindDir!.value).toBe(u.uWindDir.value);
    expect(shader.vertexShader.indexOf("vegetationWindOffset(transformed"))
      .toBeGreaterThan(shader.vertexShader.indexOf("#include <begin_vertex>"));
  });
  it("separates the program cache key per profile", () => {
    const tree = new THREE.MeshStandardMaterial();
    const grass = new THREE.MeshStandardMaterial();
    const u = createVegetationWindUniforms();
    applyVegetationWind(tree, u, "tree");
    applyVegetationWind(grass, u, "grass");
    expect(tree.customProgramCacheKey()).toBe("city-vegetation-wind-v2:tree");
    expect(grass.customProgramCacheKey()).toBe("city-vegetation-wind-v2:grass");
    expect(tree.customProgramCacheKey()).not.toBe(grass.customProgramCacheKey());
  });
  it("is idempotent for the same profile and throws on a profile conflict", () => {
    const material = new THREE.MeshStandardMaterial();
    const u = createVegetationWindUniforms();
    applyVegetationWind(material, u, "tree");
    const hook = material.onBeforeCompile;
    applyVegetationWind(material, u, "tree");
    expect(material.onBeforeCompile).toBe(hook);
    expect(() => applyVegetationWind(material, u, "grass")).toThrow(/conflict/);
  });
  it("refuses to patch a material that already carries a different compile hook", () => {
    const material = new THREE.MeshStandardMaterial();
    material.onBeforeCompile = () => undefined;
    expect(() => applyVegetationWind(material, createVegetationWindUniforms(), "tree"))
      .toThrow(/unpatched/);
  });
  it("rejects unknown profiles", () => {
    expect(() => applyVegetationWind(new THREE.MeshStandardMaterial(),
      createVegetationWindUniforms(), "bark" as never)).toThrow(/profile/);
  });
  it("anchors the base, bounds the tip and maps world bend back through the placement", () => {
    const material = new THREE.MeshStandardMaterial();
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: "#include <common>\nvoid main() { #include <begin_vertex> }",
      fragmentShader: "" };
    applyVegetationWind(material, createVegetationWindUniforms(), "grass");
    (material.onBeforeCompile as NonNullable<THREE.Material["onBeforeCompile"]>)(shader as never, {} as never);
    expect(shader.vertexShader).toContain("modelMatrix * instanceMatrix");
    expect(shader.vertexShader).toContain("transpose(linear) * worldOffset / scaleSq");
    expect(shader.vertexShader).toContain("max(objectPosition.y, 0.0)");
    expect(shader.vertexShader).toContain(`${VEGETATION_WIND_PROFILES.grass.tipDisplacementM.toFixed(4)}`);
    // No per-frame hash of time: gusts must be continuous.
    expect(shader.vertexShader).not.toMatch(/fract\(uTime|floor\([^)]*uTime/);
  });
  it("keeps the authored tip displacement bounded relative to plant height", () => {
    for (const profile of Object.values(VEGETATION_WIND_PROFILES)) {
      // Worst case: strength 1, relative height 1.25, gust 1 plus full flutter.
      const worst = profile.tipDisplacementM * 1.25 ** 2 * (1 + profile.flutter);
      expect(worst).toBeLessThan(profile.referenceHeightM * 0.6);
    }
  });
  it("patches a shadow depth material with the same wind and alpha cut-out", () => {
    const u = createVegetationWindUniforms();
    const map = new THREE.Texture();
    const depth = createVegetationWindDepthMaterial(u, "tree", { map, alphaTest: 0.5 });
    expect(depth.map).toBe(map); expect(depth.alphaTest).toBe(0.5);
    expect(depth.customProgramCacheKey()).toBe("city-vegetation-wind-v2:tree");
  });
});
