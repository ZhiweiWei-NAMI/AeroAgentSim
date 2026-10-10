// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { CITY_GROUND_MATERIAL_RULES_V1 } from "./city-ground-material-rules";
import { CITY_WEATHER_PRESETS, CITY_WEATHER_CLEAR } from "./city-weather";
import { surfaceWetnessFromWeather } from "./city-surface-wetness";
import { applyWaterSurface, createWaterSurfaceUniforms, setWaterSurfaceTime,
  setWaterSurfaceWeather, WATER_SURFACE_V1, waterSlopeAmplitudes,
  type WaterSurfaceUniforms } from "./city-water-surface";

const WATER_PRESET_IDS = CITY_GROUND_MATERIAL_RULES_V1.presets
  .map(preset => preset.id)
  .filter(id => id.startsWith("water-"));

describe("waterSlopeAmplitudes", () => {
  it("sums in quadrature to the Cox-Munk RMS slope", () => {
    const rms = (amplitudes: readonly number[]) =>
      Math.sqrt(amplitudes.reduce((sum, m) => sum + m * m, 0) / 2);
    const calm = rms(waterSlopeAmplitudes("water-river", 0));
    expect(calm).toBeCloseTo(Math.sqrt(0.003), 12);
    const windy = rms(waterSlopeAmplitudes("water-river", 7));
    expect(windy).toBeCloseTo(Math.sqrt(0.003 + 0.00512 * 7), 12);
  });
  it("scales by the type response", () => {
    const river = waterSlopeAmplitudes("water-river", 7);
    const pond = waterSlopeAmplitudes("water-pond", 7);
    expect(WATER_SURFACE_V1.typeResponse["water-pond"]! / WATER_SURFACE_V1.typeResponse["water-river"]!)
      .toBe(0.45);
    for (let index = 0; index < river.length; index++) {
      expect(pond[index]!).toBeCloseTo(river[index]! * 0.45, 12);
    }
  });
  it("rejects unknown presets and negative winds", () => {
    expect(() => waterSlopeAmplitudes("water-lagoon", 4)).toThrow(/no type response/);
    expect(() => waterSlopeAmplitudes("lawn-maintained", 4)).toThrow(/no type response/);
    expect(() => waterSlopeAmplitudes("water-river", -1)).toThrow(RangeError);
    expect(() => waterSlopeAmplitudes("water-river", NaN)).toThrow(RangeError);
  });
});

describe("twelve-wave table", () => {
  it("holds the generated wavelength series 0.3 * (5/0.3)^(i/11) m", () => {
    expect(WATER_SURFACE_V1.wavelengthsM).toHaveLength(12);
    WATER_SURFACE_V1.wavelengthsM.forEach((lambda, i) =>
      expect(lambda).toBeCloseTo(0.3 * Math.pow(5 / 0.3, i / 11), 12));
    expect(WATER_SURFACE_V1.wavelengthsM[0]).toBeCloseTo(0.3, 12);
    expect(WATER_SURFACE_V1.wavelengthsM[11]).toBeCloseTo(5.0, 12);
  });
  it("holds the golden-angle direction offsets 1.2 * sin(i * 2.399963)", () => {
    expect(WATER_SURFACE_V1.directionOffsetsRad).toHaveLength(12);
    WATER_SURFACE_V1.directionOffsetsRad.forEach((offset, i) =>
      expect(offset).toBeCloseTo(1.2 * Math.sin(i * 2.399963), 12));
    // The offsets spread over +/-1.2 rad and never repeat, so no two waves align.
    for (const offset of WATER_SURFACE_V1.directionOffsetsRad) {
      expect(Math.abs(offset)).toBeLessThanOrEqual(1.2);
    }
    expect(new Set(WATER_SURFACE_V1.directionOffsetsRad).size).toBe(12);
  });
  it("holds the golden-ratio phases 2 pi * fract(i * 0.6180339887)", () => {
    expect(WATER_SURFACE_V1.phasesRad).toHaveLength(12);
    WATER_SURFACE_V1.phasesRad.forEach((phase, i) =>
      expect(phase).toBeCloseTo(2 * Math.PI * (i * 0.6180339887 % 1), 12));
    for (const phase of WATER_SURFACE_V1.phasesRad) {
      expect(phase).toBeGreaterThanOrEqual(0);
      expect(phase).toBeLessThan(2 * Math.PI);
    }
  });
});

