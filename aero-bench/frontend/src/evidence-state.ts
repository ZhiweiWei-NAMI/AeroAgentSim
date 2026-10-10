import "./evidence-state.css";

/**
 * The four evidence states a scene view can be in. Each state carries the minimum
 * reference that justifies it, so a badge cannot claim more than its inputs establish:
 * - preview: local drafts, authored animation or visual settings; never execution.
 * - blocked: execution was requested or is required but a named gate prevents it.
 * - live: a formal run is streaming; nothing is sealed or verified yet.
 * - verified: a sealed run whose independent Verifier report is identified by digest.
 */
export type EvidenceState =
  | { readonly kind: "preview"; readonly subject: string }
  | { readonly kind: "blocked"; readonly gate: string; readonly reason: string }
  | { readonly kind: "live"; readonly runId: string }
  | { readonly kind: "verified"; readonly runId: string; readonly verifierReportSha256: string };

export const EVIDENCE_STATE_LABELS: Readonly<Record<EvidenceState["kind"], string>> = Object.freeze({
  preview: "预览 · 非正式结果",
  blocked: "执行受阻",
  live: "正式运行中 · 未验证",
  verified: "已验证回放",
});

const SHA256 = /^[0-9a-f]{64}$/;

export function checkEvidenceState(state: EvidenceState): void {
  const text = (value: unknown, name: string): void => {
    if (typeof value !== "string" || value.trim() === "") throw new Error(`Evidence state ${name} is missing`);
  };
  switch (state.kind) {
    case "preview": text(state.subject, "subject"); return;
    case "blocked": text(state.gate, "gate"); text(state.reason, "reason"); return;
    case "live": text(state.runId, "runId"); return;
    case "verified":
      text(state.runId, "runId");
      if (!SHA256.test(state.verifierReportSha256)) throw new Error("Verified state needs a Verifier report SHA-256");
      return;
    default: throw new Error(`Unknown evidence state: ${String((state as { kind?: unknown }).kind)}`);
  }
}

function detailOf(state: EvidenceState): string {
  switch (state.kind) {
    case "preview": return state.subject;
    case "blocked": return `${state.gate}：${state.reason}`;
    case "live": return `运行 ${state.runId.slice(0, 12)}`;
    case "verified": return `运行 ${state.runId.slice(0, 12)} · 验证报告 ${state.verifierReportSha256.slice(0, 12)}`;
  }
}

export interface EvidenceBadge {
  readonly element: HTMLElement;
  set(state: EvidenceState): void;
}

/** A status badge whose colour, glyph and text all encode the state (not colour alone). */
export function createEvidenceBadge(initial: EvidenceState): EvidenceBadge {
  const element = document.createElement("div");
  element.className = "evidence-badge";
  element.setAttribute("role", "status");
  const mark = document.createElement("span"); mark.className = "evidence-badge-mark"; mark.setAttribute("aria-hidden", "true");
  const label = document.createElement("strong"); label.className = "evidence-badge-label";
  const detail = document.createElement("span"); detail.className = "evidence-badge-detail";
  element.append(mark, label, detail);
  const set = (state: EvidenceState): void => {
    checkEvidenceState(state);
    element.dataset.evidenceState = state.kind;
    label.textContent = EVIDENCE_STATE_LABELS[state.kind];
    detail.textContent = detailOf(state);
    element.title = `${label.textContent} — ${detail.textContent}`;
    element.setAttribute("aria-label", element.title);
  };
  set(initial);
  return { element, set };
}
