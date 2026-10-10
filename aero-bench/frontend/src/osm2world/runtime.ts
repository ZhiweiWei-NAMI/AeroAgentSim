export const OSM2WORLD_STYLE_BASE = "/osm2world/style";
export const OSM2WORLD_RUNTIME_MODULE = "/osm2world/osm2world-core-web.mjs";
export const OSM2WORLD_STYLE_PROPERTIES = `${OSM2WORLD_STYLE_BASE}/standard.properties`;

export interface O2WMesh {
  elementId(): string | null;
  modelClass(): string | null;
  materialName(): string;
  positions(): number[];
  indices(): number[];
  normals(): number[];
  uvs(): number[];
  color(): { readonly 0: number; readonly 1: number; readonly 2: number };
  baseColorTexture(): string | null;
  opacityTexture(): string | null;
  normalTexture(): string | null;
  ormTexture(): string | null;
  transparency(): boolean;
  clampTextures(): boolean;
}

export function resolveTextureUrl(path: string | null, styleBase = OSM2WORLD_STYLE_BASE): string | null {
  if (path === null || path.length === 0) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("//") || path.split("/").includes("..")) throw new Error("OSM2World textures must use bundled style assets");
  const trimmed = path.replace(/^\.\//, "");
  if (trimmed.startsWith("/styles/default/")) return `${styleBase}/${trimmed.slice("/styles/default/".length)}`;
  if (trimmed.startsWith("/osm2world/style/")) return trimmed;
  if (trimmed.startsWith("/")) throw new Error("OSM2World texture path is outside the bundled style");
  return `${styleBase}/${trimmed}`;
}

export interface O2WConfig {}
export interface O2WConverter {
  setConfig(config: O2WConfig): void;
  convertJson(json: string, success: (meshes: O2WMesh[]) => void, failure: (error: unknown) => void, options: Readonly<Record<string, unknown>>): void;
}
export interface O2WModule {
  readonly O2WConverter: new () => O2WConverter;
  readonly loadO2WConfig: (
    url: string,
    options: Readonly<Record<string, string>>,
    success: (config: O2WConfig) => void,
    failure: (error: unknown) => void,
  ) => void;
}

let modulePromise: Promise<O2WModule> | undefined;
let configPromise: Promise<O2WConfig> | undefined;

export function loadO2WModule(): Promise<O2WModule> {
  const dynamicImport = new Function("path", "return import(path)") as (path: string) => Promise<O2WModule>;
  modulePromise ??= dynamicImport(OSM2WORLD_RUNTIME_MODULE);
  return modulePromise;
}

export async function loadO2WConfig(): Promise<O2WConfig> {
  configPromise ??= loadO2WModule().then((module) => new Promise<O2WConfig>((resolve, reject) => {
    module.loadO2WConfig(new URL(OSM2WORLD_STYLE_PROPERTIES, window.location.href).href, { lod: "3", mapProjection: "MetricMapProjection", keepOsmElements: "true", windowsOnUnlevelledBuildings: "true", material_ROOF_DEFAULT_color: "#9b424b", material_BUILDING_DEFAULT_color: "#e7d791" }, resolve, reject);
  }));
  return configPromise;
}

export async function convertOsmJson(osmJson: string): Promise<O2WMesh[]> {
  const [module, config] = await Promise.all([loadO2WModule(), loadO2WConfig()]);
  return new Promise<O2WMesh[]>((resolve, reject) => {
    const converter = new module.O2WConverter();
    converter.setConfig(config);
    converter.convertJson(osmJson, resolve, reject, {});
  });
}