describe("setWaterSurfaceWeather", () => {
  const uniforms = (): WaterSurfaceUniforms => createWaterSurfaceUniforms();
  it("maps the clockwise-from-north direction onto the city frame", () => {
    const u = uniforms();
    setWaterSurfaceWeather(u, { ...CITY_WEATHER_CLEAR, windMps: 3, windDirectionDeg: 0 });
    expect(u.uWaterWind.value.x).toBeCloseTo(0, 12);
    expect(u.uWaterWind.value.y).toBeCloseTo(-1, 12);
    setWaterSurfaceWeather(u, { ...CITY_WEATHER_CLEAR, windMps: 3, windDirectionDeg: 90 });
    expect(u.uWaterWind.value.x).toBeCloseTo(1, 12);
    expect(u.uWaterWind.value.y).toBeCloseTo(0, 12);
    expect(u.uWaterWindMps.value).toBe(3);
  });
  it("derives rain from the wetness module and gives frozen precipitation zero", () => {
    const u = uniforms();
    setWaterSurfaceWeather(u, CITY_WEATHER_PRESETS.rain);
    expect(u.uWaterRain.value).toBe(surfaceWetnessFromWeather(CITY_WEATHER_PRESETS.rain));
    expect(u.uWaterRain.value).toBeGreaterThan(0.5);
    setWaterSurfaceWeather(u, CITY_WEATHER_PRESETS.snow);
    expect(u.uWaterRain.value).toBe(0);
    setWaterSurfaceWeather(u, CITY_WEATHER_CLEAR);
    expect(u.uWaterRain.value).toBe(0);
    expect(u.uWaterWindMps.value).toBe(0);
  });
  it("keeps the direction vector by reference and rejects invalid settings", () => {
    const u = uniforms();
    const direction = u.uWaterWind.value;
    setWaterSurfaceWeather(u, CITY_WEATHER_PRESETS.rain);
    expect(u.uWaterWind.value).toBe(direction);
    expect(() => setWaterSurfaceWeather(u, { ...CITY_WEATHER_CLEAR, windMps: -2 })).toThrow(RangeError);
    expect(() => setWaterSurfaceWeather(u, { ...CITY_WEATHER_CLEAR, windDirectionDeg: NaN })).toThrow(RangeError);
  });
});

describe("setWaterSurfaceTime", () => {
  it("publishes finite nonnegative times and rejects the rest", () => {
    const u = createWaterSurfaceUniforms();
    setWaterSurfaceTime(u, 12.5);
    expect(u.uWaterTime.value).toBe(12.5);
    expect(() => setWaterSurfaceTime(u, -1)).toThrow(RangeError);
    expect(() => setWaterSurfaceTime(u, NaN)).toThrow(RangeError);
    expect(() => setWaterSurfaceTime(u, Infinity)).toThrow(RangeError);
  });
});

