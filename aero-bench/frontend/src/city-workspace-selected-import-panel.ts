import type { CitySelectedLogistics } from "./city-selected-logistics-draft";
import type { CitySelectedScenario } from "./city-selected-scenario";
import {
  SELECTED_LOGISTICS_IMPORT_DISCLOSURE,
  SELECTED_LOGISTICS_IMPORT_RETAINED_FIELDS,
  prepareSelectedLogisticsOnlyImport,
  type CityWorkspaceLogisticsImportTarget,
  type PreparedSelectedLogisticsImport,
} from "./city-workspace-selected-import";

export interface CityWorkspaceSelectedImportPanelContext {
  readonly target: CityWorkspaceLogisticsImportTarget;
  readonly scenario: CitySelectedScenario | null;
  readonly logistics: CitySelectedLogistics | null;
  readonly scenarioError: string | null;
  readonly logisticsError: string | null;
}

export interface CityWorkspaceSelectedImportPanelHandle {
  dispose(): void;
}

export type CityWorkspaceSelectedImportAction = (
  prepared: PreparedSelectedLogisticsImport,
) => void | string | Promise<void | string>;

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K, className: string, text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function row(parent: HTMLElement, label: string, value: string): void {
  const item = element("div", "studio-row");
  item.append(element("strong", "", label), element("span", "", value));
  parent.append(item);
}

/**
 * Render the retired selected drafts as a read-only source. The only mutation
 * offered here is the explicitly scoped logistics-only import into an existing
 * workspace; selected scene/facility/fleet data never becomes the active draft.
 */
export function renderCityWorkspaceSelectedImportPanel(
  root: HTMLElement,
  context: CityWorkspaceSelectedImportPanelContext,
  onImport: CityWorkspaceSelectedImportAction,
): CityWorkspaceSelectedImportPanelHandle {
  let disposed = false;
  let busy = false;
  const card = element("section", "studio-card");
  card.dataset.role = "selected-import-source";
  const heading = element("h2", "", "选区来源（只读）");
  const provenance = element("span", "provenance-chip", "只读导入来源 · 非活动草稿");
  provenance.dataset.provenance = "registered";
  const disclosure = element("p", "studio-note", SELECTED_LOGISTICS_IMPORT_DISCLOSURE);
  disclosure.dataset.role = "selected-import-disclosure";
  card.append(heading, provenance, disclosure);

  const scenario = context.scenario;
  const logistics = context.logistics;
  if (scenario === null) {
    const missing = element("p", "studio-note",
      context.scenarioError ?? "尚无可读的选区场景来源。不会自动选择其他城市。");
    missing.setAttribute("role", "alert");
    card.append(missing);
  } else {
    row(card, "选区任务", scenario.selectedScene.job_id);
    row(card, "选区摘要", scenario.selectedScene.selection_sha256);
    row(card, "原始来源", scenario.selectedScene.source_sha256);
    row(card, "只读场景内容",
      `${scenario.facilities.length} 处设施 · ${scenario.fleet.length} 个机队条目 · `
      + `${scenario.noFlyZones.length} 处禁飞区（均未导入）`);
    if (context.scenarioError !== null) {
      const invalid = element("p", "studio-note", context.scenarioError);
      invalid.setAttribute("role", "alert");
      card.append(invalid);
    }
  }
  if (logistics === null) {
    const missing = element("p", "studio-note",
      context.logisticsError ?? "尚无与该选区严格绑定的物流来源。");
    missing.setAttribute("role", "alert");
    card.append(missing);
  } else {
    row(card, "可导入物流字段",
      `${logistics.orders.length} 单订单 · ${logistics.performanceProfiles.length} 份性能档案 · `
      + `生成上限 ${logistics.orderGeneration.maxOrders}`);
    if (context.logisticsError !== null) {
      const invalid = element("p", "studio-note", context.logisticsError);
      invalid.setAttribute("role", "alert");
      card.append(invalid);
    }
  }

  const retained = element("p", "studio-note",
    `保留在只读来源：${SELECTED_LOGISTICS_IMPORT_RETAINED_FIELDS.join("、")}。`);
  retained.dataset.role = "selected-import-retained";
  const actions = element("div", "studio-row");
  const button = element("button", "studio-button", "仅导入可表示的物流字段");
  button.type = "button";
  button.disabled = scenario === null || logistics === null
    || context.scenarioError !== null || context.logisticsError !== null;
  const result = element("p", "studio-note");
  result.dataset.role = "selected-import-result";
  result.setAttribute("aria-live", "polite");
  actions.append(button);
  card.append(retained, actions, result);
  root.replaceChildren(card);

  button.addEventListener("click", () => {
    if (disposed || busy || scenario === null || logistics === null
        || context.scenarioError !== null || context.logisticsError !== null) return;
    busy = true;
    button.disabled = true;
    button.textContent = "正在核对只读来源…";
    result.textContent = "";
    void prepareSelectedLogisticsOnlyImport(context.target, scenario, logistics)
      .then(async prepared => {
        if (disposed) return;
        const note = await onImport(prepared);
        if (disposed) return;
        result.textContent = note ?? "物流字段已导入当前工作区；只读选区来源未修改。";
        result.dataset.state = "saved";
      })
      .catch((error: unknown) => {
        if (disposed) return;
        result.textContent = `导入被拒绝：${error instanceof Error ? error.message : String(error)}`;
        result.dataset.state = "error";
      })
      .finally(() => {
        if (disposed) return;
        busy = false;
        button.disabled = false;
        button.textContent = "仅导入可表示的物流字段";
      });
  });

  return {
    dispose(): void {
      if (disposed) return;
      disposed = true;
      root.replaceChildren();
    },
  };
}
