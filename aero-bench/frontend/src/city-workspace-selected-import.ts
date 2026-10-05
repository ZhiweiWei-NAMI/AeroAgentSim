import { verifySelectedSceneDigest } from "./city-selected-draft";
import {
  parseCitySelectedLogistics,
  type FleetPerformanceProfile,
  type LogisticsMode,
  type LogisticsOrderGeneration,
  type LogisticsOrderRequest,
} from "./city-selected-logistics-draft";
import {
  parseCitySelectedScenario,
  type CitySelectedScenario,
  type SelectedScenarioFacility,
} from "./city-selected-scenario";

export interface CityWorkspaceLogisticsImportTarget {
  readonly facilities: readonly {
    readonly id: string;
    readonly kind: SelectedScenarioFacility["kind"];
  }[];
  readonly fleet: readonly { readonly id: string }[];
  readonly deployment: {
    readonly executor: "docker_reference" | "kubernetes_cluster";
    readonly imageRef: string;
  };
}

export interface CityWorkspaceLogisticsImportPatch {
  readonly orders: LogisticsOrderRequest[];
  readonly orderGeneration: LogisticsOrderGeneration;
  readonly performanceProfiles: FleetPerformanceProfile[];
  readonly algorithms: {
    readonly mode: LogisticsMode;
    readonly assignment: "external";
    readonly routing: "external";
    readonly energy: "external";
    readonly parameters: Record<string, string | number | boolean>;
  };
  readonly deployment: CityWorkspaceLogisticsImportTarget["deployment"];
}

export interface PreparedSelectedLogisticsImport {
  readonly patch: CityWorkspaceLogisticsImportPatch;
  readonly source: {
    readonly jobId: string;
    readonly selectionSha256: string;
    readonly sourceSha256: string;
  };
  /** Paths remain in the read-only selected-scenario source. They are not v3 fields. */
  readonly retainedSourceFields: readonly string[];
  /** Must be presented next to the explicit import action and its result. */
  readonly disclosure: string;
}

export const SELECTED_LOGISTICS_IMPORT_RETAINED_FIELDS = [
  "/selectedScene",
  "/facilities",
  "/noFlyZones",
  "/fleet",
  "/demand",
] as const;

export const SELECTED_LOGISTICS_IMPORT_DISCLOSURE =
  "仅导入订单、订单生成、机队性能档案与 external 算法映射。"
  + "selectedScene 身份、设施放置及起降/货运/充电能力、机队 maxPayloadKg 等仍保留在只读来源中，未导入工作区。";

function uniqueIndex<T extends { readonly id: string }>(items: readonly T[], label: string): Map<string, T> {
  const result = new Map<string, T>();
  for (const item of items) {
    if (item.id.length === 0 || result.has(item.id)) {
      throw new Error(`目标工作区${label} ID 为空或重复：${item.id || "?"}`);
    }
    result.set(item.id, item);
  }
  return result;
}

function requireTargetContext(target: CityWorkspaceLogisticsImportTarget,
    scenario: CitySelectedScenario): void {
  const targetFacilities = uniqueIndex(target.facilities, "设施");
  const targetFleet = uniqueIndex(target.fleet, "机队");
  for (const source of scenario.facilities) {
    const destination = targetFacilities.get(source.id);
    if (destination === undefined) {
      throw new Error(`选中来源设施 ${source.id} 在目标工作区中不存在，拒绝重绑`);
    }
    if (destination.kind !== source.kind) {
      throw new Error(`选中来源设施 ${source.id} 类型为 ${source.kind}，但目标为 ${destination.kind}，拒绝重绑`);
    }
  }
  for (const source of scenario.fleet) {
    if (!targetFleet.has(source.id)) {
      throw new Error(`选中来源机队 ${source.id} 在目标工作区中不存在，拒绝重绑`);
    }
  }
}

/**
 * Prepare the only supported selected-draft import: logistics fields into an
 * already established workspace. The selected city itself is never converted
 * to a scenePath or copied into the workspace, because v3 cannot represent its
 * identity and richer facility/fleet capabilities without losing information.
 */
export async function prepareSelectedLogisticsOnlyImport(
  target: CityWorkspaceLogisticsImportTarget,
  selectedScenario: unknown,
  selectedLogistics: unknown,
): Promise<PreparedSelectedLogisticsImport> {
  const scenario = parseCitySelectedScenario(selectedScenario);
  const digestIssues = await verifySelectedSceneDigest(scenario.selectedScene);
  if (digestIssues.length > 0) throw new Error(digestIssues.join("；"));
  const logistics = parseCitySelectedLogistics(selectedLogistics, scenario);
  requireTargetContext(target, scenario);

  const incompatible = [
    logistics.algorithms.assignment === "external" ? null
      : `assignment=${logistics.algorithms.assignment}`,
    logistics.algorithms.routing === "external" ? null
      : `routing=${logistics.algorithms.routing}`,
    logistics.algorithms.charging === "external" ? null
      : `charging=${logistics.algorithms.charging}`,
  ].filter((value): value is string => value !== null);
  if (incompatible.length > 0) {
    throw new Error(`选中物流算法无法映射到工作区：${incompatible.join("、")}；仅允许 external → external`);
  }
  const imageRef = logistics.algorithms.externalImageRef;
  if (imageRef === null) {
    // The selected-logistics parser already requires this for any external
    // algorithm. Retain the guard here so this import cannot weaken that pin.
    throw new Error("external 算法缺少指定摘要的 OCI 镜像引用");
  }

  return {
    patch: {
      orders: logistics.orders.map(order => ({ ...order })),
      orderGeneration: { ...logistics.orderGeneration },
      performanceProfiles: logistics.performanceProfiles.map(profile => ({
        ...profile,
        aircraftBody: { ...profile.aircraftBody },
      })),
      algorithms: {
        mode: logistics.algorithms.mode,
        assignment: "external",
        routing: "external",
        energy: "external",
        parameters: { ...logistics.algorithms.parameters },
      },
      deployment: { ...target.deployment, imageRef },
    },
    source: {
      jobId: scenario.selectedScene.job_id,
      selectionSha256: scenario.selectedScene.selection_sha256,
      sourceSha256: scenario.selectedScene.source_sha256,
    },
    retainedSourceFields: SELECTED_LOGISTICS_IMPORT_RETAINED_FIELDS,
    disclosure: SELECTED_LOGISTICS_IMPORT_DISCLOSURE,
  };
}
