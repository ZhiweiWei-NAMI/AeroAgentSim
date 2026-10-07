import {
  CITY_AUTHORED_LANDSCAPE_KINDS,
  parseCityAuthoredLandscape,
  type CityAuthoredLandscapeItem,
  type CityAuthoredLandscapeKind,
  type CityAuthoredLandscapePoint,
} from "./city-authored-landscape";

import { isTerrainCompletionItem, TERRAIN_COMPLETION_V1,
  type TerrainCompletionStats } from "./city-terrain-completion";

export interface CityLandscapeCompletion {
  propose(kind: CityAuthoredLandscapeKind): { items: readonly CityAuthoredLandscapeItem[];
    stats: TerrainCompletionStats };
}

export type CityLandscapeChangeHandler = (
  items: readonly CityAuthoredLandscapeItem[],
) => void;

const kindLabels: Readonly<Record<CityAuthoredLandscapeKind, string>> = {
  green: "绿地设计",
  plaza: "广场设计",
  planting_strip: "种植带设计",
};

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  if (className !== undefined) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function button(
  label: string,
  action: () => void,
  className = "studio-button",
): HTMLButtonElement {
  const result = element("button", className, label);
  result.type = "button";
  result.addEventListener("click", action);
  return result;
}

function field(label: string, control: HTMLElement): HTMLLabelElement {
  const wrapper = element("label", "studio-field");
  wrapper.append(element("span", "studio-field-label", label), control);
  return wrapper;
}

function formatAreaM2(areaM2: number): string {
  return Number.isInteger(areaM2) ? String(areaM2) : areaM2.toFixed(1);
}

interface LandscapeDraft {
  id: string;
  label: string;
  provenance: "authored";
  kind: CityAuthoredLandscapeKind;
  polygon: CityAuthoredLandscapePoint[];
}

function cloneItem(item: CityAuthoredLandscapeItem): LandscapeDraft {
  return {
    id: item.id,
    label: item.label,
    provenance: "authored",
    kind: item.kind,
    polygon: item.polygon.map((point) => ({ x: point.x, z: point.z })),
  };
}

class CityLandscapePanel {
  private items: CityAuthoredLandscapeItem[];
  private onChange: CityLandscapeChangeHandler;
  private draft: LandscapeDraft | null = null;
  private originalId: string | null = null;
  private error: string | null = null;
  private completionKind: CityAuthoredLandscapeKind | null = null;
  private completionStatus: string | null = null;

  constructor(
    private readonly root: HTMLElement,
    items: readonly CityAuthoredLandscapeItem[],
    onChange: CityLandscapeChangeHandler,
    private completion: CityLandscapeCompletion | null,
  ) {
    this.items = parseCityAuthoredLandscape(items);
    this.onChange = onChange;
    this.render();
  }

  update(
    items: readonly CityAuthoredLandscapeItem[],
    onChange: CityLandscapeChangeHandler,
    completion: CityLandscapeCompletion | null,
  ): void {
    this.items = parseCityAuthoredLandscape(items);
    this.onChange = onChange;
    this.completion = completion;
    if (
      this.originalId !== null &&
      !this.items.some((item) => item.id === this.originalId)
    ) {
      this.draft = null;
      this.originalId = null;
      this.error = null;
    }
    this.render();
  }

  private nextId(): string {
    const existing = new Set(this.items.map((item) => item.id));
    let suffix = 1;
    while (existing.has(`landscape-${suffix}`)) suffix++;
    return `landscape-${suffix}`;
  }

  private startCreate(): void {
    this.originalId = null;
    this.draft = {
      id: this.nextId(),
      label: "",
      provenance: "authored",
      kind: "green",
      polygon: [],
    };
    this.error = null;
    this.render();
  }

  private startEdit(item: CityAuthoredLandscapeItem): void {
    this.originalId = item.id;
    this.draft = cloneItem(item);
    this.error = null;
    this.render();
  }

  private patchDraft(patch: Partial<LandscapeDraft>): void {
    if (this.draft === null)
      throw new Error("City landscape editor has no active draft");
    this.draft = { ...this.draft, ...patch };
    this.error = null;
    this.render();
  }

  private patchVertex(index: number, axis: "x" | "z", value: number): void {
    if (this.draft === null || this.draft.polygon[index] === undefined) {
      throw new Error("City landscape editor vertex is missing");
    }
    const polygon = this.draft.polygon.map((point, pointIndex) =>
      pointIndex === index ? { ...point, [axis]: value } : point,
    );
    this.patchDraft({ polygon });
  }

  private addVertex(): void {
    if (this.draft === null)
      throw new Error("City landscape editor has no active draft");
    const previous = this.draft.polygon.at(-1);
    const point =
      previous === undefined
        ? { x: 0, z: 0 }
        : { x: previous.x + 10, z: previous.z };
    this.patchDraft({ polygon: [...this.draft.polygon, point] });
  }

