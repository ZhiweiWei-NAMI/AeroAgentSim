import { describe, expect, it } from "vitest";
import {
  displayMeters,
  displayRatio,
  displayValue,
  formatBytes,
  formatMilliseconds,
  formatSimTime,
  integrityForTracePhase,
  livePhaseKey,
  networkFrameAtSceneState,
  recordedTicks,
  runtimePhaseKey,
  sampleAtTick,
  sampleInstruments,
  sceneStateAtTick,
  shortDigest,
  simTimeAtTick,
  statusTone,
  tracePhaseKey,
  trajectoriesFromSceneStates,
} from "./view-model";
import {
  identifier,
  publicTrace,
  sceneState,
  stateSample,
} from "./testing/trace-v3-fixture";
import { parsePublicTrace } from "./trace";

function parsedFixture() {
  return parsePublicTrace(publicTrace());
}

describe("display helpers", () => {
  it("shows the unavailable marker for omitted values", () => {
    expect(displayValue(undefined)).toBe("—");
    expect(displayValue(null)).toBe("—");
    expect(displayRatio(null)).toBe("—");
    expect(displayMeters(undefined)).toBe("—");
  });

  it("does not treat zero or false as missing", () => {
    expect(displayValue(0)).toBe("0");
    expect(displayValue(false)).toBe("false");
    expect(displayMeters(0)).toBe("0.00 m");
  });

  it("formats simulation time deterministically", () => {
    expect(formatSimTime(61_250_000_000)).toBe("01:01.250");
  });

  it("formats bytes and milliseconds deterministically", () => {
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatMilliseconds(12.34)).toBe("12.34 ms");
  });

  it("shortens digests without altering them", () => {
    expect(shortDigest("a".repeat(64))).toBe(`aaaaaaaa…${"a".repeat(8)}`);
  });
});

describe("phase presentation", () => {
  it("maps the live lifecycle phases onto typed i18n keys", () => {
    expect(livePhaseKey("running")).toBe("livePhase.running");
    expect(runtimePhaseKey("paused")).toBe("runPhase.paused");
    expect(tracePhaseKey("verified")).toBe("phase.verified");
  });

  it("tones terminal and healthy states distinctly", () => {
    expect(statusTone("running")).toBe("ok");
    expect(statusTone("completed")).toBe("ok");
    expect(statusTone("aborted")).toBe("bad");
    expect(statusTone("paused")).toBe("warn");
    expect(statusTone("mystery.state")).toBe("muted");
  });

  it("derives sealed-trace integrity from the phase", () => {
    expect(integrityForTracePhase("verified").tone).toBe("ok");
    expect(integrityForTracePhase("sealed").tone).toBe("ok");
    expect(integrityForTracePhase("aborted").tone).toBe("bad");
  });
});

