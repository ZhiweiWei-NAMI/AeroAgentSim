// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import {
  CITY_AUTHORED_LANDSCAPE_KINDS,
  authoredLandscapeMaterial,
  createCityAuthoredLandscapeLayer,
  parseCityAuthoredLandscape,
  planCityAuthoredLandscape,
  type CityAuthoredLandscapeItem,
  type CityAuthoredLandscapeKind,
  type CityAuthoredLandscapePoint,
  type VerifiedAuthoredLandscapeGeometry,
} from "./city-authored-landscape";
import type { EnvironmentPolygon } from "./city-environment";
import { createSurfaceWetnessUniforms } from "./city-surface-wetness";
import { createWaterSurfaceUniforms } from "./city-water-surface";
import { createVegetationWindUniforms } from "./city-vegetation-wind";

import { ALL_TEXTURE_SET_IDS, fakeTerrainTextureSets } from "./testing/terrain-surface-fixture";
import { createTerrainSurfaceKit } from "./city-terrain-surfaces";
import { authoredGroundMaterialInput, assignGroundMaterial, compileGroundMaterialRules,
  CITY_GROUND_MATERIAL_RULES_V1 } from "./city-ground-material-rules";

const DISPLAYED_SURFACE_SHA256 = "a".repeat(64);

function ring(
  minX: number,
  minZ: number,
  maxX: number,
  maxZ: number,
  closed = false,
): CityAuthoredLandscapePoint[] {
  const points = [
    { x: minX, z: minZ },
    { x: maxX, z: minZ },
    { x: maxX, z: maxZ },
    { x: minX, z: maxZ },
  ];
  return closed ? [...points, points[0]!] : points;
}

function polygon(
  minX: number,
  minZ: number,
  maxX: number,
  maxZ: number,
): EnvironmentPolygon {
  return {
    outline: [
      [minX, minZ],
      [maxX, minZ],
      [maxX, maxZ],
      [minX, maxZ],
    ],
    holes: [],
  };
}

function item(
  id: string,
  kind: CityAuthoredLandscapeKind,
  points = ring(0, 0, 10, 10),
): CityAuthoredLandscapeItem {
  return {
    id,
    label: `Design ${id}`,
    provenance: "authored",
    kind,
    polygon: points,
  };
}

function geometry(
  overrides: Partial<VerifiedAuthoredLandscapeGeometry> = {},
): VerifiedAuthoredLandscapeGeometry {
  return {
    displayedSurfaceSha256: DISPLAYED_SURFACE_SHA256,
    roadGeometry: "published",
    extent: polygon(0, 0, 10, 10),
    roadbed: [polygon(0, 0, 2, 10)],
    walkbed: [polygon(2, 0, 4, 10)],
    buildings: [polygon(4, 0, 6, 10)],
    ...overrides,
  };
}

function triangleCentroid(
  triangle: readonly (readonly [number, number])[],
): readonly [number, number] {
  return [
    (triangle[0]![0] + triangle[1]![0] + triangle[2]![0]) / 3,
    (triangle[0]![1] + triangle[1]![1] + triangle[2]![1]) / 3,
  ];
}