  private removeVertex(index: number): void {
    if (this.draft === null)
      throw new Error("City landscape editor has no active draft");
    this.patchDraft({
      polygon: this.draft.polygon.filter(
        (_point, pointIndex) => pointIndex !== index,
      ),
    });
  }

  private save(): void {
    if (this.draft === null)
      throw new Error("City landscape editor has no active draft");
    try {
      const saved = parseCityAuthoredLandscape([this.draft])[0]!;
      const candidate =
        this.originalId === null
          ? [...this.items, saved]
          : this.items.map((item) =>
              item.id === this.originalId ? saved : item,
            );
      const next = parseCityAuthoredLandscape(candidate);
      this.items = next;
      this.originalId = saved.id;
      this.draft = cloneItem(saved);
      this.error = null;
      this.onChange(next);
      this.render();
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : String(cause);
      this.render();
    }
  }

  private remove(item: CityAuthoredLandscapeItem): void {
    const next = this.items.filter((candidate) => candidate.id !== item.id);
    this.items = next;
    if (this.originalId === item.id) {
      this.draft = null;
      this.originalId = null;
    }
    this.error = null;
    this.onChange(next);
    this.render();
  }

  private renderItem(item: CityAuthoredLandscapeItem): HTMLElement {
    const row = element("div", "studio-row");
    row.dataset.landscapeId = item.id;
    const description = element(
      "strong",
      undefined,
      `${item.label} · ${kindLabels[item.kind]} · ${item.polygon.length} 个顶点`,
    );
    const actions = element("div", "studio-row");
    const edit = button("编辑", () => this.startEdit(item));
    edit.setAttribute("aria-label", `编辑景观 ${item.id}`);
    const remove = button(
      "删除",
      () => this.remove(item),
      "studio-button studio-danger",
    );
    remove.setAttribute("aria-label", `删除景观 ${item.id}`);
    actions.append(edit, remove);
    row.append(description, actions);
    return row;
  }

  private textInput(
    label: string,
    value: string,
    action: (value: string) => void,
  ): HTMLLabelElement {
    const input = element("input");
    input.type = "text";
    input.value = value;
    input.setAttribute("aria-label", label);
    input.addEventListener("change", () => action(input.value));
    return field(label, input);
  }

  private renderEditor(draft: LandscapeDraft): HTMLElement {
    const editor = element("section", "studio-card");
    editor.dataset.role = "landscape-editor";
    editor.append(
      element(
        "h3",
        undefined,
        this.originalId === null ? "新建创作景观" : `编辑 ${this.originalId}`,
      ),
    );
    editor.append(
      element(
        "p",
        "studio-note",
        "来源：创作设计。此图层不是 OSM、实测物理真值或 Provider 样本。",
      ),
    );
    const grid = element("div", "studio-grid");
    grid.append(
      this.textInput("景观 ID", draft.id, (id) => this.patchDraft({ id })),
      this.textInput("景观标签", draft.label, (label) =>
        this.patchDraft({ label }),
      ),
    );
    const select = element("select");
    select.setAttribute("aria-label", "景观类型");
    for (const kind of CITY_AUTHORED_LANDSCAPE_KINDS) {
      const option = element("option", undefined, kindLabels[kind]);
      option.value = kind;
      select.append(option);
    }
    select.value = draft.kind;
    select.addEventListener("change", () =>
      this.patchDraft({ kind: select.value as CityAuthoredLandscapeKind }),
    );
    grid.append(field("景观类型", select));
    const provenance = element("input");
    provenance.type = "text";
    provenance.value = "authored";
    provenance.readOnly = true;
    provenance.setAttribute("aria-label", "景观来源");
    grid.append(field("景观来源", provenance));
    editor.append(grid, element("h3", undefined, "多边形顶点"));
    for (const [index, point] of draft.polygon.entries()) {
      const row = element("div", "studio-row");
      row.dataset.vertexIndex = String(index);
      const vertexGrid = element("div", "studio-grid");
      for (const [axis, axisLabel] of [
        ["x", "X"],
        ["z", "Z"],
      ] as const) {
        const input = element("input");
        input.type = "number";
        input.step = "any";
        input.value = String(point[axis]);
        const label = `顶点 ${index + 1} ${axisLabel} / m`;
        input.setAttribute("aria-label", label);
        input.addEventListener("change", () =>
          this.patchVertex(index, axis, input.valueAsNumber),
        );
        vertexGrid.append(field(label, input));
      }
      const remove = button(
        "删除顶点",
        () => this.removeVertex(index),
        "studio-button studio-danger",
      );
      remove.setAttribute("aria-label", `删除顶点 ${index + 1}`);
      row.append(vertexGrid, remove);
      editor.append(row);
    }
    if (draft.polygon.length === 0) {
      editor.append(
        element(
          "p",
          "studio-note",
          "尚未添加顶点；保存前至少需要三个不相交的顶点。",
        ),
      );
    }
    const actions = element("div", "studio-row");
    actions.append(
      button("添加顶点", () => this.addVertex()),
      button(
        "保存景观",
        () => this.save(),
        "studio-button studio-button-primary",
      ),
      button("取消", () => {
        this.draft = null;
        this.originalId = null;
        this.error = null;
        this.render();
      }),
    );
    editor.append(actions);
    const error = element("p", "studio-note", this.error ?? "");
    error.setAttribute("role", "alert");
    error.setAttribute("aria-live", "polite");
    editor.append(error);
    return editor;
  }