describe("exact-tick lookups", () => {
  it("finds the exact SceneState at a tick and never interpolates", () => {
    const trace = parsedFixture();
    expect(sceneStateAtTick(trace, 1)?.at.tick).toBe(1);
    expect(sceneStateAtTick(trace, 2)).toBeNull();
  });

  it("finds exact entity samples and returns null otherwise", () => {
    const trace = parsedFixture();
    const state = sceneStateAtTick(trace, 1)!;
    expect(sampleAtTick(state, identifier("uav"))?.entity_id).toBe(identifier("uav"));
    expect(sampleAtTick(state, "fixture.missing")).toBeNull();
    expect(sampleAtTick(null, identifier("uav"))).toBeNull();
  });

  it("indexes the authoritative sim time from declared records only", () => {
    const trace = parsedFixture();
    expect(simTimeAtTick(trace, 1)).toBe(1_000_000_000);
    expect(simTimeAtTick(trace, 99)).toBeNull();
  });

  it("projects declared network nodes onto the exact live SceneState", () => {
    const trace = parsedFixture();
    const uav = identifier("uav");
    const scenario = {
      ...trace.scenario,
      network: {
        provider_id: identifier("network"),
        radio_profiles: [{
          radio_profile_id: identifier("radio"),
          provider_id: identifier("network"),
          wifi_standard: "802.11ax",
          frequency_ghz: 5.8,
          channel_width_mhz: 20,
          tx_power_dbm: 20,
          rx_sensitivity_dbm: -90,
        }],
        node_bindings: [{
          node_id: identifier("node"),
          entity_id: uav,
          endpoint_id: identifier("endpoint"),
          radio_profile_id: identifier("radio"),
        }],
        links: [],
      },
    } as unknown as typeof trace.scenario;
    const state = sceneStateAtTick(trace, 1);
    const frame = networkFrameAtSceneState(scenario, state);
    expect(frame?.at.tick).toBe(1);
    expect(frame?.scene_state_digest).toBe(state?.scene_state_digest);
    expect(frame?.nodes[0]?.entity_id).toBe(uav);
    expect(frame?.links).toEqual([]);
  });

  it("does not project an incomplete network binding", () => {
    const trace = parsedFixture();
    const scenario = {
      ...trace.scenario,
      network: {
        provider_id: identifier("network"),
        radio_profiles: [{
          radio_profile_id: identifier("radio"),
          provider_id: identifier("network"),
          wifi_standard: "802.11ax",
          frequency_ghz: 5.8,
          channel_width_mhz: 20,
          tx_power_dbm: 20,
          rx_sensitivity_dbm: -90,
        }],
        node_bindings: [{
          node_id: identifier("node"),
          entity_id: identifier("missing"),
          endpoint_id: identifier("endpoint"),
          radio_profile_id: identifier("radio"),
        }],
        links: [],
      },
    } as unknown as typeof trace.scenario;
    expect(networkFrameAtSceneState(scenario, sceneStateAtTick(trace, 1))).toBeNull();
  });

  it("records ticks from scene states and events only", () => {
    const trace = parsedFixture();
    expect(recordedTicks(trace)).toEqual([1]);
  });

});

describe("sample instruments", () => {
  it("declares only fields the sample carries", () => {
    const sample = stateSample(1, identifier("uav")) as unknown as Parameters<typeof sampleInstruments>[0];
    const instruments = sampleInstruments(sample);
    expect(instruments.find((instrument) => instrument.key === "armed")?.declared).toBe(false);
    expect(instruments.find((instrument) => instrument.key === "battery")?.declared).toBe(false);
  });

  it("reads declared battery, armed and health values verbatim", () => {
    const sample = stateSample(1, identifier("uav"), {
      armed: true,
      battery: { remaining_fraction: 0.75 },
      health: { healthy: false },
    }) as unknown as Parameters<typeof sampleInstruments>[0];
    const instruments = sampleInstruments(sample);
    expect(instruments.find((instrument) => instrument.key === "armed")).toEqual({
      key: "armed",
      declared: true,
      value: true,
    });
    expect(instruments.find((instrument) => instrument.key === "battery")?.value).toBe(0.75);
    expect(instruments.find((instrument) => instrument.key === "health")?.value).toBe(false);
  });

  it("treats a missing sample as fully undeclared", () => {
    for (const instrument of sampleInstruments(null)) {
      expect(instrument.declared).toBe(false);
      expect(instrument.value).toBeNull();
    }
  });
});

describe("scene state helpers", () => {
  it("lists declared entity ids deterministically", () => {
    const state = sceneState(2, [identifier("uav")], [stateSample(2, identifier("uav"))]) as never as ReturnType<typeof parsePublicTrace>["scene_states"][number];
    expect(state.declared_entity_ids).toEqual([identifier("uav")]);
  });
});

describe("trajectoriesFromSceneStates", () => {
  it("copies dynamic samples in tick order and skips static samples", () => {
    const uav = identifier("uav");
    const pad = identifier("pad");
    const states = [
      sceneState(1, [uav, pad], [
        stateSample(1, uav),
        stateSample(1, pad, { sample_kind: "static" }),
      ]),
      sceneState(2, [uav, pad], [
        stateSample(2, uav),
        stateSample(2, pad, { sample_kind: "static" }),
      ]),
    ] as never as Parameters<typeof trajectoriesFromSceneStates>[0];
    const trajectories = trajectoriesFromSceneStates(states);
    expect(trajectories.map((item) => item.entity_id)).toEqual([uav]);
    expect(trajectories[0]?.samples.map((sample) => sample.at.tick)).toEqual([1, 2]);
  });
});
