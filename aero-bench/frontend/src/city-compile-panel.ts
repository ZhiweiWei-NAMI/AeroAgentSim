import {
  compileCityDraft, fetchNativeSceneCatalog,
  type CityCompilationResult, type NativeSceneRegistration,
} from "./city-authoring-api";
import type { CityWorkspaceConfig } from "./city-workspace-config";

export interface CityCompiledSelection {
  readonly compilationId: string;
  readonly registrationId: string;
  readonly runIds: readonly string[];
}

export interface CityCompilePanelHandle {
  /** Aborts any pending request and empties the panel. Idempotent. */
  dispose(): void;
}

function addNote(parent: HTMLElement, text: string): HTMLParagraphElement {
  const note = document.createElement("p");
  note.className = "studio-note";
  note.textContent = text;
  parent.append(note);
  return note;
}

function keyValueRow(parent: HTMLElement, key: string, value: string): void {
  const row = document.createElement("div");
  row.className = "studio-row";
  const label = document.createElement("strong");
  label.textContent = key;
  const text = document.createElement("span");
  text.textContent = value;
  row.append(label, text);
  parent.append(row);
}

function registrationLabel(registration: NativeSceneRegistration): string {
  return `${registration.registration_id} · ${registration.profile_id} · world ${registration.world_id}`;
}

/**
 * Authoring client for the native-scene catalog and draft compilation (I1). This panel only
 * resolves input: it never starts, polls, or replays a run. A `运行` affordance appears only
 * for an actually `compiled` result and only hands the resolved IDs back through `onCompiled`;
 * the caller decides whether and how to start that Run. The explicit public-scenario document
 * at a registration's `scene_url` is backend-rendered input, never a `city-preview` config,
 * and this panel never passes it to `parseCitySceneConfig` (B-011).
 */