describe("applyWaterSurface", () => {
  // The real `meshphysical` sources: applyWaterSurface only needs the chunk tokens.
  const physicalShader = () => ({
    uniforms: {} as Record<string, unknown>,
    vertexShader: THREE.ShaderLib.physical.vertexShader,
    fragmentShader: THREE.ShaderLib.physical.fragmentShader,
  });
  const patch = (presetId = "water-river") => {
    const material = new THREE.MeshPhysicalMaterial({ name: `water:${presetId}` });
    const u = createWaterSurfaceUniforms();
    applyWaterSurface(material, presetId, u);
    return { material, u };
  };
  it("patches the physical shader sources in place with shared uniform objects", () => {
    const { material, u } = patch();
    const shader = physicalShader();
    material.onBeforeCompile(shader as never, undefined as never);
    // Shared uniforms by identity, not copies: the layer's weather updates reach them.
    expect(shader.uniforms.uWaterTime).toBe(u.uWaterTime);
    expect(shader.uniforms.uWaterWind).toBe(u.uWaterWind);
    expect(shader.uniforms.uWaterWindMps).toBe(u.uWaterWindMps);
    expect(shader.uniforms.uWaterRain).toBe(u.uWaterRain);
    // Vertex: the varying declaration after <common> and the world XZ after project_vertex.
    expect(shader.vertexShader).toContain("varying vec2 vWaterXZ;");
    const projectIndex = shader.vertexShader.indexOf("#include <project_vertex>");
    const xzIndex = shader.vertexShader.indexOf("vWaterXZ = ( modelMatrix");
    expect(xzIndex).toBeGreaterThan(projectIndex);
    // Fragment: the normal replacement sits between the chunk it replaces and the
    // lighting chunk that consumes roughnessFactor.
    const mapsIndex = shader.fragmentShader.indexOf("#include <normal_fragment_maps>");
    const normalIndex = shader.fragmentShader.indexOf("normal = waterSurfaceNormal( waterLostSlope2 );");
    const lightsIndex = shader.fragmentShader.indexOf("#include <lights_physical_fragment>");
    expect(normalIndex).toBeGreaterThan(mapsIndex);
    expect(lightsIndex).toBeGreaterThan(normalIndex);
    material.dispose();
  });
  it("rotates every wave direction with the wind uniform and returns a view-space normal", () => {
    const { material } = patch();
    const shader = physicalShader();
    material.onBeforeCompile(shader as never, undefined as never);
    WATER_SURFACE_V1.directionOffsetsRad.forEach((offset, index) => {
      const rot = new RegExp(`waveRot${index} = vec2\\( ([-0-9.e]+), ([-0-9.e]+) \\);`).exec(shader.fragmentShader);
      expect(rot).not.toBeNull();
      expect(Number(rot![1])).toBeCloseTo(Math.cos(offset), 12);
      expect(Number(rot![2])).toBeCloseTo(Math.sin(offset), 12);
      expect(shader.fragmentShader).toContain(
        `vec2 waveDir${index} = vec2( waveRot${index}.x * uWaterWind.x - waveRot${index}.y * uWaterWind.y,`);
    });
    // The wind uniform is read, not only declared.
    expect(shader.fragmentShader.match(/uWaterWind\b/g)!.length).toBeGreaterThan(1);
    // vNormal lives in view space; the ripple normal is rotated there by viewMatrix.
    expect(shader.fragmentShader).toContain("return normalize( ( viewMatrix * vec4( worldNormal, 0.0 ) ).xyz );");
    material.dispose();
  });
  it("writes the dispersion omega for the 0.3 m wave as a literal", () => {
    const { material } = patch();
    const shader = physicalShader();
    material.onBeforeCompile(shader as never, undefined as never);
    const lambda = WATER_SURFACE_V1.wavelengthsM[0]!;
    const k = 2 * Math.PI / lambda;
    const omega = Math.sqrt(9.81 * k + 7.28e-5 * k * k * k);
    const literal = Number(/omega0 = ([0-9.]+);/.exec(shader.fragmentShader)![1]!);
    expect(Math.abs(literal - omega)).toBeLessThan(1e-6);
    material.dispose();
  });
  it("compiles all twelve waves into the fragment source", () => {
    const { material } = patch();
    const shader = physicalShader();
    material.onBeforeCompile(shader as never, undefined as never);
    expect(shader.fragmentShader.match(/vec2 waveDir\d+ =/g)).toHaveLength(12);
    expect(shader.fragmentShader.match(/const vec2 waveRot\d+ =/g)).toHaveLength(12);
    material.dispose();
  });
  it("marks the material and keys the program by version and preset", () => {
    // `needsUpdate` is setter-only on THREE.Material: the observable effect is a
    // version bump, which forces a program rebuild with the new cache key.
    const material = new THREE.MeshPhysicalMaterial({ name: "water:pond" });
    const versionBefore = material.version;
    applyWaterSurface(material, "water-pond", createWaterSurfaceUniforms());
    expect(material.userData.waterSurface).toEqual({ version: "city-water-surface-v2",
      presetId: "water-pond", typeResponse: 0.45, marker: "city-water-surface/v2" });
    expect(material.customProgramCacheKey()).toBe("city-water-surface-v2:water-pond");
    expect(material.version).toBe(versionBefore + 1);
    material.dispose();
  });
  it("throws when patching twice, on a normal map, for an unknown preset", () => {
    const { material } = patch();
    expect(() => applyWaterSurface(material, "water-river", createWaterSurfaceUniforms()))
      .toThrow(/Water surface needs an unpatched water material/);
    material.dispose();
    const withNormalMap = new THREE.MeshPhysicalMaterial({ name: "water:bad",
      normalMap: new THREE.Texture() });
    expect(() => applyWaterSurface(withNormalMap, "water-river", createWaterSurfaceUniforms()))
      .toThrow(/without a normal map/);
    withNormalMap.normalMap!.dispose();
    withNormalMap.dispose();
    const flat = new THREE.MeshPhysicalMaterial({ name: "water:flat", flatShading: true });
    expect(() => applyWaterSurface(flat, "water-river", createWaterSurfaceUniforms()))
      .toThrow(/smooth-shaded/);
    flat.dispose();
    const unpatched = new THREE.MeshPhysicalMaterial({ name: "water:unknown" });
    expect(() => applyWaterSurface(unpatched, "water-lagoon", createWaterSurfaceUniforms()))
      .toThrow(/no type response for preset: water-lagoon/);
    unpatched.dispose();
  });
  it("names the chunk when the fragment source lacks normal_fragment_maps", () => {
    const material = new THREE.MeshPhysicalMaterial({ name: "water:broken" });
    const u = createWaterSurfaceUniforms();
    applyWaterSurface(material, "water-river", u);
    const shader = { uniforms: {} as Record<string, unknown>,
      vertexShader: THREE.ShaderLib.physical.vertexShader,
      fragmentShader: "#include <common>\n#include <lights_physical_fragment>" };
    expect(() => material.onBeforeCompile(shader as never, undefined as never))
      .toThrow(/Water surface could not patch <normal_fragment_maps>/);
    material.dispose();
  });
  it("gives every rule-set water preset a type response", () => {
    for (const presetId of WATER_PRESET_IDS) {
      expect(WATER_SURFACE_V1.typeResponse[presetId], presetId).toBeTypeOf("number");
      const response = WATER_SURFACE_V1.typeResponse[presetId]!;
      expect(response).toBeGreaterThan(0);
      expect(response).toBeLessThanOrEqual(1);
    }
    expect(Object.keys(WATER_SURFACE_V1.typeResponse).sort()).toEqual([...WATER_PRESET_IDS].sort());
  });
});
