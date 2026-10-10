import * as THREE from "three";
import { GLTFLoader, type GLTF } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

type CityImage = HTMLImageElement | ImageBitmap;

type DecodeOptions = { readonly kind: "image" } | {
  readonly kind: "bitmap";
  readonly options: ImageBitmapOptions;
};

function stableOptions(options: object): string {
  return JSON.stringify(Object.entries(options).sort(([a], [b]) => a.localeCompare(b)));
}

function blobKey(url: string, credentials: RequestCredentials, headers: Record<string, string>): string {
  return JSON.stringify([url, credentials, stableOptions(headers)]);
}

/** Cache only immutable bytes and decoded sources; models and texture parameters remain private. */
export class CityAssetCache {
  private readonly bytes = new Map<string, Promise<Blob>>();
  private readonly sources = new Map<string, Promise<THREE.Source<CityImage>>>();

  blob(url: string, credentials: RequestCredentials = "same-origin",
    headers: Record<string, string> = {}): Promise<Blob> {
    const key = blobKey(url, credentials, headers);
    let pending = this.bytes.get(key);
    if (pending === undefined) {
      pending = fetch(url, { credentials, headers }).then(response => {
        if (!response.ok) throw new Error(`City asset failed: ${response.status} ${url}`);
        return response.blob();
      });
      this.bytes.set(key, pending);
    }
    return pending;
  }

  source(url: string, decode: DecodeOptions, credentials: RequestCredentials = "same-origin",
    headers: Record<string, string> = {}): Promise<THREE.Source<CityImage>> {
    const key = JSON.stringify([url, decode.kind,
      decode.kind === "bitmap" ? stableOptions(decode.options) : "", credentials, stableOptions(headers)]);
    let pending = this.sources.get(key);
    if (pending === undefined) {
      pending = this.blob(url, credentials, headers).then(async blob => {
        if (decode.kind === "bitmap") return new THREE.Source(await createImageBitmap(blob, decode.options));
        const objectUrl = URL.createObjectURL(blob);
        try {
          return new THREE.Source(await new THREE.ImageLoader().loadAsync(objectUrl));
        } finally {
          URL.revokeObjectURL(objectUrl);
        }
      }).finally(() => {
        // The decoded source is cached; image bytes are only shared while decodes are in flight.
        this.bytes.delete(blobKey(url, credentials, headers));
      });
      this.sources.set(key, pending);
    }
    return pending;
  }
}

// The city and entity loaders share the viewer's asset lifetime, without enabling THREE.Cache globally.
export const cityAssetCache = new CityAssetCache();
const textureReadiness = new WeakMap<THREE.Texture, Promise<void>>();

function resolvedUrl(manager: THREE.LoadingManager, url: string): string {
  return new URL(manager.resolveURL(url), document.baseURI).href;
}

/** Return a new texture immediately, as FBXLoader requires, then attach the shared source. */
export class SharedCityTextureLoader extends THREE.Loader<THREE.Texture<CityImage>> {
  constructor(manager = new THREE.LoadingManager(), private readonly decode: DecodeOptions = { kind: "image" },
    private readonly cache = cityAssetCache) {
    super(manager);
  }

  override load(url: string, onLoad?: (texture: THREE.Texture<CityImage>) => void,
    _onProgress?: (event: ProgressEvent) => void, onError?: (error: unknown) => void): THREE.Texture<CityImage> {
    const resolved = resolvedUrl(this.manager, (this.path ?? "") + url);
    const texture = new THREE.Texture<CityImage>();
    this.manager.itemStart(resolved);
    const credentials = this.crossOrigin === "anonymous" ? "same-origin" : "include";
    const ready = this.cache.source(resolved, this.decode, credentials, this.requestHeader).then(source => {
      // Mark the new texture without invalidating the already decoded shared source.
      texture.needsUpdate = true;
      texture.source = source;
    });
    textureReadiness.set(texture, ready);
    void ready.then(() => onLoad?.(texture)).catch(error => {
      onError?.(error);
      this.manager.itemError(resolved);
    }).finally(() => this.manager.itemEnd(resolved));
    return texture;
  }
}