export function renderCityCompilePanel(
  root: HTMLElement,
  getDraft: () => CityWorkspaceConfig,
  onCompiled: (selection: CityCompiledSelection) => void,
  onSelectReference?: (registration: NativeSceneRegistration) => Promise<string | null>,
): CityCompilePanelHandle {
  const controller = new AbortController();
  let disposed = false;
  let registrations: readonly NativeSceneRegistration[] = [];
  let selected: NativeSceneRegistration | null = null;
  let busy = false;

  const card = document.createElement("section");
  card.className = "studio-card";
  const heading = document.createElement("h2");
  heading.textContent = "原生场景编译";
  card.append(heading);
  const provenance = document.createElement("span");
  provenance.className = "provenance-chip";
  provenance.dataset.provenance = "unknown";
  provenance.textContent = "注册来源待校验";
  card.append(provenance);
  addNote(card, "编译只解析输入并返回不可变结果；它不执行、不验证，也不会在此面板发起运行。"
    + "编译结果的 executed 和 verified 字段恒为 false。");

  const catalogGrid = document.createElement("div");
  catalogGrid.className = "studio-grid";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "原生场景注册");
  select.disabled = true;
  const selectLabel = document.createElement("label");
  selectLabel.className = "studio-field";
  const selectCaption = document.createElement("span");
  selectCaption.textContent = "原生场景注册";
  selectLabel.append(selectCaption, select);
  catalogGrid.append(selectLabel);
  card.append(catalogGrid);
  const catalogNote = addNote(card, "正在加载原生场景目录……");
  catalogNote.setAttribute("aria-live", "polite");

  const compileButton = document.createElement("button");
  compileButton.type = "button";
  compileButton.className = "studio-button";
  compileButton.textContent = "编译当前草稿";
  compileButton.disabled = true;
  const actions = document.createElement("div");
  actions.className = "studio-row";
  actions.append(compileButton);
  const referenceButton = document.createElement("button");
  referenceButton.type = "button";
  referenceButton.className = "studio-button";
  referenceButton.textContent = "载入所选注册的参考草稿";
  referenceButton.disabled = true;
  if (onSelectReference !== undefined) actions.append(referenceButton);
  card.append(actions);

  const resultSection = document.createElement("div");
  resultSection.className = "studio-grid";
  card.append(resultSection);

  root.replaceChildren(card);

  function populateSelect(): void {
    select.replaceChildren();
    for (const registration of registrations) {
      const option = document.createElement("option");
      option.value = registration.registration_id;
      option.textContent = registrationLabel(registration);
      select.append(option);
    }
    select.disabled = registrations.length === 0;
    selected = registrations.length > 0 ? registrations[0]! : null;
    if (selected !== null) select.value = selected.registration_id;
  }

  function renderBlockers(result: CityCompilationResult): void {
    const panel = document.createElement("div");
    panel.className = "studio-card";
    const title = document.createElement("h3");
    title.textContent = "存在执行阻断";
    panel.append(title);
    addNote(panel, `草稿哈希 ${result.draft_sha256} 相对注册 ${result.registration_id} 未能解析为可执行输入。`
      + `该注册声明的可编辑执行字段：${selected?.editable_execution_fields.join("、") || "无"}。`
      + "其余变更字段需按下列指针修正。");
    for (const blocker of result.blockers) {
      const row = document.createElement("div");
      row.className = "studio-row";
      const pointer = document.createElement("code");
      pointer.textContent = blocker.field;
      const text = document.createElement("span");
      text.textContent = `[${blocker.code}] ${blocker.message}`;
      row.append(pointer, text);
      panel.append(row);
    }
    resultSection.append(panel);
  }

  function renderCompiled(result: CityCompilationResult): void {
    const panel = document.createElement("div");
    panel.className = "studio-card";
    const title = document.createElement("h3");
    title.textContent = "编译完成（尚未执行、尚未验证）";
    panel.append(title);
    keyValueRow(panel, "编译 ID", result.compilation_id);
    keyValueRow(panel, "草稿哈希", result.draft_sha256);
    if (result.suite !== null) keyValueRow(panel, "Suite", `${result.suite.path} · ${result.suite.sha256}`);
    for (const run of result.runs) {
      keyValueRow(panel, "已解析 Run ID", `${run.run_id} · ${run.executor_kind} · ${run.feasible ? "可行" : "不可行"}`);
    }
    addNote(panel, "本面板不发起运行；下方按钮仅把已解析的编译 ID 和 Run ID 交给调用方决定是否运行。");
    const runButton = document.createElement("button");
    runButton.type = "button";
    runButton.className = "studio-button";
    runButton.textContent = "运行";
    runButton.addEventListener("click", () => {
      onCompiled({
        compilationId: result.compilation_id,
        registrationId: result.registration_id,
        runIds: result.runs.map(run => run.run_id),
      });
    });
    panel.append(runButton);
    resultSection.append(panel);
  }

  function renderError(text: string): void {
    const panel = document.createElement("div");
    panel.className = "studio-card";
    addNote(panel, text);
    resultSection.append(panel);
  }

  function updateBusyState(): void {
    provenance.dataset.provenance = selected === null ? "unknown" : "registered";
    provenance.textContent = selected === null ? "无可用注册来源" : "已注册原生来源 · 尚未运行";
    compileButton.disabled = busy || selected === null;
    compileButton.textContent = busy ? "编译中…" : "编译当前草稿";
    select.disabled = busy || registrations.length === 0;
    referenceButton.disabled = busy || selected === null;
  }

  async function loadCatalog(): Promise<void> {
    try {
      const outcome = await fetchNativeSceneCatalog(controller.signal);
      if (disposed) return;
      if (outcome.kind === "error") {
        catalogNote.textContent = `原生场景目录不可用：[${outcome.code}] ${outcome.detail}`;
        return;
      }
      registrations = outcome.catalog.registrations;
      populateSelect();
      catalogNote.textContent = registrations.length === 0
        ? "原生场景目录为空：尚无已发布的注册，草稿暂无法编译。"
        : `已加载 ${registrations.length} 个原生场景注册。选择注册后编译当前草稿。`;
      updateBusyState();
    } catch (error) {
      if (disposed) return;
      catalogNote.textContent = `读取原生场景目录失败：${error instanceof Error ? error.message : String(error)}`;
    }
  }

  select.addEventListener("change", () => {
    selected = registrations.find(registration => registration.registration_id === select.value) ?? null;
    updateBusyState();
  });

  referenceButton.addEventListener("click", () => {
    if (busy || selected === null || onSelectReference === undefined) return;
    busy = true;
    updateBusyState();
    resultSection.replaceChildren();
    void onSelectReference(selected).then(note => {
      if (!disposed && note !== null) addNote(resultSection, note);
    }).catch(error => {
      if (!disposed) renderError(`参考草稿载入失败：${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => { if (!disposed) { busy = false; updateBusyState(); } });
  });

  compileButton.addEventListener("click", () => {
    if (busy || selected === null) return;
    const registration = selected;
    busy = true;
    updateBusyState();
    resultSection.replaceChildren();
    void compileCityDraft({
      registration_id: registration.registration_id,
      registration_sha256: registration.registration_sha256,
      draft: getDraft(),
    }, controller.signal).then(outcome => {
      if (disposed) return;
      if (outcome.kind === "compiled") renderCompiled(outcome.result);
      else if (outcome.kind === "blocked") renderBlockers(outcome.result);
      else renderError(`编译请求被拒绝：[${outcome.code}] ${outcome.detail}`);
    }).catch((error: unknown) => {
      if (disposed) return;
      renderError(`编译请求失败：${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => {
      if (disposed) return;
      busy = false;
      updateBusyState();
    });
  });

  void loadCatalog();

  return {
    dispose(): void {
      if (disposed) return;
      disposed = true;
      controller.abort();
      root.replaceChildren();
    },
  };
}
