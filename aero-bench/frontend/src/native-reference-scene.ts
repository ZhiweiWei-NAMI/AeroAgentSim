import { AssetResolver, MAX_ASSET_BYTES } from "./asset-resolver";
import { parseNativeSceneRegistration, type NativeSceneRegistration } from "./city-authoring-api";
import { parseCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";
import { assertPublicScenario } from "./generated/contract-validators";
import type { PublicScenario } from "./generated/aero-bench-contracts";
import {
  inspectNativeCityPresentation, loadNativeCityPresentation,
  type LoadedNativeCityPresentation, type NativeCityPresentationProgress,
  type NativeCityPresentationInspection,
} from "./native-city-presentation";
import { parseStrictJson } from "./strict-json";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";
import { loadScenarioMeshPack, meshPackAsset } from "./osm2world/assets";
import type { LoadedMeshPack } from "./osm2world/pack-loader";

export interface VerifiedNativeReferenceScene {
  readonly registration: NativeSceneRegistration;
  readonly scenario: PublicScenario;
  readonly draft: CityWorkspaceConfig;
}

export type LoadedNativeReferenceVisual =
  | {
    readonly kind: "mesh-pack";
    readonly pack: LoadedMeshPack;
    readonly nativePresentation: null;
    readonly unavailableReason: null;
    dispose(): void;
  }
  | {
    readonly kind: "native-city";
    readonly pack: null;
    readonly nativePresentation: LoadedNativeCityPresentation;
    readonly unavailableReason: null;
    dispose(): void;
  }
  | {
    readonly kind: "unavailable";
    readonly pack: null;
    readonly nativePresentation: null;
    readonly unavailableReason: string;
    dispose(): void;
  };

export interface LoadNativeReferenceVisualOptions {
  readonly signal?: AbortSignal;
  readonly onProgress?: (progress: NativeCityPresentationProgress) => void;
}

export function inspectNativeReferenceVisual(
  reference: VerifiedNativeReferenceScene,
): NativeCityPresentationInspection {
  return inspectNativeCityPresentation(reference.scenario);
}

export function nativeReferenceHasVisualPresentation(reference: VerifiedNativeReferenceScene): boolean {
  return inspectNativeReferenceVisual(reference).kind !== "unavailable";
}

function nativeReferenceBaseHref(reference: VerifiedNativeReferenceScene): string {
  return new URL(`/authoring/v1/native-scenes/${reference.registration.registration_id}/`,
    window.location.href).href;
}

/** Explicit selection of a registered public scenario, never a city-preview parser input. */
export async function loadNativeReferenceScene(
  selection: NativeSceneRegistration, signal?: AbortSignal,
): Promise<VerifiedNativeReferenceScene> {
  const registration = parseNativeSceneRegistration(selection);
  if (registration.reference_draft.scenePath !== registration.scene_path) {
    throw new Error("reference draft does not select its registered presentation");
  }
  const response = await fetch(registration.scene_url, { signal, redirect: "error",
    headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`native reference scene request failed (${response.status})`);
  const bytes = await readBoundedResponse(response, MAX_ASSET_BYTES, "native reference scene", signal,
    registration.scene_size_bytes);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    byte => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== registration.scene_sha256) throw new Error("native reference scene digest differs from registration");
  const scenario = parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  assertPublicScenario(scenario);
  if (scenario.world_id !== registration.world_id || scenario.world_digest !== registration.world_digest
    || scenario.seed !== registration.reference_draft.seed) {
    throw new Error("native reference scenario identity differs from registration");
  }
  return { registration, scenario,
    draft: parseCityWorkspaceConfig(structuredClone(registration.reference_draft)) };
}

/** Download only assets declared by the selected public scenario and verify every byte. */
export async function loadNativeReferencePack(
  reference: VerifiedNativeReferenceScene, signal?: AbortSignal,
): Promise<LoadedMeshPack | null> {
  const resolver = new AssetResolver({ baseHref: nativeReferenceBaseHref(reference) });
  const abort = (): void => resolver.dispose();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    await Promise.all(reference.scenario.assets.map(asset => resolver.fetchVerifiedBytes(asset.replay_path, {
      sha256: asset.sha256, sizeBytes: asset.size_bytes, mediaType: asset.media_type,
    }, signal)));
    assertNotAborted(signal);
    // A registration without an osm_mesh layer declares no 3D pack. Retain that
    // explicit absence; the viewer reports it rather than selecting another city.
    if (meshPackAsset(reference.scenario) === null) {
      resolver.dispose();
      return null;
    }
    const pack = await loadScenarioMeshPack(reference.scenario, resolver);
    return { ...pack, dispose: () => { pack.dispose(); resolver.dispose(); } };
  } catch (error) {
    resolver.dispose();
    throw error;
  } finally {
    signal?.removeEventListener("abort", abort);
  }
}

/** Load exactly the visual route declared by the registered PublicScenario.
 * Incomplete registrations remain usable compiler inputs, but never borrow a
 * different city's geometry or become visually ready. */
export async function loadNativeReferenceVisual(
  reference: VerifiedNativeReferenceScene,
  options: LoadNativeReferenceVisualOptions = {},
): Promise<LoadedNativeReferenceVisual> {
  const inspection = inspectNativeReferenceVisual(reference);
  if (inspection.kind === "native-city") {
    const presentation = await loadNativeCityPresentation(inspection.plan, {
      baseHref: nativeReferenceBaseHref(reference), signal: options.signal,
      onProgress: options.onProgress,
    });
    return {
      kind: "native-city", pack: null, nativePresentation: presentation,
      unavailableReason: null, dispose: () => presentation.dispose(),
    };
  }
  const pack = await loadNativeReferencePack(reference, options.signal);
  if (inspection.kind === "mesh-pack") {
    if (pack === null) throw new Error("native reference visual route changed while loading");
    return {
      kind: "mesh-pack", pack, nativePresentation: null,
      unavailableReason: null, dispose: () => pack.dispose(),
    };
  }
  if (pack !== null) {
    pack.dispose();
    throw new Error("incomplete native reference unexpectedly loaded a mesh pack");
  }
  return {
    kind: "unavailable", pack: null, nativePresentation: null,
    unavailableReason: inspection.reason, dispose: () => undefined,
  };
}
