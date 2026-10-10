import { beforeEach, describe, expect, it } from "vitest";
import { AgentConsole } from "./agent-console";
import { InteractionTimeline, buildInteractionChains } from "./interaction-timeline";
import { publicRunEvent } from "./testing/trace-v3-fixture";
import { initLanguage, t } from "./i18n";
import type { PublicRunEvent } from "./generated/aero-bench-contracts";

function event(overrides: Record<string, unknown> = {}): PublicRunEvent {
  return publicRunEvent(0, overrides) as unknown as PublicRunEvent;
}

describe("AgentConsole", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    initLanguage();
  });

  it("renders authorized decision summaries with declared payload attributes", () => {
    const console_ = new AgentConsole(document.createElement("div"));
    console_.update({ events: [event()] });
    const text = console_.container.textContent ?? "";
    expect(text).toContain("fixture.agent");
    expect(text).toContain("agent.decision_summary.v1");
    expect(text).toContain("summary = advance to waypoint");
    console_.dispose();
  });

  it("shows an explicit empty state without authorized agent records", () => {
    const console_ = new AgentConsole(document.createElement("div"));
    console_.update({ events: [] });
    expect(console_.container.textContent).toContain(t("agent.none"));
    console_.dispose();
  });

  it("never renders process output even if a malformed record arrives", () => {
    const console_ = new AgentConsole(document.createElement("div"));
    console_.update({
      events: [event({ interaction_type: "process.stdout.v1", agent_id: "fixture.agent" })],
    });
    expect(console_.container.textContent).toContain(t("agent.none"));
    console_.dispose();
  });
});

describe("buildInteractionChains", () => {
  it("groups strictly by the declared correlation id", () => {
    const first = event({ sequence: 0, correlation_id: "fixture.corr.a" });
    const second = event({ sequence: 1, correlation_id: "fixture.corr.a", interaction_type: "mission.event.v1", agent_id: null, source_kind: "provider", source: "fixture.mission" });
    const other = event({ sequence: 2, correlation_id: "fixture.corr.b" });
    const chains = buildInteractionChains([first, second, other]);
    expect(chains).toHaveLength(2);
    expect(chains[0]!.correlationId).toBe("fixture.corr.a");
    expect(chains[0]!.stages.get("agent.decision_summary.v1")).toBeDefined();
    expect(chains[0]!.stages.get("mission.event.v1")).toBeDefined();
    expect(chains[1]!.stages.get("agent.decision_summary.v1")).toBeDefined();
    expect(chains[1]!.stages.get("mission.event.v1")).toBeUndefined();
  });

  it("orders chains by their first declared sequence", () => {
    const later = event({ sequence: 5, correlation_id: "fixture.corr.later" });
    const earlier = event({ sequence: 2, correlation_id: "fixture.corr.earlier" });
    const chains = buildInteractionChains([later, earlier]);
    expect(chains[0]!.correlationId).toBe("fixture.corr.earlier");
  });

  it("skips records outside the public vocabulary", () => {
    const chains = buildInteractionChains([event({ interaction_type: "process.stdout.v1" })]);
    expect(chains).toHaveLength(0);
  });
});

describe("InteractionTimeline", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    initLanguage();
  });

  it("renders declared stages and marks missing stages as gaps", () => {
    const timeline = new InteractionTimeline(document.createElement("div"));
    timeline.update({ events: [event()] });
    const text = timeline.container.textContent ?? "";
    expect(text).toContain(t("stage.decision"));
    const gaps = timeline.container.querySelectorAll(".chain-gap");
    expect(gaps.length).toBeGreaterThan(0);
    timeline.dispose();
  });

  it("shows an explicit empty state without public events", () => {
    const timeline = new InteractionTimeline(document.createElement("div"));
    timeline.update({ events: [] });
    expect(timeline.container.textContent).toContain(t("timeline.none"));
    timeline.dispose();
  });
});