  private replaceCompletion(
    proposals: readonly CityAuthoredLandscapeItem[],
    stats: TerrainCompletionStats | null = null,
  ): void {
    const next = parseCityAuthoredLandscape([
      ...this.items.filter(item => !isTerrainCompletionItem(item)), ...proposals,
    ]);
    this.items = next;
    this.completionStatus = stats === null ? null : `已生成 ${proposals.length} 项，共 ${formatAreaM2(stats.areaM2)} m²`
      + (stats.truncated ? `（已达上限 ${TERRAIN_COMPLETION_V1.maxItems} 项，按面积保留最大者）` : "");
    if (this.originalId !== null && !next.some(item => item.id === this.originalId)) {
      this.draft = null;
      this.originalId = null;
    }
    this.error = null;
    this.onChange(next);
    this.render();
  }

  private renderCompletion(): HTMLElement {
    const section = element("section", "studio-card");
    section.dataset.role = "terrain-completion";
    section.append(element("h3", undefined, "自动补全（创作层，默认关闭）"),
      element("p", "studio-note", "建议属于创作设计，不是来源数据；可在景观列表中编辑或删除。"));
    const select = element("select");
    select.setAttribute("aria-label", "自动补全类型");
    const placeholder = element("option", undefined, "选择类型");
    placeholder.value = "";
    placeholder.disabled = true;
    select.append(placeholder);
    for (const kind of CITY_AUTHORED_LANDSCAPE_KINDS) {
      const option = element("option", undefined, kindLabels[kind]);
      option.value = kind;
      select.append(option);
    }
    select.value = this.completionKind ?? "";
    const generate = button("生成建议", () => {
      if (this.completionKind === null || this.completion === null) return;
      try {
        const { items, stats } = this.completion.propose(this.completionKind);
        this.replaceCompletion(items, stats);
      }
      catch (cause) {
        this.error = cause instanceof Error ? cause.message : String(cause);
        this.render();
      }
    });
    generate.disabled = this.completionKind === null;
    select.addEventListener("change", () => {
      this.completionKind = CITY_AUTHORED_LANDSCAPE_KINDS.includes(select.value as CityAuthoredLandscapeKind)
        ? select.value as CityAuthoredLandscapeKind : null;
      generate.disabled = this.completionKind === null;
    });
    section.append(field("自动补全类型", select), generate,
      button("移除自动补全", () => this.replaceCompletion([]), "studio-button studio-danger"));
    if (this.completionStatus !== null)
      section.append(element("p", "studio-note completion-status", this.completionStatus));
    const error = element("p", "studio-note", this.error ?? "");
    error.setAttribute("role", "alert");
    section.append(error);
    return section;
  }

  private render(): void {
    const card = element("section", "studio-card");
    card.append(
      element("h2", undefined, "创作景观"),
      element(
        "p",
        "studio-note",
        "单独维护创作绿地、广场和种植带；来源标签始终为 authored，不会写入 OSM 来源文件。",
      ),
    );
    if (this.items.length === 0)
      card.append(element("p", "studio-note", "当前没有创作景观。"));
    else for (const item of this.items) card.append(this.renderItem(item));
    card.append(button("添加创作景观", () => this.startCreate()));
    const children: HTMLElement[] = [card];
    if (this.completion !== null) children.push(this.renderCompletion());
    if (this.draft !== null) children.push(this.renderEditor(this.draft));
    this.root.replaceChildren(...children);
  }
}

const panels = new WeakMap<HTMLElement, CityLandscapePanel>();

/** Render the isolated authoredLandscape[] editor. Workspace-v3 integration supplies the array. */
export function renderCityLandscapePanel(
  root: HTMLElement,
  items: readonly CityAuthoredLandscapeItem[],
  onChange: CityLandscapeChangeHandler,
  completion: CityLandscapeCompletion | null = null,
): void {
  const existing = panels.get(root);
  if (existing === undefined)
    panels.set(root, new CityLandscapePanel(root, items, onChange, completion));
  else existing.update(items, onChange, completion);
}
