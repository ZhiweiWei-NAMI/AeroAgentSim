import type { CityWorkspaceConfig } from "./city-workspace-config";

type CityEvent = CityWorkspaceConfig["events"][number];
type ActionRule = CityWorkspaceConfig["actionRules"][number];
type StateKeyframe = CityWorkspaceConfig["stateKeyframes"][number];
type LabelRule = CityWorkspaceConfig["labelRules"][number];
type PendingKeyframe = { id: string; atS: string; entityId: string; x: string; y: string; z: string; label: string };

const EVENT_TYPES: CityEvent["type"][] = [
  "order.created", "weather.changed", "airspace.activated", "charger.outage", "traffic.restricted",
];
const ACTIONS: ActionRule["action"][] = [
  "accept_order", "plan_route", "follow_route", "takeoff", "land", "start_charging", "stop_charging",
];
const EXECUTOR_ROLES: ActionRule["executorRole"][] = ["coordinator", "vehicle"];
const OPERATORS: LabelRule["operator"][] = ["eq", "lt", "gt"];
const selections = new WeakMap<HTMLElement, {
  events: number; actionRules: number; stateKeyframes: number; labelRules: number;
}>();
const pendingKeyframes = new WeakMap<HTMLElement, PendingKeyframe>();

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function textField(parent: HTMLElement, caption: string, value: string, onInput: (value: string) => void,
                   required = false): void {
  const label = element("label", "studio-field");
  label.append(element("span", "", caption));
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  const error = element("span", "studio-note");
  error.setAttribute("role", "alert");
  input.addEventListener("input", () => {
    if (required && input.value.trim() === "") {
      error.textContent = "此字段不能为空";
      return;
    }
    error.textContent = "";
    onInput(input.value);
  });
  label.append(input, error);
  parent.append(label);
}

function numberField(parent: HTMLElement, caption: string, value: number, onInput: (value: number) => void,
                     nonnegative = false): void {
  const label = element("label", "studio-field");
  label.append(element("span", "", caption));
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  if (nonnegative) input.min = "0";
  input.value = String(value);
  const error = element("span", "studio-note");
  error.setAttribute("role", "alert");
  input.addEventListener("input", () => {
    const next = Number(input.value);
    if (input.value.trim() === "" || !Number.isFinite(next) || (nonnegative && next < 0)) {
      error.textContent = nonnegative ? "请输入不小于 0 的有限数字" : "请输入有限数字";
      return;
    }
    error.textContent = "";
    onInput(next);
  });
  label.append(input, error);
  parent.append(label);
}

function selectField<T extends string>(parent: HTMLElement, caption: string, value: T,
                                       options: readonly T[], onChange: (value: T) => void): void {
  const label = element("label", "studio-field");
  label.append(element("span", "", caption));
  const select = document.createElement("select");
  select.setAttribute("aria-label", caption);
  for (const optionValue of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionValue;
    select.append(option);
  }
  select.value = value;
  select.addEventListener("change", () => onChange(select.value as T));
  label.append(select);
  parent.append(label);
}

function hasFiniteJsonNumbers(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(hasFiniteJsonNumbers);
  if (value !== null && typeof value === "object") return Object.values(value).every(hasFiniteJsonNumbers);
  return true;
}

function jsonObjectField(parent: HTMLElement, caption: string, noun: string,
                         value: Record<string, unknown>, onInput: (value: Record<string, unknown>) => void): void {
  const label = element("label", "studio-field");
  label.append(element("span", "", caption));
  const textarea = document.createElement("textarea");
  textarea.value = JSON.stringify(value, null, 2);
  const error = element("span", "studio-note");
  error.setAttribute("role", "alert");
  textarea.addEventListener("input", () => {
    try {
      const parsed: unknown = JSON.parse(textarea.value);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        error.textContent = `${noun}必须是 JSON 对象`;
        return;
      }
      if (!hasFiniteJsonNumbers(parsed)) {
        error.textContent = "JSON 数字必须有限";
        return;
      }
      error.textContent = "";
      onInput(parsed as Record<string, unknown>);
    } catch {
      error.textContent = "JSON 格式无效";
    }
  });
  label.append(textarea, error);
  parent.append(label);
}

