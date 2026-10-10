import {
  fetchTrafficPreviewJob,
  fetchTrafficPreviewProfiles,
  loadReadyTrafficPreview,
  submitTrafficPreview,
  trafficPreviewDraftMatches,
  type TrafficPreviewProfile,
  type TrafficPreviewSubmission,
  type VerifiedTrafficPreviewArtifact,
} from "./city-traffic-preview-api";
import type { TrafficPreviewJob } from "./generated/aero-bench-contracts";
import type { CityWorkspaceConfig } from "./city-workspace-config";

export interface CityTrafficPreviewPanelHandle {
  /** Re-check the live draft and stop an in-flight preview if it no longer matches. */
  refresh(): void;
  /** Abort catalog/job/asset requests and empty the panel. Idempotent. */
  dispose(): void;
}

export interface CityTrafficPreviewPanelOptions {
  readonly pollIntervalMs?: number;
}

const JOB_LABEL: Readonly<Record<TrafficPreviewJob["state"], string>> = {
  queued: "已排队",
  recording: "SUMO 记录中",
  auditing: "规范审计中",
  ready: "轨迹已发布",
  failed: "生成失败",
};

function note(parent: HTMLElement, text: string): HTMLParagraphElement {
  const element = document.createElement("p");
  element.className = "studio-note";
  element.textContent = text;
  parent.append(element);
  return element;
}

function row(parent: HTMLElement, key: string, value: string): void {
  const element = document.createElement("div");
  element.className = "studio-row";
  const label = document.createElement("strong");
  label.textContent = key;
  const content = document.createElement("span");
  content.textContent = value;
  element.append(label, content);
  parent.append(element);
}