describe("city authored-landscape contract", () => {
  it("round-trips every exact workspace-v3 item kind and normalizes a closing vertex", () => {
    const input = CITY_AUTHORED_LANDSCAPE_KINDS.map((kind, index) =>
      item(
        `landscape-${index + 1}`,
        kind,
        ring(index * 20, 0, index * 20 + 10, 10, index === 0),
      ),
    );
    const parsed = parseCityAuthoredLandscape(
      JSON.parse(JSON.stringify(input)),
    );
    expect(parsed.map((entry) => entry.kind)).toEqual(
      CITY_AUTHORED_LANDSCAPE_KINDS,
    );
    expect(parsed[0]!.polygon).toHaveLength(4);
    expect(
      parseCityAuthoredLandscape(JSON.parse(JSON.stringify(parsed))),
    ).toEqual(parsed);
    expect(Object.keys(parsed[0]!)).toEqual([
      "id",
      "label",
      "provenance",
      "kind",
      "polygon",
    ]);
  });

  it("rejects forged provenance, source identity fields, duplicate IDs, and unknown kinds", () => {
    const valid = item("landscape-1", "green");
    expect(() =>
      parseCityAuthoredLandscape([{ ...valid, provenance: "osm" }]),
    ).toThrow(/provenance/);
    expect(() =>
      parseCityAuthoredLandscape([{ ...valid, sourceId: "osm:way:1" }]),
    ).toThrow(/fields/);
    expect(() => parseCityAuthoredLandscape([valid, valid])).toThrow(
      /duplicated/,
    );
    expect(() =>
      parseCityAuthoredLandscape([{ ...valid, kind: "forest" }]),
    ).toThrow(/kind/);
    expect(() =>
      parseCityAuthoredLandscape([{ ...valid, label: "  " }]),
    ).toThrow(/label/);
    expect(() => parseCityAuthoredLandscape({ items: [valid] })).toThrow(
      /array/,
    );
  });

  it("rejects malformed, non-finite, repeated, degenerate, and self-intersecting rings", () => {
    const valid = item("landscape-1", "green");
    for (const suspect of [
      [
        { x: 0, z: 0 },
        { x: 1, z: 0 },
      ],
      [
        { x: 0, z: 0 },
        { x: Infinity, z: 0 },
        { x: 0, z: 1 },
      ],
      [
        { x: 0, z: 0 },
        { x: 1, z: 0 },
        { x: 1, z: 0 },
        { x: 0, z: 1 },
      ],
    ])
      expect(() =>
        parseCityAuthoredLandscape([{ ...valid, polygon: suspect }]),
      ).toThrow();
    expect(() =>
      parseCityAuthoredLandscape([
        {
          ...valid,
          polygon: [
            { x: 0, z: 0 },
            { x: 1, z: 0 },
            { x: 2, z: 0 },
          ],
        },
      ]),
    ).toThrow(/degenerate/);
    expect(() =>
      parseCityAuthoredLandscape([
        {
          ...valid,
          polygon: [
            { x: 0, z: 0 },
            { x: 2, z: 2 },
            { x: 0, z: 2 },
            { x: 2, z: 0 },
          ],
        },
      ]),
    ).toThrow(/self-intersects/);
    expect(() =>
      parseCityAuthoredLandscape([
        {
          ...valid,
          polygon: [
            { x: 0, z: 0, latitude: 31 },
            { x: 2, z: 0 },
            { x: 0, z: 2 },
          ],
        },
      ]),
    ).toThrow(/fields/);
  });
});

describe("city authored-landscape planning", () => {
  it("clips against the scene extent, motor road, walkbed, and building footprints", () => {
    const plan = planCityAuthoredLandscape(
      [item("landscape-1", "plaza")],
      geometry(),
    );
    expect(plan.items[0]).toMatchObject({
      sourceAreaM2: 100,
      drawnAreaM2: 40,
      removedAreaM2: 60,
    });
    expect(plan.stats).toMatchObject({
      sourceAreaM2: 100,
      drawnAreaM2: 40,
      removedAreaM2: 60,
      countsByKind: { green: 0, plaza: 1, planting_strip: 0 },
    });
    for (const triangle of plan.items[0]!.triangles) {
      expect(triangleCentroid(triangle)[0]).toBeGreaterThanOrEqual(6 - 1e-7);
    }
    expect(plan.displayedSurfaceSha256).toBe(DISPLAYED_SURFACE_SHA256);
  });

  it("clips authored input at the verified scene extent without moving its vertices", () => {
    const authored = item("landscape-1", "green", ring(-5, 0, 10, 10));
    const plan = planCityAuthoredLandscape(
      [authored],
      geometry({
        roadbed: [polygon(20, 20, 21, 21)],
        walkbed: [polygon(22, 22, 23, 23)],
        buildings: [],
      }),
    );
    expect(plan.items[0]!.polygon).toEqual(authored.polygon);
    expect(plan.items[0]).toMatchObject({
      sourceAreaM2: 150,
      drawnAreaM2: 100,
      removedAreaM2: 50,
    });
  });

  it("retains a building courtyard because a footprint hole is not an obstacle", () => {
    const building = {
      outline: polygon(2, 2, 8, 8).outline,
      holes: [polygon(4, 4, 6, 6).outline],
    };
    const plan = planCityAuthoredLandscape(
      [item("landscape-1", "planting_strip")],
      geometry({
        roadbed: [polygon(20, 20, 21, 21)],
        walkbed: [polygon(22, 22, 23, 23)],
        buildings: [building],
      }),
    );
    expect(plan.items[0]!.drawnAreaM2).toBeCloseTo(68, 7);
  });

  it("requires the verified displayed-surface identity and published road geometry", () => {
    const authored = [item("landscape-1", "green")];
    expect(() =>
      planCityAuthoredLandscape(
        authored,
        geometry({ displayedSurfaceSha256: "not-a-digest" }),
      ),
    ).toThrow(/verified published/);
    expect(() =>
      planCityAuthoredLandscape(
        authored,
        geometry({ roadGeometry: "pending-native-gate" as never }),
      ),
    ).toThrow(/verified published/);
    expect(() =>
      planCityAuthoredLandscape(authored, geometry({ roadbed: [] })),
    ).toThrow(/verified published/);
  });
});

