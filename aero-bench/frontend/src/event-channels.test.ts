import { describe, expect, it } from "vitest";
import {
  ChannelBuffer,
  MAX_LINES_PER_CHANNEL,
  PublicEventRejectedError,
  assertPublicEventSafe,
  channelOfEvent,
  eventLine,
  transitionLine,
} from "./event-channels";
import { publicRunEvent, runTransitionStreamEvent } from "./testing/trace-v3-fixture";
import type { PublicRunEvent, RunExecutionTransition } from "./generated/aero-bench-contracts";

function asEvent(overrides: Record<string, unknown> = {}): PublicRunEvent {
  return publicRunEvent(0, overrides) as unknown as PublicRunEvent;
}

describe("channel routing", () => {
  it("routes events strictly by declared interaction type", () => {
    expect(channelOfEvent(asEvent())).toBe("agent");
    expect(channelOfEvent(asEvent({ interaction_type: "agent.observation.v1" }))).toBe("io");
    expect(channelOfEvent(asEvent({ interaction_type: "agent.tool_call.v1" }))).toBe("io");
    expect(channelOfEvent(asEvent({ interaction_type: "px4.telemetry.v1" }))).toBe("px4");
    expect(channelOfEvent(asEvent({ interaction_type: "mavlink.command_ack.v1" }))).toBe("mavlink");
    expect(channelOfEvent(asEvent({ interaction_type: "ns3.link_state.v1" }))).toBe("ns3");
    expect(channelOfEvent(asEvent({ interaction_type: "verifier.evidence.v1" }))).toBe("verifier");
  });

  it("routes by declared source when no interaction vocabulary matches", () => {
    expect(
      channelOfEvent(
        asEvent({ interaction_type: null, source_kind: "provider", source: "provider.sumo" }),
      ),
    ).toBe("sumo");
    expect(
      channelOfEvent(asEvent({ interaction_type: null, source_kind: "provider", source: "px4.sitl" })),
    ).toBe("px4");
    expect(channelOfEvent(asEvent({ interaction_type: "mission.event.v1" }))).toBe("events");
  });

  it("rejects process output interactions outright", () => {
    expect(() => assertPublicEventSafe(asEvent({ interaction_type: "process.stdout.v1" }))).toThrow(
      PublicEventRejectedError,
    );
    expect(() => assertPublicEventSafe(asEvent({ interaction_type: "process.stderr.v1" }))).toThrow(
      PublicEventRejectedError,
    );
  });

  it("rejects payload attributes outside the closed public vocabulary", () => {
    expect(() =>
      assertPublicEventSafe(
        asEvent({
          public_payload: [{ name: "hidden_reasoning", value: "…", value_type: "str" }],
        }),
      ),
    ).toThrow(PublicEventRejectedError);
    expect(() =>
      assertPublicEventSafe(
        asEvent({
          public_payload: [{ name: "token", value: "x", value_type: "str" }],
        }),
      ),
    ).toThrow(PublicEventRejectedError);
  });
});

describe("line composition", () => {
  it("composes event lines only from declared fields", () => {
    const line = eventLine(asEvent({ observation_id: "fixture.obs" }));
    expect(line.text).toContain("interaction_type=agent.decision_summary.v1".replace("interaction_type=", ""));
    expect(line.text).toContain("agent.decision_summary.v1");
    expect(line.text).toContain("obs=fixture.obs");
    expect(line.text).toContain("payload=");
    expect(line.text).toContain("summary=advance to waypoint");
  });

  it("composes transition lines from the declared transition fields", () => {
    const envelope = runTransitionStreamEvent(4, "paused") as unknown as {
      transition: RunExecutionTransition;
    };
    const line = transitionLine(envelope.transition);
    expect(line.text).toContain("phase=paused");
    expect(line.tone).toBe("muted");
  });
});

describe("ChannelBuffer", () => {
  it("drops duplicate keys and trims to the bound", () => {
    const buffer = new ChannelBuffer();
    const first = eventLine(asEvent());
    buffer.append(first);
    buffer.append(first);
    expect(buffer.size).toBe(1);
    for (let sequence = 0; sequence < MAX_LINES_PER_CHANNEL + 5; sequence += 1) {
      buffer.append(eventLine(asEvent({ sequence, event_id: `event.${String(sequence).padStart(16, "0")}` })));
    }
    expect(buffer.size).toBe(MAX_LINES_PER_CHANNEL);
    expect(buffer.snapshot[0]!.key).toBe("event.0000000000000005");
  });

  it("clears completely", () => {
    const buffer = new ChannelBuffer();
    buffer.append(eventLine(asEvent()));
    buffer.clear();
    expect(buffer.size).toBe(0);
  });
});
