// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderUnboundMissionRecords } from "./p02-mission-records";
import type { RunStageEvent } from "./p02-entity-overlays";
const event: RunStageEvent = {
  tick: 292, timeSeconds: 146, flipTimeSeconds: 146, state: "delivered",
  eventId: "event.0000000000159114", providerId: "network",
  evidence: [{ artifact_id: "artifact.delivery", selector: "0", digest: "c".repeat(64), visibility: "public" }],
};
describe("unbound mission records", () => {
  it("keeps actual inspection report delivery outside business-order rows", () => {
    const section = renderUnboundMissionRecords(document, [event, { ...event, eventId: "event.0000000000159115",
      evidence: [{ ...event.evidence[0]!, selector: "1" }] }], "en", value => String(value));
    expect(section?.getAttribute("aria-label")).toBe("Mission records");
    expect(section?.querySelectorAll(".p02-mission-record-row")).toHaveLength(2);
    expect(section?.querySelectorAll(".p02-order-row")).toHaveLength(0);
    expect(section?.querySelectorAll("button")).toHaveLength(0);
    expect(section?.textContent).toContain("network · delivered");
    expect(section?.textContent).toContain("do not establish logistics delivery");
    expect(section?.textContent).toContain("artifact.delivery [0]");
    expect(section?.textContent).toContain("artifact.delivery [1]");
  });
  it("leaves absent provider records absent and localizes the identity boundary", () => {
    expect(renderUnboundMissionRecords(document, [], "en", String)).toBeNull();
    const section = renderUnboundMissionRecords(document, [event], "zh", String);
    expect(section?.getAttribute("aria-label")).toBe("任务记录");
    expect(section?.textContent).toContain("不表示物流交付");
  });
});