describe("city authored-landscape visual layer", () => {
  it("keeps wind-driven planting detail inside the clipped motor-road boundary", () => {
    const plan = planCityAuthoredLandscape(
      [item("green-1", "green")],
      geometry({
        roadbed: [polygon(0, 0, 2, 10)],
        walkbed: [polygon(20, 20, 21, 21)],
        buildings: [],
      }),
    );
    const renderer = createCityAuthoredLandscapeLayer(plan, {
      wetness: createSurfaceWetnessUniforms(),
      wind: createVegetationWindUniforms(),
      labelTextureFactory: () => new THREE.Texture(),
    });
    const blades = renderer.group.children.find(
      (child) => child instanceof THREE.InstancedMesh,
    ) as THREE.InstancedMesh;
    expect(blades.count).toBeGreaterThan(0);
    const matrix = new THREE.Matrix4(),
      position = new THREE.Vector3();
    for (let index = 0; index < blades.count; index++) {
      blades.getMatrixAt(index, matrix);
      position.setFromMatrixPosition(matrix);
      expect(position.x).toBeGreaterThanOrEqual(2.5 - 1e-7);
    }
    const label = renderer.group.children.find(
      (child) => child instanceof THREE.Sprite,
    ) as THREE.Sprite;
    expect(label.position.x).toBeGreaterThan(2);
    renderer.dispose();
  });

  it("labels authored provenance and shares visual wetness and wind uniforms", () => {
    const items = [
      item("green-1", "green", ring(0, 0, 8, 8)),
      item("plaza-1", "plaza", ring(12, 0, 20, 8)),
      item("strip-1", "planting_strip", ring(24, 0, 32, 8)),
    ];
    const plan = planCityAuthoredLandscape(
      items,
      geometry({
        extent: polygon(-5, -5, 40, 15),
        roadbed: [polygon(50, 50, 51, 51)],
        walkbed: [polygon(52, 52, 53, 53)],
        buildings: [],
      }),
    );
    const wetness = createSurfaceWetnessUniforms(),
      wind = createVegetationWindUniforms();
    const labels: string[] = [],
      labelTextures: THREE.Texture[] = [];
    const renderer = createCityAuthoredLandscapeLayer(plan, {
      wetness,
      wind,
      labelTextureFactory: (label) => {
        labels.push(label);
        const texture = new THREE.Texture();
        labelTextures.push(texture);
        return texture;
      },
    });
    expect(renderer.group.name).toContain("not source truth");
    expect(renderer.group.userData).toMatchObject({
      provenance: "authored",
      visualOnly: true,
    });
    expect(labels).toEqual(items.map((entry) => entry.label));
    const surfaces = renderer.group.children.filter(
      (child) =>
        child instanceof THREE.Mesh && !(child instanceof THREE.InstancedMesh),
    ) as THREE.Mesh[];
    const vegetation = renderer.group.children.filter(
      (child) => child instanceof THREE.InstancedMesh,
    ) as THREE.InstancedMesh[];
    const sprites = renderer.group.children.filter(
      (child) => child instanceof THREE.Sprite,
    );
    expect(surfaces).toHaveLength(3);
    expect(vegetation).toHaveLength(2);
    expect(sprites).toHaveLength(3);
    expect(
      surfaces.every(
        (surface) =>
          surface.userData.provenance === "authored" &&
          surface.userData.visualOnly === true &&
          surface.userData.sourceId === undefined,
      ),
    ).toBe(true);

    const wetShader = {
      uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: "#include <common>\n#include <beginnormal_vertex>",
      fragmentShader:
        "#include <common>\n#include <roughnessmap_fragment>\n#include <map_fragment>",
    };
    (surfaces[0]!.material as THREE.MeshStandardMaterial).onBeforeCompile(
      wetShader as never,
      {} as never,
    );
    expect(wetShader.uniforms.uSurfaceWetness).toBe(wetness.uWetness);
    const windShader = {
      uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: "#include <common>\n#include <begin_vertex>",
      fragmentShader: "",
    };
    (vegetation[0]!.material as THREE.MeshStandardMaterial).onBeforeCompile(
      windShader as never,
      {} as never,
    );
    expect(windShader.uniforms.uTime).toBe(wind.uTime);
    expect(windShader.uniforms.uWindDir).toBe(wind.uWindDir);

    const geometries = new Set<THREE.BufferGeometry>();
    renderer.group.traverse((node) => {
      if (node instanceof THREE.Mesh) geometries.add(node.geometry);
    });
    const geometryDisposals = [...geometries].map((resource) =>
      vi.spyOn(resource, "dispose"),
    );
    const materialDisposals = renderer.materials.map((resource) =>
      vi.spyOn(resource, "dispose"),
    );
    const textureDisposals = labelTextures.map((resource) =>
      vi.spyOn(resource, "dispose"),
    );
    renderer.dispose();
    expect(renderer.group.children).toHaveLength(0);
    expect(geometryDisposals.every((spy) => spy.mock.calls.length === 1)).toBe(
      true,
    );
    expect(materialDisposals.every((spy) => spy.mock.calls.length === 1)).toBe(
      true,
    );
    expect(textureDisposals.every((spy) => spy.mock.calls.length === 1)).toBe(
      true,
    );
  });
});