function nextId(prefix: string, items: readonly { id: string }[]): string {
  const used = new Set(items.map(item => item.id));
  let suffix = 1;
  while (used.has(`${prefix}-${suffix}`)) suffix += 1;
  return `${prefix}-${suffix}`;
}

function renderFlow(parent: HTMLElement, config: CityWorkspaceConfig): void {
  const flow = element("div", "studio-card city-event-flow");
  flow.append(element("h3", "", "事件到画面的编排"));
  const stages = element("div", "studio-row city-event-flow-stages");
  stages.setAttribute("role", "group");
  stages.setAttribute("aria-label", "事件到视觉的四层流程");
  const labels = [
    ["事件", `${config.events.length} 条定时事件`],
    ["动作转换", `${config.actionRules.length} 条触发规则`],
    ["状态与标签", `${config.stateKeyframes.length} 个预览关键帧 · ${config.labelRules.length} 条标签规则`],
    ["视觉", "按编排预览呈现"],
  ];
  for (const [index, [title, detail]] of labels.entries()) {
    const stage = element("div", "studio-row");
    stage.append(element("strong", "", `${index + 1}. ${title}`), element("span", "", detail));
    stages.append(stage);
    if (index < labels.length - 1) {
      const arrow = element("span", "studio-note", "→");
      arrow.setAttribute("aria-hidden", "true");
      stages.append(arrow);
    }
  }
  flow.append(stages, element("p", "studio-note",
    "运行标签由 Provider 返回的真实状态生成。手工关键帧只用于编排预览，不是物理证据。"));
  parent.replaceChildren(flow);
}