function field(caption: string, control: HTMLElement): HTMLLabelElement {
  const label = document.createElement("label");
  label.className = "studio-field";
  const title = document.createElement("span");
  title.textContent = caption;
  label.append(title, control);
  return label;
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Traffic preview polling was aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    const abort = (): void => {
      window.clearTimeout(timer);
      reject(new DOMException("Traffic preview polling was aborted", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

/**
 * Audited offline SUMO preview authoring UI. It submits an explicit catalog profile,
 * keeps the server-normalized workspace identity pinned through polling, and hands the
 * complete verified trace to the caller. It never labels this engineering artifact as
 * a formal Provider execution or verifier result.
 */
export function renderCityTrafficPreviewPanel(
  root: HTMLElement,
  getDraft: () => CityWorkspaceConfig,
  onReady: (artifact: VerifiedTrafficPreviewArtifact) => void | Promise<void>,
  options: CityTrafficPreviewPanelOptions = {},
): CityTrafficPreviewPanelHandle {
  const pollIntervalMs = options.pollIntervalMs ?? 1_000;
  if (!Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 0) {
    throw new RangeError("Traffic preview poll interval must be a non-negative integer");
  }
  const catalogController = new AbortController();
  let runController: AbortController | null = null;
  let disposed = false;
  let catalogBusy = true;
  let catalogIssue: string | null = null;
  let runBusy = false;
  let generation = 0;
  let profiles: readonly TrafficPreviewProfile[] = [];
  let activeSubmission: TrafficPreviewSubmission | null = null;
  let appliedSubmission: TrafficPreviewSubmission | null = null;

  const card = document.createElement("section");
  card.className = "studio-card traffic-preview-panel";
  const heading = document.createElement("h2");
  heading.textContent = "SUMO 交通预览";
  const provenance = document.createElement("span");
  provenance.className = "provenance-chip";
  provenance.dataset.provenance = "unknown";
  provenance.textContent = "离线工程预览 · 非正式运行";
  card.append(heading, provenance);
  note(card, "该面板生成经规范审计的离线工程轨迹。它不绑定正式 SUMO Provider，"
    + "executed、verified 与 formal_provider_bound 均为 false。渲染器不得删减轨迹后宣称通过验收。");

  const form = document.createElement("div");
  form.className = "studio-grid";
  const profileSelect = document.createElement("select");
  profileSelect.setAttribute("aria-label", "SUMO 预览档案");
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择显式预览档案";
  profileSelect.append(placeholder);
  profileSelect.value = "";
  const durationInput = document.createElement("input");
  durationInput.type = "number";
  durationInput.min = "30";
  durationInput.max = "180";
  durationInput.step = "1";
  durationInput.value = "120";
  durationInput.setAttribute("aria-label", "交通预览时长（秒）");
  form.append(field("预览档案（必须显式选择）", profileSelect), field("记录时长（30–180 秒）", durationInput));
  card.append(form);

  const profileNote = note(card, "正在读取交通预览档案目录……");
  profileNote.setAttribute("aria-live", "polite");
  const actions = document.createElement("div");
  actions.className = "studio-row";
  const generateButton = document.createElement("button");
  generateButton.type = "button";
  generateButton.className = "studio-button";
  generateButton.textContent = "生成审计交通预览";
  actions.append(generateButton);
  card.append(actions);

  const result = document.createElement("section");
  result.className = "studio-card traffic-preview-result";
  result.dataset.state = "idle";
  result.setAttribute("aria-live", "polite");
  result.setAttribute("aria-label", "交通预览状态");
  card.append(result);
  root.replaceChildren(card);

  function selectedProfile(): TrafficPreviewProfile | null {
    return profiles.find(profile => profile.profile_id === profileSelect.value) ?? null;
  }

  function duration(): number | null {
    const value = Number(durationInput.value);
    return Number.isSafeInteger(value) && value >= 30 && value <= 180 ? value : null;
  }

  function profileMatchesDraft(profile: TrafficPreviewProfile | null): boolean {
    if (profile === null) return false;
    try {
      return getDraft().scenePath === profile.scene_path;
    } catch {
      return false;
    }
  }

  function renderResult(state: "idle" | "pending" | "failed" | "stale" | "ready",
                        title: string, lines: readonly [string, string][] = []): void {
    result.dataset.state = state;
    result.replaceChildren();
    const resultHeading = document.createElement("h3");
    resultHeading.textContent = title;
    result.append(resultHeading);
    for (const [key, value] of lines) row(result, key, value);
  }

  function updateControls(): void {
    const selected = selectedProfile();
    const validDuration = duration() !== null;
    const matches = profileMatchesDraft(selected);
    profileSelect.disabled = catalogBusy || runBusy || profiles.length === 0;
    durationInput.disabled = catalogBusy || runBusy;
    generateButton.disabled = catalogBusy || runBusy || selected === null || !validDuration || !matches;
    generateButton.textContent = runBusy ? "生成与审计中…" : "生成审计交通预览";
    if (selected === null) {
      provenance.dataset.provenance = "unknown";
      provenance.textContent = "离线工程预览 · 非正式运行";
    } else if (runBusy) {
      provenance.dataset.provenance = "derived";
      provenance.textContent = "离线 SUMO 生成中 · 非正式运行";
    } else {
      provenance.dataset.provenance = "registered";
      provenance.textContent = "已注册离线档案 · 非正式运行";
    }
  }

  function updateProfileNote(): void {
    const selected = selectedProfile();
    if (catalogBusy) return;
    if (catalogIssue !== null) {
      profileNote.textContent = catalogIssue;
    } else if (profiles.length === 0) {
      profileNote.textContent = "档案目录为空：后端尚未发布可用的离线 SUMO 预览档案。";
    } else if (selected === null) {
      profileNote.textContent = `已加载 ${profiles.length} 个档案。必须显式选择档案；系统不会按场景名称猜测。`;
    } else if (!profileMatchesDraft(selected)) {
      profileNote.textContent = `场景不匹配：档案绑定 ${selected.scene_path}，当前草稿为 ${getDraft().scenePath}。`;
    } else {
      profileNote.textContent = `档案 ${selected.profile_id} · profile ${selected.profile_sha256} · scene ${selected.scene_sha256}`;
    }
  }

  function finishRun(keepSubmission = false): void {
    runBusy = false;
    if (!keepSubmission) activeSubmission = null;
    runController = null;
    updateProfileNote();
    updateControls();
  }

  function markStale(submission: TrafficPreviewSubmission): void {
    const invalidatedApplied = appliedSubmission !== null
      && !trafficPreviewDraftMatches(appliedSubmission.draftSnapshot, getDraft());
    if (invalidatedApplied) appliedSubmission = null;
    generation += 1;
    runController?.abort();
    renderResult("stale", "草稿已变化，预览结果已作废", [
      ["已提交工作区", submission.acceptedJob.workspace_sha256],
      ["处理方式", invalidatedApplied
        ? "已装配轨迹已标记为过时，不再与当前草稿匹配；请重新生成"
        : "未载入、未显示该轨迹；请按当前草稿重新生成"],
    ]);
    finishRun();
  }

  function draftStillCurrent(submission: TrafficPreviewSubmission): boolean {
    return trafficPreviewDraftMatches(submission.draftSnapshot, getDraft());
  }

  function renderJob(job: TrafficPreviewJob): void {
    renderResult("pending", `交通预览：${JOB_LABEL[job.state]}`, [
      ["任务 ID", job.job_id],
      ["档案", `${job.profile_id} · ${job.profile_sha256}`],
      ["工作区", `${job.workspace_sha256} · ${job.workspace_size_bytes} bytes`],
      ["声明", "离线工程预览；未执行正式 Provider，未形成 verifier 结论"],
    ]);
  }

  async function run(submission: TrafficPreviewSubmission, token: number,
                     signal: AbortSignal): Promise<void> {
    let job = submission.acceptedJob;
    while (!disposed && token === generation) {
      if (!draftStillCurrent(submission)) {
        markStale(submission);
        return;
      }
      if (job.state === "failed") {
        renderResult("failed", "交通预览生成失败", [
          ["错误", `[${job.error!.code}] ${job.error!.message}`],
          ["任务 ID", job.job_id],
        ]);
        finishRun();
        return;
      }
      if (job.state === "ready") {
        renderResult("pending", "正在校验完整交通轨迹", [
          ["任务 ID", job.job_id],
          ["工作区", job.workspace_sha256],
          ["校验", "同源 URL、字节数、ETag、SHA-256、PASS 审计身份与需求绑定"],
        ]);
        const artifact = await loadReadyTrafficPreview(submission, job, signal);
        if (disposed || token !== generation) return;
        if (!draftStillCurrent(submission)) {
          markStale(submission);
          return;
        }
        await onReady(artifact);
        if (disposed || token !== generation) return;
        if (!draftStillCurrent(submission)) {
          markStale(submission);
          return;
        }
        appliedSubmission = submission;
        provenance.dataset.provenance = "recorded";
        provenance.textContent = "完整审计轨迹 · 非正式运行";
        renderResult("ready", "离线交通轨迹已完成字节与身份校验", [
          ["任务 ID", job.job_id],
          ["轨迹 SHA-256", job.trace!.sha256],
          ["规范审计", `PASS · ${job.canonical_audit!.sha256}`],
          ["范围", "完整轨迹已交给调用方；显示筛选不构成验收"],
        ]);
        finishRun(true);
        provenance.dataset.provenance = "recorded";
        provenance.textContent = "完整审计轨迹 · 非正式运行";
        return;
      }
      renderJob(job);
      await wait(pollIntervalMs, signal);
      const outcome = await fetchTrafficPreviewJob(submission, signal);
      if (disposed || token !== generation) return;
      if (outcome.kind === "error") {
        renderResult("failed", "交通预览任务查询失败", [
          ["错误", `HTTP ${outcome.status} · [${outcome.code}] ${outcome.detail}`],
          ["任务 ID", job.job_id],
        ]);
        finishRun();
        return;
      }
      job = outcome.job;
    }
  }

  async function loadCatalog(): Promise<void> {
    try {
      const outcome = await fetchTrafficPreviewProfiles(catalogController.signal);
      if (disposed) return;
      if (outcome.kind === "error") {
        catalogIssue = `交通预览目录不可用：[${outcome.code}] ${outcome.detail}`;
        renderResult("failed", "交通预览目录不可用", [
          ["错误", `HTTP ${outcome.status} · [${outcome.code}] ${outcome.detail}`],
        ]);
        return;
      }
      profiles = outcome.catalog.profiles;
      for (const profile of profiles) {
        const option = document.createElement("option");
        option.value = profile.profile_id;
        option.textContent = `${profile.profile_id} · ${profile.scene_path}`;
        profileSelect.append(option);
      }
    } catch (error) {
      if (!disposed) {
        catalogIssue = `读取交通预览目录失败：${error instanceof Error ? error.message : String(error)}`;
        renderResult("failed", "读取交通预览目录失败", [["错误", catalogIssue]]);
      }
    } finally {
      if (!disposed) {
        catalogBusy = false;
        updateProfileNote();
        updateControls();
      }
    }
  }

  profileSelect.addEventListener("change", () => {
    if (!runBusy) {
      activeSubmission = null;
      renderResult("idle", "尚未生成交通预览");
    }
    updateProfileNote();
    updateControls();
  });
  durationInput.addEventListener("input", () => {
    if (!runBusy) activeSubmission = null;
    updateControls();
  });
  generateButton.addEventListener("click", () => {
    const profile = selectedProfile();
    const seconds = duration();
    if (disposed || runBusy || profile === null || seconds === null || !profileMatchesDraft(profile)) return;
    generation += 1;
    const token = generation;
    runController = new AbortController();
    runBusy = true;
    activeSubmission = null;
    updateControls();
    renderResult("pending", "正在提交不可变草稿快照", [
      ["档案", `${profile.profile_id} · ${profile.profile_sha256}`],
      ["场景", `${profile.scene_path} · ${profile.scene_sha256}`],
    ]);
    void submitTrafficPreview(profile, seconds, getDraft(), runController.signal).then(outcome => {
      if (disposed || token !== generation) return;
      if (outcome.kind === "error") {
        renderResult("failed", "交通预览请求被拒绝", [
          ["错误", `HTTP ${outcome.status} · [${outcome.code}] ${outcome.detail}`],
        ]);
        finishRun();
        return;
      }
      activeSubmission = outcome.submission;
      if (!draftStillCurrent(outcome.submission)) {
        markStale(outcome.submission);
        return;
      }
      void run(outcome.submission, token, runController!.signal).catch(error => {
        if (disposed || token !== generation || (error instanceof DOMException && error.name === "AbortError")) return;
        renderResult("failed", "交通预览处理失败", [
          ["错误", error instanceof Error ? error.message : String(error)],
        ]);
        finishRun();
      });
    }).catch(error => {
      if (disposed || token !== generation || (error instanceof DOMException && error.name === "AbortError")) return;
      renderResult("failed", "交通预览提交失败", [
        ["错误", error instanceof Error ? error.message : String(error)],
      ]);
      finishRun();
    });
  });

  renderResult("idle", "尚未生成交通预览");
  updateControls();
  void loadCatalog();

  return {
    refresh(): void {
      const tracked = activeSubmission ?? appliedSubmission;
      if (disposed || tracked === null || draftStillCurrent(tracked)) {
        updateProfileNote();
        updateControls();
        return;
      }
      markStale(tracked);
    },
    dispose(): void {
      if (disposed) return;
      disposed = true;
      generation += 1;
      catalogController.abort();
      runController?.abort();
      root.replaceChildren();
    },
  };
}