export function shareFbxTextures(manager: THREE.LoadingManager): void {
  manager.addHandler(/\.(?:bmp|jpe?g|png|tiff?|tga|webp)(?:[?#].*)?$/i, new SharedCityTextureLoader(manager));
}

/** Parse each model separately: callers change hierarchy, geometry and materials after loading. */
export async function loadSharedFbx(loader: FBXLoader, url: string): Promise<THREE.Group> {
  const resolved = resolvedUrl(loader.manager, loader.path + url);
  const bytes = await cityAssetCache.blob(resolved, loader.withCredentials ? "include" : "same-origin", loader.requestHeader);
  const model = loader.parse(await bytes.arrayBuffer(), loader.resourcePath || loader.path || THREE.LoaderUtils.extractUrlBase(url));
  const ready: Promise<void>[] = [];
  model.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture) {
          const pending = textureReadiness.get(value);
          if (pending !== undefined) ready.push(pending);
        }
      }
    }
  });
  await Promise.all(ready);
  return model;
}

class SharedCityGltfLoader extends GLTFLoader {
  override load(url: string, onLoad: (gltf: GLTF) => void,
    _onProgress?: (event: ProgressEvent) => void, onError?: (error: unknown) => void): void {
    const resolved = resolvedUrl(this.manager, this.path + url);
    const relativePath = THREE.LoaderUtils.extractUrlBase(url);
    const path = this.resourcePath || (this.path ? THREE.LoaderUtils.resolveURL(relativePath, this.path) : relativePath);
    this.manager.itemStart(resolved);
    void cityAssetCache.blob(resolved, this.withCredentials ? "include" : "same-origin", this.requestHeader)
      .then(blob => blob.arrayBuffer()).then(bytes => this.parseAsync(bytes, path)).then(onLoad).catch(error => {
        onError?.(error);
        this.manager.itemError(resolved);
      }).finally(() => this.manager.itemEnd(resolved));
  }
}

export function createSharedCityGltfLoader(): GLTFLoader {
  return new SharedCityGltfLoader().register(parser => {
    const native = parser.textureLoader;
    // Respect GLTFLoader's browser-specific choice and its ImageBitmap decode options.
    const decode: DecodeOptions = native instanceof THREE.ImageBitmapLoader
      ? { kind: "bitmap", options: { ...native.options, colorSpaceConversion: "none" } }
      : { kind: "image" };
    const shared = new SharedCityTextureLoader(parser.options.manager, decode)
      .setCrossOrigin(native.crossOrigin).setRequestHeader(native.requestHeader);
    const loadTextureImage = parser.loadTextureImage.bind(parser);
    parser.loadTextureImage = (textureIndex, sourceIndex, loader) =>
      loadTextureImage(textureIndex, sourceIndex, loader === native ? shared : loader);
    return { name: "AERO_shared_texture_sources" };
  });
}

/** A slice may exceed the budget by one atomic item; yield before starting another. */
export async function runInFrameSlices<T>(items: Iterable<T>,
  work: (item: T) => void, budgetMs = 16,
  now: () => number = () => performance.now(),
  nextFrame: () => Promise<void> = () => new Promise(resolve => requestAnimationFrame(() => resolve()))): Promise<void> {
  // Begin in a fresh task so preceding model construction is not part of the first slice.
  await nextFrame();
  let sliceStarted = now();
  for (const item of items) {
    work(item);
    if (now() - sliceStarted > budgetMs) {
      await nextFrame();
      sliceStarted = now();
    }
  }
}

/**
 * Wait, without a blocking GL call, until the GPU has executed every command issued so far.
 * Without KHR_parallel_shader_compile, compileAsync resolves before its links run; the next
 * synchronous program query would otherwise wait for the whole queue in one task.
 */
export async function waitForGpuCommands(renderer: THREE.WebGLRenderer,
  nextFrame: () => Promise<void> = () => new Promise(resolve => requestAnimationFrame(() => resolve()))): Promise<void> {
  const gl = renderer.getContext() as WebGL2RenderingContext; // Three r163+ creates only WebGL2 contexts.
  const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
  if (sync === null) throw new Error("WebGL fenceSync returned no sync object");
  gl.flush();
  try {
    // WebGL updates sync status only between tasks, so this query does not block.
    while (gl.getSyncParameter(sync, gl.SYNC_STATUS) !== gl.SIGNALED) {
      if (gl.isContextLost()) throw new Error("WebGL context lost while waiting for GPU commands");
      await nextFrame();
    }
  } finally {
    gl.deleteSync(sync);
  }
}