describe("authored terrain presets", () => {
  it("maps the prescribed tags through V1 without changing the material basis", () => {
    const compiled = compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1);
    for (const [kind, tags, family, preset, basis] of [
      ["green", { landuse: "grass" }, "grass", "lawn-maintained", "cover-tag"],
      ["planting_strip", { surface: "woodchips" }, "mulch", "mulch", "surface-tag"],
      ["plaza", { surface: "paving_stones" }, "paving_unit", "paving-unit", "surface-tag"],
    ] as const) {
      const assignment = authoredLandscapeMaterial(item("design", kind));
      const rules = assignGroundMaterial(authoredGroundMaterialInput({ id: "design",
        provenance: { kind: "authored", designId: "design" } }, tags), compiled, assignment.seed);
      expect(assignment).toEqual(rules);
      expect(assignment).toMatchObject({ origin: "authored", sourceRefs: { designId: "design" }, family, preset, basis });
    }
  });

  it("borrows the kit materials, preserves kit wetness, and disposes only its own resources", () => {
    const items = CITY_AUTHORED_LANDSCAPE_KINDS.map((kind, n) => item(`design-${n}`, kind));
    const assignments = items.map(authoredLandscapeMaterial);
    const wetness = createSurfaceWetnessUniforms();
    const kit = createTerrainSurfaceKit(assignments, fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS), wetness,
      createWaterSurfaceUniforms());
    const disposals = kit.materials.map(material => vi.spyOn(material, "dispose"));
    const renderer = createCityAuthoredLandscapeLayer(planCityAuthoredLandscape(items, geometry()), {
      wetness: createSurfaceWetnessUniforms(), wind: createVegetationWindUniforms(), surfaces: kit,
      labelTextureFactory: () => new THREE.Texture(),
    });
    const meshes = renderer.group.children.filter(child => child instanceof THREE.Mesh
      && !(child instanceof THREE.InstancedMesh)) as THREE.Mesh[];
    for (const [n, mesh] of meshes.entries()) {
      expect(mesh.material).toBe(kit.material(assignments[n]!));
      expect(mesh.userData.groundMaterial).toEqual(assignments[n]);
      expect(mesh.geometry.getAttribute("uv")).toBeDefined();
      expect(mesh.geometry.getAttribute("color")).toBeDefined();
      expect(renderer.materials).not.toContain(mesh.material);
    }
    // The real standard shader: the kit's terrain detail patch rejects a source missing its chunks.
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: THREE.ShaderLib.standard.vertexShader,
      fragmentShader: THREE.ShaderLib.standard.fragmentShader };
    (meshes[0]!.material as THREE.MeshStandardMaterial).onBeforeCompile(shader as never, {} as never);
    expect(shader.uniforms.uSurfaceWetness).toBe(wetness.uWetness);
    renderer.dispose();
    expect(disposals.every(spy => spy.mock.calls.length === 0)).toBe(true);
    kit.dispose();
    expect(disposals.every(spy => spy.mock.calls.length === 1)).toBe(true);
  });

  it("retains the original flat colours before a kit exists", () => {
    const items = CITY_AUTHORED_LANDSCAPE_KINDS.map((kind, n) => item(`flat-${n}`, kind));
    const renderer = createCityAuthoredLandscapeLayer(planCityAuthoredLandscape(items, geometry()), {
      wetness: createSurfaceWetnessUniforms(), wind: createVegetationWindUniforms(),
      labelTextureFactory: () => new THREE.Texture(),
    });
    const surfaces = renderer.group.children.filter(child => child instanceof THREE.Mesh
      && !(child instanceof THREE.InstancedMesh)) as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>[];
    expect(surfaces.map(mesh => mesh.material.color.getHex())).toEqual([0x66874c, 0xa59f94, 0x5f7848]);
    expect(surfaces.every(mesh => mesh.userData.groundMaterial === undefined)).toBe(true);
    renderer.dispose();
  });
});
