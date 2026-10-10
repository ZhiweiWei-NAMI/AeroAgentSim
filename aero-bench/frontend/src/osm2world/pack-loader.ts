import { AssetResolver, type DeclaredAsset } from "../asset-resolver";
import { parseMeshPack, unpackMeshBatch, type MeshPackManifest, type PackFile, type PackedBatch } from "./pack";

export interface LoadedMeshPack {
  readonly manifest: MeshPackManifest;
  readonly manifestSha256: string;
  readonly batches: readonly { readonly batch: PackedBatch; readonly arrays: ReturnType<typeof unpackMeshBatch> }[];
  readonly textureUrls: ReadonlyMap<string, string>;
  dispose(): void;
}

async function verifiedBytes(resolver: AssetResolver, file: PackFile, mediaType: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const ref: DeclaredAsset = { sha256: file.sha256, sizeBytes: file.size_bytes, mediaType };
  return resolver.fetchVerifiedBytes(`assets/${file.sha256}`, ref, signal);
}

async function boundedMap<T, R>(values: readonly T[], map: (value: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(values.length);
  let next = 0;
  let failure: unknown;
  let failed = false;
  await Promise.all(Array.from({ length: Math.min(4, values.length) }, async () => {
    while (!failed && next < values.length) {
      const index = next++;
      try { results[index] = await map(values[index]!); }
      catch (error) { failed = true; failure = error; }
    }
  }));
  if (failed) throw failure;
  return results;
}

export async function loadMeshPack(resolver: AssetResolver, ref: PackFile, signal?: AbortSignal,
                                   validateManifest?: (manifest: MeshPackManifest) => void,
                                   onProgress?: (completed: number, total: number) => void,
                                   selectTextures?: (manifest: MeshPackManifest) => ReadonlySet<string>): Promise<LoadedMeshPack> {
  const urls: string[] = [];
  try {
    const bytes = await verifiedBytes(resolver, ref, "application/json", signal);
    const manifest = parseMeshPack(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
    validateManifest?.(manifest);
    const selected = selectTextures?.(manifest);
    if (selected !== undefined && [...selected].some(path => !(path in manifest.textures))) {
      throw new Error("Selected pack texture is not declared by the manifest");
    }
    const textureEntries = Object.entries(manifest.textures)
      .filter(([path]) => selected === undefined || selected.has(path));
    const total = manifest.batches.length + textureEntries.length;
    let completed = 0;
    onProgress?.(completed, total);
    const batches = await boundedMap(manifest.batches, async batch => {
      const buffer = await verifiedBytes(resolver, batch.file, "application/octet-stream", signal);
      const arrays = unpackMeshBatch(batch, buffer);
      onProgress?.(++completed, total);
      return { batch, arrays };
    });
    const textureUrls = new Map<string, string>();
    await boundedMap(textureEntries, async ([path, file]) => {
      const extension = path.toLowerCase().split(".").at(-1);
      const mediaType = extension === "png" ? "image/png" : extension === "jpg" || extension === "jpeg" ? "image/jpeg" : extension === "svg" ? "image/svg+xml" : null;
      if (mediaType === null) throw Error("unsupported packed texture encoding");
      const texture = await resolver.fetchVerified(`assets/${file.sha256}`, { sha256: file.sha256, sizeBytes: file.size_bytes, mediaType }, signal);
      urls.push(texture.url);
      textureUrls.set(path, texture.url);
      onProgress?.(++completed, total);
    });
    return { manifest, manifestSha256: ref.sha256, batches, textureUrls,
      dispose: () => { for (const url of urls) resolver.revoke(url); } };
  } catch (error) {
    for (const url of urls) resolver.revoke(url);
    throw error;
  }
}