/** Edit draft choreography only. This panel has no run, artifact, or public-trace write path. */
export function renderCityEventPanel(root: HTMLElement, config: CityWorkspaceConfig,
                                     onChange: (next: CityWorkspaceConfig) => void): void {
  let current = config;
  const selected = selections.get(root) ?? { events: 0, actionRules: 0, stateKeyframes: 0, labelRules: 0 };
  selections.set(root, selected);
  let pendingKeyframe: PendingKeyframe | null = pendingKeyframes.get(root) ?? null;
  let summaryUpdaters: (() => void)[] = [];

  function emit(next: CityWorkspaceConfig): void {
    current = next;
    onChange(next);
    renderFlow(flowHost, current);
    summaryUpdaters.forEach(update => update());
  }

  function patchEvent(index: number, patch: Partial<CityEvent>): void {
    emit({ ...current, events: current.events.map((row, at) => at === index ? { ...row, ...patch } : row) });
  }
  function patchAction(index: number, patch: Partial<ActionRule>): void {
    emit({ ...current, actionRules: current.actionRules.map((row, at) => at === index ? { ...row, ...patch } : row) });
  }
  function patchKeyframe(index: number, patch: Partial<StateKeyframe>): void {
    emit({ ...current, stateKeyframes: current.stateKeyframes.map((row, at) => at === index ? { ...row, ...patch } : row) });
  }
  function patchLabel(index: number, patch: Partial<LabelRule>): void {
    emit({ ...current, labelRules: current.labelRules.map((row, at) => at === index ? { ...row, ...patch } : row) });
  }

  const flowHost = element("div", "");
  const editorHost = element("div", "studio-grid");

  function pendingKeyframeCard(): Node {
    if (pendingKeyframe === null) return document.createDocumentFragment();
    const pending = pendingKeyframe;
    const card = element("section", "studio-card");
    card.append(element("h4", "", "新关键帧"),
      element("p", "studio-note", "位置单位为米；y 表示机体底部高度。关键帧仅用于编排预览。"));
    const fields = element("div", "studio-grid");
    const rawField = (caption: string, key: keyof PendingKeyframe, numeric = false): void => {
      const label = element("label", "studio-field");
      label.append(element("span", "", caption));
      const input = document.createElement("input");
      input.type = numeric ? "number" : "text";
      if (numeric) input.step = "any";
      input.value = pending[key];
      input.addEventListener("input", () => { pending[key] = input.value; });
      label.append(input);
      fields.append(label);
    };
    rawField("新关键帧 ID", "id");
    rawField("新预览时间（秒）", "atS", true);
    rawField("新实体 ID", "entityId");
    rawField("新位置 x（米）", "x", true);
    rawField("新位置 y（机体底部高度，米）", "y", true);
    rawField("新位置 z（米）", "z", true);
    rawField("新预览标签", "label");
    const error = element("p", "studio-note");
    error.setAttribute("role", "alert");
    const controls = element("div", "studio-row");
    const save = element("button", "studio-button", "保存关键帧");
    save.type = "button";
    save.setAttribute("aria-label", "保存关键帧");
    save.addEventListener("click", () => {
      const id = pending.id.trim(), entityId = pending.entityId.trim();
      if (id === "" || current.stateKeyframes.some(item => item.id === id)) {
        error.textContent = "关键帧 ID 不能为空且不能重复";
        return;
      }
      if (!current.fleet.some(item => item.id === entityId)) {
        error.textContent = "实体 ID 必须属于当前机队";
        return;
      }
      const values = [pending.atS, pending.x, pending.y, pending.z];
      const numbers = values.map(value => Number(value));
      if (values.some(value => value.trim() === "") || numbers.some(value => !Number.isFinite(value)) || numbers[0]! < 0) {
        error.textContent = "请填写有效的时间和三个位置坐标，时间不得小于 0";
        return;
      }
      const stateKeyframes = [...current.stateKeyframes, {
        id, atS: numbers[0]!, entityId,
        position: { x: numbers[1]!, y: numbers[2]!, z: numbers[3]! }, label: pending.label,
      }];
      pendingKeyframe = null;
      pendingKeyframes.delete(root);
      selected.stateKeyframes = stateKeyframes.length - 1;
      emit({ ...current, stateKeyframes });
      draw();
    });
    const cancel = element("button", "studio-button", "取消新增关键帧");
    cancel.type = "button";
    cancel.setAttribute("aria-label", "取消新增关键帧");
    cancel.addEventListener("click", () => {
      pendingKeyframe = null;
      pendingKeyframes.delete(root);
      draw();
    });
    controls.append(save, cancel);
    card.append(fields, error, controls);
    return card;
  }

  function section<T extends { id: string }>(title: string, getRows: () => readonly T[], selectedIndex: number,
                                             select: (index: number) => void, add: () => void,
                                             remove: (index: number) => void,
                                             describe: (row: T) => string,
                                             form: (parent: HTMLElement, row: T, index: number) => void): HTMLElement {
    const rows = getRows();
    const card = element("section", "studio-card");
    const heading = element("div", "studio-row");
    heading.append(element("h3", "", title));
    const addButton = element("button", "studio-button", `添加${title}`);
    addButton.type = "button";
    addButton.setAttribute("aria-label", `添加${title}`);
    addButton.addEventListener("click", add);
    heading.append(addButton);
    card.append(heading);
    if (rows.length === 0) {
      card.append(element("p", "studio-note", `暂无${title}。`));
      return card;
    }
    const list = element("div", "studio-row");
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", `${title}列表`);
    const buttons: HTMLButtonElement[] = [];
    rows.forEach((row, index) => {
      const button = element("button", "studio-button", describe(row));
      button.type = "button";
      button.setAttribute("aria-label", `选择${title} ${row.id}`);
      button.setAttribute("aria-pressed", String(index === selectedIndex));
      button.addEventListener("click", () => select(index));
      list.append(button);
      buttons.push(button);
    });
    card.append(list);
    const active = Math.min(selectedIndex, rows.length - 1);
    const fields = element("div", "studio-grid");
    fields.setAttribute("role", "group");
    fields.setAttribute("aria-label", `${title} ${rows[active]!.id} 编辑`);
    form(fields, rows[active]!, active);
    const removeButton = element("button", "studio-button", `删除${title}`);
    removeButton.type = "button";
    removeButton.setAttribute("aria-label", `删除${title} ${rows[active]!.id}`);
    removeButton.addEventListener("click", () => remove(active));
    card.append(fields, removeButton);
    summaryUpdaters.push(() => {
      const latest = getRows();
      buttons.forEach((button, index) => {
        const row = latest[index];
        if (row === undefined) return;
        button.textContent = describe(row);
        button.setAttribute("aria-label", `选择${title} ${row.id}`);
      });
      const activeRow = latest[active];
      if (activeRow !== undefined) {
        fields.setAttribute("aria-label", `${title} ${activeRow.id} 编辑`);
        removeButton.setAttribute("aria-label", `删除${title} ${activeRow.id}`);
      }
    });
    return card;
  }

  function draw(): void {
    summaryUpdaters = [];
    renderFlow(flowHost, current);
    editorHost.replaceChildren(
      section("事件", () => current.events, selected.events,
        index => { selected.events = index; draw(); },
        () => {
          const events = [...current.events, {
            id: nextId("event", current.events), atS: 0, type: "order.created" as const,
            targetId: "", payload: {},
          }];
          selected.events = events.length - 1;
          emit({ ...current, events }); draw();
        },
        index => {
          const events = current.events.filter((_, at) => at !== index);
          selected.events = Math.max(0, Math.min(index, events.length - 1));
          emit({ ...current, events }); draw();
        },
        row => `${row.id} · ${row.atS}s · ${row.type}`,
        (fields, row, index) => {
          textField(fields, "事件 ID", row.id, value => patchEvent(index, { id: value }), true);
          numberField(fields, "发生时间（秒）", row.atS, value => patchEvent(index, { atS: value }), true);
          selectField(fields, "事件类型", row.type, EVENT_TYPES, value => patchEvent(index, { type: value }));
          textField(fields, "目标 ID", row.targetId, value => patchEvent(index, { targetId: value }));
          jsonObjectField(fields, "事件载荷（JSON 对象）", "载荷", row.payload,
            value => patchEvent(index, { payload: value }));
        }),
      section("动作规则", () => current.actionRules, selected.actionRules,
        index => { selected.actionRules = index; draw(); },
        () => {
          const actionRules = [...current.actionRules, {
            id: nextId("action", current.actionRules), eventType: EVENT_TYPES[0]!,
            action: "accept_order" as const, executorRole: "coordinator" as const, arguments: {},
          }];
          selected.actionRules = actionRules.length - 1;
          emit({ ...current, actionRules }); draw();
        },
        index => {
          const actionRules = current.actionRules.filter((_, at) => at !== index);
          selected.actionRules = Math.max(0, Math.min(index, actionRules.length - 1));
          emit({ ...current, actionRules }); draw();
        },
        row => `${row.id} · ${row.eventType} → ${row.action}`,
        (fields, row, index) => {
          textField(fields, "规则 ID", row.id, value => patchAction(index, { id: value }), true);
          textField(fields, "触发事件类型", row.eventType, value => patchAction(index, { eventType: value }), true);
          selectField(fields, "动作", row.action, ACTIONS, value => patchAction(index, { action: value }));
          selectField(fields, "执行角色", row.executorRole, EXECUTOR_ROLES,
            value => patchAction(index, { executorRole: value }));
          jsonObjectField(fields, "动作参数（JSON 对象）", "参数", row.arguments,
            value => patchAction(index, { arguments: value }));
        }),
      section("状态关键帧", () => current.stateKeyframes, selected.stateKeyframes,
        index => { selected.stateKeyframes = index; draw(); },
        () => {
          if (pendingKeyframe !== null) return;
          const previous = current.stateKeyframes[selected.stateKeyframes];
          pendingKeyframe = {
            id: nextId("keyframe", current.stateKeyframes),
            atS: String(previous === undefined ? 0 : previous.atS + 1),
            entityId: previous?.entityId ?? current.fleet[0]?.id ?? "",
            x: previous === undefined ? "" : String(previous.position.x),
            y: previous === undefined ? "" : String(previous.position.y),
            z: previous === undefined ? "" : String(previous.position.z),
            label: "",
          };
          pendingKeyframes.set(root, pendingKeyframe);
          draw();
        },
        index => {
          const stateKeyframes = current.stateKeyframes.filter((_, at) => at !== index);
          selected.stateKeyframes = Math.max(0, Math.min(index, stateKeyframes.length - 1));
          emit({ ...current, stateKeyframes }); draw();
        },
        row => `${row.id} · ${row.atS}s · ${row.entityId}`,
        (fields, row, index) => {
          textField(fields, "关键帧 ID", row.id, value => patchKeyframe(index, { id: value }), true);
          numberField(fields, "预览时间（秒）", row.atS, value => patchKeyframe(index, { atS: value }), true);
          textField(fields, "实体 ID", row.entityId, value => patchKeyframe(index, { entityId: value }), true);
          for (const axis of ["x", "y", "z"] as const) {
            const caption = axis === "y" ? "位置 y（机体底部高度，米）" : `位置 ${axis}（米）`;
            numberField(fields, caption, row.position[axis], value =>
              patchKeyframe(index, { position: { ...current.stateKeyframes[index]!.position, [axis]: value } }));
          }
          textField(fields, "预览标签", row.label, value => patchKeyframe(index, { label: value }));
        }),
      pendingKeyframeCard(),
      section("标签规则", () => current.labelRules, selected.labelRules,
        index => { selected.labelRules = index; draw(); },
        () => {
          const labelRules = [...current.labelRules, {
            id: nextId("label", current.labelRules), field: "status", operator: "eq" as const,
            value: "", label: "状态",
          }];
          selected.labelRules = labelRules.length - 1;
          emit({ ...current, labelRules }); draw();
        },
        index => {
          const labelRules = current.labelRules.filter((_, at) => at !== index);
          selected.labelRules = Math.max(0, Math.min(index, labelRules.length - 1));
          emit({ ...current, labelRules }); draw();
        },
        row => `${row.id} · ${row.field} ${row.operator} ${String(row.value)}`,
        (fields, row, index) => {
          textField(fields, "标签规则 ID", row.id, value => patchLabel(index, { id: value }), true);
          textField(fields, "Provider 状态字段", row.field, value => patchLabel(index, { field: value }), true);
          selectField(fields, "比较", row.operator, OPERATORS, value => patchLabel(index, { operator: value }));
          const valueType = typeof row.value;
          selectField(fields, "值类型", valueType, ["string", "number", "boolean"], value => {
            patchLabel(index, { value: value === "string" ? "" : value === "number" ? 0 : false });
            draw();
          });
          if (valueType === "number") {
            numberField(fields, "比较值", row.value as number, value => patchLabel(index, { value }));
          } else if (valueType === "boolean") {
            selectField(fields, "比较值", String(row.value), ["true", "false"],
              value => patchLabel(index, { value: value === "true" }));
          } else {
            textField(fields, "比较值", row.value as string, value => patchLabel(index, { value }));
          }
          textField(fields, "显示标签", row.label, value => patchLabel(index, { label: value }), true);
        }),
    );
  }

  root.replaceChildren(flowHost, editorHost);
  draw();
}
