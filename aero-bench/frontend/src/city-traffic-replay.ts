import { AssetResolver } from "./asset-resolver";
import { parseVisualAssetInventory, VerifiedVisualAssets } from "./city-authoring-api";
import { CityTrafficPreview, type CitySignalSelection } from "./city-presentation";
import type { CitySceneJsonAssetRef } from "./city-scene-config";
import { assertNotAborted } from "./verified-bytes";

/** The declared source URL supplies bytes; Three.js receives only the verified blob. */
export function createCitySignalFixtureAssets(ref: CitySceneJsonAssetRef | undefined,
  viewerHref: string, signal?: AbortSignal): VerifiedVisualAssets {
  assertNotAborted(signal);
  if (ref === undefined) throw new Error("City traffic requires a declared signal model byte identity");
  const entries = parseVisualAssetInventory({ schema_version: "aero-bench.city-visual-assets/v1",
    assets: [{ path: ref.url, sha256: ref.sha256, size_bytes: ref.size_bytes, media_type: "model/gltf-binary" }] });
  const base = new URL("/", viewerHref);
  const source = new URL(ref.url, base);
  const digestUrl = new URL(`assets/${ref.sha256}`, base);
  const resolver = new AssetResolver({ baseHref: base.href, fetch: async (input, init) => {
    const requested = input instanceof Request ? input.url : String(input);
    if (requested !== digestUrl.href) throw new Error("Signal model request differs from its declared byte identity");
    return fetch(source, init);
  } });
  return new VerifiedVisualAssets(entries, resolver, signal);
}

/** Own the short-lived verified source reader across the actual replay model load. */
export async function loadCityTrafficReplay(trafficUrl: string, flightUrl: string,
  signalModel: CitySceneJsonAssetRef | undefined, viewerHref: string,
  drawnSignals: CitySignalSelection, signal?: AbortSignal): Promise<CityTrafficPreview> {
  const assets = createCitySignalFixtureAssets(signalModel, viewerHref, signal);
  const abort = (): void => assets.dispose();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    await assets.preload([signalModel!.url]);
    assertNotAborted(signal);
    return await CityTrafficPreview.load(trafficUrl, flightUrl, assets, drawnSignals);
  } finally {
    signal?.removeEventListener("abort", abort);
    assets.dispose();
  }
}
