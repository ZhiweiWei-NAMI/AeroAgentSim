import * as THREE from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CityAssetCache, SharedCityTextureLoader, createSharedCityGltfLoader,
  loadSharedFbx, shareFbxTextures, runInFrameSlices, waitForGpuCommands } from "./city-asset-cache";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function mockImages() {
  const image = { width: 2048, height: 2048 };
  const fetchMock = vi.fn(async () => new Response(new Blob(["image"])));
  const decode = vi.fn(async () => image);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("createImageBitmap", decode);
  return { fetchMock, decode };
}

describe("CityAssetCache", () => {
  it("shares in-flight and completed image sources for the same URL and decode options", async () => {
    const { fetchMock, decode } = mockImages();
    const cache = new CityAssetCache();
    const options = { kind: "bitmap", options: { premultiplyAlpha: "none", colorSpaceConversion: "none" } } as const;
    const [a, b] = await Promise.all([cache.source("https://assets.test/car.webp", options),
      cache.source("https://assets.test/car.webp", options)]);
    const c = await cache.source("https://assets.test/car.webp", {
      kind: "bitmap", options: { colorSpaceConversion: "none", premultiplyAlpha: "none" },
    });
    expect(a).toBe(b);
    expect(a).toBe(c);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(decode).toHaveBeenCalledTimes(1);
  });

  it("separates decode options but shares fetched bytes", async () => {
    const { fetchMock, decode } = mockImages();
    const cache = new CityAssetCache();
    const [a, b] = await Promise.all([cache.source("https://assets.test/car.webp", {
      kind: "bitmap", options: { imageOrientation: "none" },
    }), cache.source("https://assets.test/car.webp", {
      kind: "bitmap", options: { imageOrientation: "flipY" },
    })]);
    expect(a).not.toBe(b);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(decode).toHaveBeenCalledTimes(2);
  });

  it("releases image bytes once the shared source is decoded", async () => {
    const { fetchMock } = mockImages();
    const cache = new CityAssetCache();
    const source = await cache.source("https://assets.test/released.webp", { kind: "bitmap", options: {} });
    expect(await cache.source("https://assets.test/released.webp", { kind: "bitmap", options: {} })).toBe(source);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await cache.blob("https://assets.test/released.webp");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps every texture's sampler, UV transform, colour space and flipY private", async () => {
    mockImages();
    const loader = new SharedCityTextureLoader(new THREE.LoadingManager(), {
      kind: "bitmap", options: { premultiplyAlpha: "none" },
    }, new CityAssetCache());
    const [a, b] = await Promise.all([loader.loadAsync("https://assets.test/params.webp"),
      loader.loadAsync("https://assets.test/params.webp")]);
    a.wrapS = THREE.RepeatWrapping;
    a.minFilter = THREE.NearestFilter;
    a.colorSpace = THREE.SRGBColorSpace;
    a.offset.set(0.2, 0.4);
    a.repeat.set(2, 3);
    a.flipY = false;
    a.generateMipmaps = false;
    expect(a).not.toBe(b);
    expect(a.source).toBe(b.source);
    expect(b.wrapS).toBe(THREE.ClampToEdgeWrapping);
    expect(b.minFilter).toBe(THREE.LinearMipmapLinearFilter);
    expect(b.colorSpace).toBe(THREE.NoColorSpace);
    expect(b.offset.toArray()).toEqual([0, 0]);
    expect(b.repeat.toArray()).toEqual([1, 1]);
    expect(b.flipY).toBe(true);
    expect(b.generateMipmaps).toBe(true);
    const version = a.source.version;
    const later = await loader.loadAsync("https://assets.test/params.webp");
    expect(later.source).toBe(a.source);
    expect(a.source.version).toBe(version);
    expect(later.version).toBeGreaterThan(0);
  });

  it("keys after alias resolution and balances each manager's load lifecycle", async () => {
    const { fetchMock } = mockImages();
    const cache = new CityAssetCache();
    const managers = [new THREE.LoadingManager(), new THREE.LoadingManager()];
    const ended = managers.map(manager => vi.spyOn(manager, "itemEnd"));
    const loaders = managers.map(manager => {
      manager.setURLModifier(() => "https://assets.test/aliased.webp");
      return new SharedCityTextureLoader(manager, { kind: "bitmap", options: {} }, cache);
    });
    const [a, b] = await Promise.all([loaders[0]!.loadAsync("wheel.tga"), loaders[1]!.loadAsync("Wheel.png")]);
    expect(a.source).toBe(b.source);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // itemEnd follows the load callback in the same promise chain.
    await Promise.resolve();
    expect(ended.every(end => end.mock.calls.length === 1)).toBe(true);
  });

  it("does not mix verified blob URL identities or failed requests", async () => {
    const { fetchMock } = mockImages();
    const cache = new CityAssetCache();
    const options = { kind: "bitmap", options: {} } as const;
    expect(await cache.source("blob:https://assets.test/digest-a", options))
      .not.toBe(await cache.source("blob:https://assets.test/digest-b", options));
    fetchMock.mockResolvedValueOnce(new Response("missing", { status: 404 }));
    await expect(cache.source("https://assets.test/missing.webp", options)).rejects.toThrow("404");
    await expect(cache.source("https://assets.test/missing.webp", options)).rejects.toThrow("404");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("uses the shared source across separate GLTF parses without changing samplers", async () => {
    const { fetchMock, decode } = mockImages();
    const json = JSON.stringify({ asset: { version: "2.0" }, scenes: [{ nodes: [] }], scene: 0,
      images: [{ uri: "https://assets.test/gltf-shared.webp" }],
      samplers: [{ wrapS: 10497 }, { wrapS: 33071 }],
      textures: [{ source: 0, sampler: 0 }, { source: 0, sampler: 1 }] });
    const [a, b] = await Promise.all([createSharedCityGltfLoader().parseAsync(json, ""),
      createSharedCityGltfLoader().parseAsync(json, "")]);
    const [first, second] = await Promise.all([a.parser.getDependency("texture", 0),
      b.parser.getDependency("texture", 1)]) as [THREE.Texture, THREE.Texture];
    expect(first.source).toBe(second.source);
    expect(first.flipY).toBe(false);
    expect(second.flipY).toBe(false);
    expect(first.wrapS).toBe(THREE.RepeatWrapping);
    expect(second.wrapS).toBe(THREE.ClampToEdgeWrapping);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(decode).toHaveBeenCalledWith(expect.anything(), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
  });

  it("fetches repeated FBX bytes once, parses independently and waits for image readiness", async () => {
    const { fetchMock } = mockImages();
    const manager = new THREE.LoadingManager();
    const cache = new CityAssetCache();
    const textures = new SharedCityTextureLoader(manager, { kind: "bitmap", options: {} }, cache);
    manager.addHandler(/\.webp$/, textures);
    let finishDecode!: (image: { width: number; height: number }) => void;
    vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise(resolve => { finishDecode = resolve; })));
    const parsedBuffers: ArrayBuffer[] = [];
    vi.spyOn(FBXLoader.prototype, "parse").mockImplementation(bytes => {
      if (typeof bytes === "string") throw new Error("Expected binary FBX input");
      parsedBuffers.push(bytes);
      const model = new THREE.Group();
      model.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhongMaterial({
        map: textures.load("https://assets.test/fbx-image.webp"),
      })));
      return model;
    });
    let completed = false;
    const models = Promise.all([loadSharedFbx(new FBXLoader(manager), "https://assets.test/repeated.fbx"),
      loadSharedFbx(new FBXLoader(manager), "https://assets.test/repeated.fbx")]).then(result => {
      completed = true; return result;
    });
    await vi.waitFor(() => expect(finishDecode).toBeTypeOf("function"));
    expect(completed).toBe(false);
    finishDecode({ width: 32, height: 32 });
    const [a, b] = await models;
    expect(a).not.toBe(b);
    expect(parsedBuffers[0]).not.toBe(parsedBuffers[1]);
    const first = a.children[0] as THREE.Mesh<THREE.BoxGeometry, THREE.MeshPhongMaterial>;
    const second = b.children[0] as THREE.Mesh<THREE.BoxGeometry, THREE.MeshPhongMaterial>;
    expect(first.material).not.toBe(second.material);
    expect(first.material.map!.source).toBe(second.material.map!.source);
    expect(first.material.map!.source.getSize(new THREE.Vector3()).x).toBe(32);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("registers the FBX extension handler, including aliased TGA references", () => {
    const manager = new THREE.LoadingManager();
    shareFbxTextures(manager);
    expect(manager.getHandler(".tga")).toBeInstanceOf(SharedCityTextureLoader);
    expect(manager.getHandler(".png")).toBe(manager.getHandler(".tga"));
  });
});

describe("runInFrameSlices", () => {
  it("yields when the fake clock crosses 16ms and isolates an oversized upload", async () => {
    let clock = 0;
    const slices: number[][] = [];
    const costs = [8, 9, 40, 4, 4];
    const textures = costs.map(() => new THREE.Texture());
    const nextFrame = vi.fn(async () => { slices.push([]); });
    await runInFrameSlices(textures, texture => {
      const cost = costs[textures.indexOf(texture)]!;
      slices.at(-1)!.push(cost);
      clock += cost;
    }, 16, () => clock, nextFrame);
    expect(slices).toEqual([[8, 9], [40], [4, 4]]);
    expect(nextFrame).toHaveBeenCalledTimes(3);
  });

  it("starts a fresh slice before uploading and yields after a final oversized upload", async () => {
    let clock = 0;
    const events: string[] = [];
    await runInFrameSlices([new THREE.Texture()], () => {
      events.push("upload"); clock += 500;
    }, 16, () => clock, async () => { events.push("frame"); });
    expect(events).toEqual(["frame", "upload", "frame"]);
  });
});

describe("waitForGpuCommands", () => {
  const fakeGl = (statuses: number[], events: string[], lost = () => false) => {
    const sync = {};
    return {
      SYNC_GPU_COMMANDS_COMPLETE: 0x9117, SYNC_STATUS: 0x9114, SIGNALED: 0x9119, UNSIGNALED: 0x9118,
      fenceSync: vi.fn(() => { events.push("fence"); return sync; }),
      flush: vi.fn(() => { events.push("flush"); }),
      getSyncParameter: vi.fn((target: object) => { expect(target).toBe(sync); events.push("status"); return statuses.shift() ?? null; }),
      isContextLost: vi.fn(lost),
      deleteSync: vi.fn((target: object) => { expect(target).toBe(sync); events.push("delete"); }),
    };
  };
  const renderer = (gl: object) => ({ getContext: () => gl }) as unknown as THREE.WebGLRenderer;

  it("polls a flushed fence once per frame and deletes it when signalled", async () => {
    const events: string[] = [];
    const gl = fakeGl([0x9118, 0x9118, 0x9119], events);
    await waitForGpuCommands(renderer(gl), async () => { events.push("frame"); });
    expect(gl.fenceSync).toHaveBeenCalledWith(0x9117, 0);
    expect(events).toEqual(["fence", "flush", "status", "frame", "status", "frame", "status", "delete"]);
  });

  it("fails explicitly on context loss and still deletes the fence", async () => {
    const events: string[] = [];
    const gl = fakeGl([], events, () => true);
    await expect(waitForGpuCommands(renderer(gl), async () => { events.push("frame"); }))
      .rejects.toThrow("WebGL context lost while waiting for GPU commands");
    expect(events).toEqual(["fence", "flush", "status", "delete"]);
  });

  it("fails explicitly when no fence is created", async () => {
    const gl = { ...fakeGl([], []), fenceSync: () => null };
    await expect(waitForGpuCommands(renderer(gl), async () => {})).rejects.toThrow("WebGL fenceSync returned no sync object");
  });
});
