import type { SelectedFleetAssetOption } from "./city-selected-fleet-panel";

const MODEL_ID = /^model:[A-Za-z0-9][A-Za-z0-9_.:-]*$/;

/** Read only the curated UAV preview entries. Catalogue entries are visual
 * choices; their displayed battery and payload values remain user inputs. */
export function selectedFleetOptionsFromCatalog(value: unknown): SelectedFleetAssetOption[] {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("素材目录格式无效");
  }
  const catalog = value as Record<string, unknown>;
  if (catalog.schema_version !== "aero-bench.asset-library/v1" || !Array.isArray(catalog.entries)) {
    throw new Error("素材目录版本或条目无效");
  }
  const result: SelectedFleetAssetOption[] = [];
  const seen = new Set<string>();
  for (const raw of catalog.entries) {
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) continue;
    const entry = raw as Record<string, unknown>;
    if (typeof entry.id !== "string" || !entry.id.startsWith("model:")) continue;
    if (entry.status !== "ready") continue;
    if (!MODEL_ID.test(entry.id) || entry.category !== "uav" || entry.kind !== "model"
        || entry.format !== "GLB"
        || typeof entry.title !== "string" || !entry.title.trim()
        || typeof entry.subgroup !== "string" || !entry.subgroup.trim()
        || typeof entry.model_url !== "string" || !/^\/models\/uav\/[^?#]+\.glb$/.test(entry.model_url)
        || seen.has(entry.id)) {
      throw new Error(`可用 UAV 机型目录条目无效：${entry.id}`);
    }
    seen.add(entry.id);
    result.push({ id: entry.id, title: entry.title, subgroup: entry.subgroup,
      preview: "展示模型；载重、电量、动力与适航参数未由素材验证。" });
  }
  return result;
}

export async function loadSelectedFleetOptions(signal?: AbortSignal): Promise<SelectedFleetAssetOption[]> {
  const response = await fetch("/asset-library/catalog.json", { signal });
  if (!response.ok) throw new Error(`本地素材目录读取失败：HTTP ${response.status}`);
  return selectedFleetOptionsFromCatalog(await response.json() as unknown);
}
