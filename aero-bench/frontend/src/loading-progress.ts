import { currentLanguage } from "./i18n";
import { element } from "./ui";

export type LoadStage = "request" | "download" | "hash" | "parse" | "index" | "scene" | "render" | "ready" | "failed";
export interface LoadProgress {
  readonly stage: LoadStage;
  readonly completed?: number;
  readonly total?: number;
  readonly detail?: string;
}
export type ProgressListener = (progress: LoadProgress) => void;
const labels: Record<LoadStage, readonly [string, string]> = {
  request: ["正在请求公开轨迹", "Requesting public trace"],
  download: ["正在下载轨迹", "Downloading trace"],
  hash: ["正在检查轨迹摘要", "Checking trace digest"],
  parse: ["正在解析轨迹与数据契约", "Parsing trace and data contracts"],
  index: ["正在检查封存记录与回放分片", "Checking sealed history and replay shards"],
  scene: ["正在加载城市场景与纹理", "Loading city geometry and textures"],
  render: ["正在准备场景画面", "Preparing scene rendering"],
  ready: ["轨迹与城市场景已就绪", "Trace and city scene ready"],
  failed: ["加载失败", "Loading failed"],
};

/** Percentages describe only the current measured stage, never total elapsed work. */
export class LoadingProgressView {
  readonly root = element("div", "loading-progress");
  private readonly label = element("strong");
  private readonly detail = element("span");
  private readonly bar = element("progress");
  private value: LoadProgress | null = null;
  constructor() {
    this.root.setAttribute("role", "status");
    this.root.setAttribute("aria-live", "polite");
    this.root.append(this.label, this.detail, this.bar);
    this.clear();
  }
  clear(): void { this.value = null; this.root.hidden = true; }
  update(value: LoadProgress): void { this.value = value; this.refresh(); }
  refresh(): void {
    const value = this.value;
    if (value === null) return;
    this.root.hidden = false;
    this.root.dataset.stage = value.stage;
    const label = labels[value.stage][currentLanguage() === "zh" ? 0 : 1];
    this.label.textContent = label;
    this.bar.setAttribute("aria-label", label);
    this.bar.hidden = value.stage === "ready" || value.stage === "failed";
    const measured = value.total !== undefined && value.total > 0 && value.completed !== undefined;
    if (measured) { this.bar.max = value.total!; this.bar.value = value.completed!; }
    else this.bar.removeAttribute("value");
    const count = value.completed === undefined ? "" : value.stage === "download"
      ? `${(value.completed / 1048576).toFixed(1)} MiB${value.total === undefined ? "" : ` / ${(value.total / 1048576).toFixed(1)} MiB`}`
      : `${value.completed}${value.total === undefined ? "" : ` / ${value.total}`}`;
    this.detail.textContent = value.detail ?? `${count}${measured ? ` · ${Math.floor(value.completed! / value.total! * 100)}%` : ""}`;
  }
}
