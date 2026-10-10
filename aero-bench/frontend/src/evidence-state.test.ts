// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { checkEvidenceState, createEvidenceBadge, EVIDENCE_STATE_LABELS } from "./evidence-state";

const DIGEST = "a".repeat(64);

describe("evidence state badge", () => {
  it("renders each state with a distinct label, data state and accessible description", () => {
    const badge = createEvidenceBadge({ kind: "preview", subject: "本地草稿" });
    expect(badge.element.getAttribute("role")).toBe("status");
    expect(badge.element.dataset.evidenceState).toBe("preview");
    expect(badge.element.textContent).toContain(EVIDENCE_STATE_LABELS.preview);
    badge.set({ kind: "blocked", gate: "道路原生几何门", reason: "2 处建筑接触" });
    expect(badge.element.dataset.evidenceState).toBe("blocked");
    expect(badge.element.getAttribute("aria-label")).toContain("道路原生几何门：2 处建筑接触");
    badge.set({ kind: "live", runId: "0123456789abcdef" });
    expect(badge.element.textContent).toContain("运行 0123456789ab");
    badge.set({ kind: "verified", runId: "r".repeat(64), verifierReportSha256: DIGEST });
    expect(badge.element.dataset.evidenceState).toBe("verified");
    expect(new Set(Object.values(EVIDENCE_STATE_LABELS)).size).toBe(4);
  });
  it("refuses a verified state without a Verifier report digest", () => {
    expect(() => checkEvidenceState({ kind: "verified", runId: "run", verifierReportSha256: "pending" }))
      .toThrow(/Verifier report/);
    const badge = createEvidenceBadge({ kind: "preview", subject: "草稿" });
    expect(() => badge.set({ kind: "verified", runId: "run", verifierReportSha256: "" })).toThrow();
    expect(badge.element.dataset.evidenceState).toBe("preview");
  });
  it("requires the reference each state depends on", () => {
    expect(() => checkEvidenceState({ kind: "blocked", gate: "", reason: "x" })).toThrow(/gate/);
    expect(() => checkEvidenceState({ kind: "blocked", gate: "g", reason: " " })).toThrow(/reason/);
    expect(() => checkEvidenceState({ kind: "live", runId: "" })).toThrow(/runId/);
    expect(() => checkEvidenceState({ kind: "preview", subject: "" })).toThrow(/subject/);
    expect(() => checkEvidenceState({ kind: "accepted" } as never)).toThrow(/Unknown/);
  });
});
