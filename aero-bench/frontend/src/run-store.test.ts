import { beforeEach, describe, expect, it, vi } from "vitest";
import { ControlProtocolError, type StreamEnvelope } from "./run-control";
import { RunSession, MAX_EVENTS, MAX_SCENE_STATES } from "./run-store";
import { RunStartIdentityStore } from "./run-start-identity";
import type { ControlCatalog, StartRunResponse } from "./generated/aero-bench-contracts";
import {
  RUN_ID,
  controlCatalog,
  publicRunEventStreamEvent,
  runTransitionStreamEvent,
  scenario,
  sceneStateStreamEvent,
} from "./testing/trace-v3-fixture";

function startResponse(): StartRunResponse {
  return {
    schema_version: "aero-bench.start-run-response/v1",
    run_id: RUN_ID,
    credentials: {
      schema_version: "aero-bench.run-access-credentials/v1",
      run_id: RUN_ID,
      operator_token: "c".repeat(64),
      csrf_token: "d".repeat(64),
    },
    snapshot: {
      schema_version: "aero-bench.run-execution-snapshot/v1",
      run_id: RUN_ID,
      phase: "starting",
      transition_sequence: 0,
      failure_classes: [],
      preflight: null,
      runtime_control: null,
      summary: null,
    },
    scenario: scenario() as unknown as StartRunResponse["scenario"],
  };
}

function fakeClient() {
  return {
    catalog: vi.fn(async (): Promise<ControlCatalog> => controlCatalog() as unknown as ControlCatalog),
    startRun: vi.fn(async (): Promise<StartRunResponse> => startResponse()),
    control: vi.fn(async () => {
      throw new ControlProtocolError("no control in this fixture");
    }),
    status: vi.fn(async () => {
      throw new ControlProtocolError("no status in this fixture");
    }),
  };
}

/** Catalog fixture with the run's served (authoritative) start_id set. */
function servedCatalog(startId: string | null): ControlCatalog {
  const base = controlCatalog();
  const run = base.runs as Record<string, unknown>[];
  return controlCatalog({ runs: [{ ...run[0], start_id: startId }] }) as unknown as ControlCatalog;
}

function sessionWith(client: unknown, identifiers: string[] = []): RunSession {
  let index = 0;
  return new RunSession(client as never, {
    newIdentifier: (prefix) => identifiers[index] ?? `${prefix}.${index++}`,
  });
}

describe("RunSession", () => {
  let session: RunSession;

  beforeEach(() => {
    session = sessionWith(fakeClient(), ["start.1", "control.1"]);
  });

  it("loads the catalog and selects only catalog-declared runs", async () => {
    await session.loadCatalog("a".repeat(64));
    expect(session.currentState.catalog?.runs).toHaveLength(1);
    expect(() => session.selectRun(RUN_ID)).not.toThrow();
    expect(() => session.selectRun("f".repeat(64))).toThrow(/absent from the loaded catalog/);
  });

  it("starts a run with a generated start id and opens with the response snapshot", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    const state = session.currentState;
    expect(state.hasRunCredentials).toBe(true);
    expect(state.snapshot?.phase).toBe("starting");
    expect(state.connection).toBe("connected");
    expect(state.scenario?.world_id).toBe("fixture.world");
  });

  it("reuses the persisted non-secret start id through the supported Start API after reload", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); } };
    const client = fakeClient();
    // The catalog is authoritative: before the run exists it serves no
    // start_id, and after the first Start it acknowledges the saved one.
    let servedStartId: string | null = null;
    client.catalog.mockImplementation(async () => servedCatalog(servedStartId));
    for (const generated of ["start.first", "start.must-not-replace"]) {
      const current = new RunSession(client as never, {
        startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
        newIdentifier: () => generated,
      });
      await current.loadCatalog("a".repeat(64));
      current.selectRun(RUN_ID);
      await current.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
      expect(current.currentState.hasRunCredentials).toBe(true);
      current.dispose();
      servedStartId = "start.first";
    }
    expect(client.startRun).toHaveBeenCalledTimes(2);
    expect(client.startRun.mock.calls.map(args => (args as unknown[])[2])).toEqual(["start.first", "start.first"]);
    const saved = JSON.parse([...values.values()][0]!);
    expect(Object.keys(saved).sort()).toEqual(["schemaVersion", "serviceOrigin", "runId", "startId", "compilationId"].sort());
    expect(saved.runId).toBe(RUN_ID);
  });

  it("does not send Start if persistence fails before the HTTP request", async () => {
    const client = fakeClient();
    const current = new RunSession(client as never, {
      startIdentities: new RunStartIdentityStore({ getItem: () => null,
        setItem: () => { throw new Error("storage is not writable"); } },
        "http://127.0.0.1:8766"),
      newIdentifier: () => "start.first",
    });
    await current.loadCatalog("a".repeat(64));
    current.selectRun(RUN_ID);
    await current.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    expect(client.startRun).not.toHaveBeenCalled();
    expect(current.currentState.sessionError).toBe("storage is not writable");
    expect(current.currentState.hasRunCredentials).toBe(false);
    current.dispose();
  });

  it("imports the catalog-served start id and reuses it for Start after reload", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); } };
    // First service process: the run is created with a locally generated ID.
    const created = new RunSession(fakeClient() as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.created",
    });
    await created.loadCatalog("a".repeat(64));
    created.selectRun(RUN_ID);
    await created.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    created.dispose();
    // Reloaded console against the same service: the authenticated catalog
    // now acknowledges that exact start_id, and Start must reuse it even
    // though a different identifier would be generated.
    const served = servedCatalog("start.created");
    const client = fakeClient();
    client.catalog.mockImplementation(async () => served as unknown as ControlCatalog);
    const reloaded = new RunSession(client as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.must-not-replace",
    });
    await reloaded.loadCatalog("a".repeat(64));
    reloaded.selectRun(RUN_ID);
    await reloaded.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    expect(reloaded.currentState.hasRunCredentials).toBe(true);
    expect(client.startRun.mock.calls.map(args => (args as unknown[])[2])).toEqual(["start.created"]);
    reloaded.dispose();
  });

  it("discovers a start id for a run created by another console without a saved identity", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); } };
    const served = servedCatalog("start.served");
    const client = fakeClient();
    client.catalog.mockImplementation(async () => served as unknown as ControlCatalog);
    const current = new RunSession(client as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.local-only",
    });
    await current.loadCatalog("a".repeat(64));
    expect(current.currentState.discoveredStartId).toBeNull();
    current.selectRun(RUN_ID);
    expect(current.currentState.discoveredStartId).toBe("start.served");
    await current.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    expect(client.startRun.mock.calls.map(args => (args as unknown[])[2])).toEqual(["start.served"]);
    current.dispose();
  });

  it("refuses a catalog with a malformed start identity explicitly and never starts", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); } };
    const served = servedCatalog("start.bad!");
    const client = fakeClient();
    client.catalog.mockImplementation(async () => served as unknown as ControlCatalog);
    const current = new RunSession(client as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.first",
    });
    await current.loadCatalog("a".repeat(64));
    // The refusal surfaces as a catalog error and the catalog is not adopted,
    // so no run can be selected or started from the malformed catalog.
    expect(current.currentState.catalogError).toMatch(/malformed/);
    expect(current.currentState.catalog).toBeNull();
    await expect(current.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) }))
      .rejects.toThrow("no catalog run is selected");
    expect(client.startRun).not.toHaveBeenCalled();
    current.dispose();
  });

  it("preserves the saved start id when the catalog serves none for the run", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); } };
    const stale = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64));
    stale.getOrCreate(RUN_ID, () => "start.stale");
    const client = fakeClient();
    const current = new RunSession(client as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.fresh",
    });
    await current.loadCatalog("a".repeat(64));
    current.selectRun(RUN_ID);
    expect(current.currentState.discoveredStartId).toBeNull();
    await current.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    // The catalog serving null proves nothing (the Start response carrying
    // the identity may have been lost), so the saved pre-request ID is
    // reused idempotently instead of creating a second execution.
    expect(client.startRun.mock.calls.map(args => (args as unknown[])[2])).toEqual(["start.stale"]);
    current.dispose();
  });

  it("replays the pre-request identity after a lost Start response and a null catalog", async () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); } };
    // First console: Start is sent (persisting the identity first), but the
    // HTTP response is lost on the way back, so no run credentials arrive.
    const lostClient = fakeClient();
    lostClient.startRun.mockRejectedValue(new ControlProtocolError("start response lost"));
    const lost = new RunSession(lostClient as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.lost-response",
    });
    await lost.loadCatalog("a".repeat(64));
    lost.selectRun(RUN_ID);
    await lost.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    expect(lost.currentState.hasRunCredentials).toBe(false);
    expect(lostClient.startRun).toHaveBeenCalledTimes(1);
    lost.dispose();
    // Reloaded console: the catalog still serves no identity for the run
    // (null), and the persisted pre-request identity must be preserved and
    // replayed instead of generating a second one.
    const reloadedClient = fakeClient();
    const reloaded = new RunSession(reloadedClient as never, {
      startIdentities: new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "f".repeat(64)),
      newIdentifier: () => "start.must-not-replace",
    });
    await reloaded.loadCatalog("a".repeat(64));
    reloaded.selectRun(RUN_ID);
    await reloaded.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    expect(reloaded.currentState.hasRunCredentials).toBe(true);
    expect(reloadedClient.startRun.mock.calls.map(args => (args as unknown[])[2]))
      .toEqual(["start.lost-response"]);
    reloaded.dispose();
  });

  it("ingests scene states only at strictly advancing ticks", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });

    const ingest = (session as unknown as { ingestEnvelope: (envelope: StreamEnvelope) => void }).ingestEnvelope.bind(session);
    ingest(sceneStateStreamEvent(1) as unknown as StreamEnvelope);
    expect(session.currentState.latestTick).toBe(1);

    ingest(sceneStateStreamEvent(1) as unknown as StreamEnvelope);
    expect(session.currentState.latestTick).toBe(1);
    expect(session.currentState.sessionError).toMatch(/did not advance/);

    ingest(sceneStateStreamEvent(2) as unknown as StreamEnvelope);
    expect(session.currentState.latestTick).toBe(2);
    expect(session.currentState.sceneStates.map((state) => state.at.tick)).toEqual([1, 2]);
  });

  it("keeps scene state history bounded with the oldest ticks dropped", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    const ingest = (session as unknown as { ingestEnvelope: (envelope: StreamEnvelope) => void }).ingestEnvelope.bind(session);
    for (let tick = 1; tick <= MAX_SCENE_STATES + 10; tick += 1) {
      ingest(sceneStateStreamEvent(tick) as unknown as StreamEnvelope);
    }
    const ticks = session.currentState.sceneStates.map((state) => state.at.tick);
    expect(ticks).toHaveLength(MAX_SCENE_STATES);
    expect(ticks[0]).toBe(11);
    expect(ticks[ticks.length - 1]).toBe(MAX_SCENE_STATES + 10);
  });

  it("ingests public events in ascending sequence and rejects regressions", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    const ingest = (session as unknown as { ingestEnvelope: (envelope: StreamEnvelope) => void }).ingestEnvelope.bind(session);
    ingest(publicRunEventStreamEvent(1) as unknown as StreamEnvelope);
    ingest(publicRunEventStreamEvent(3) as unknown as StreamEnvelope);
    ingest(publicRunEventStreamEvent(2) as unknown as StreamEnvelope);
    expect(session.currentState.events.map((event) => event.sequence)).toEqual([1, 3]);
    expect(session.currentState.sessionError).toMatch(/did not advance/);
  });

  it("projects the declared SUMO traffic-light payload into the live frame", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    const ingest = (session as unknown as { ingestEnvelope: (envelope: StreamEnvelope) => void }).ingestEnvelope.bind(session);
    const trafficLights = JSON.stringify([
      {
        next_switch_s: 27,
        phase_index: 0,
        program_id: "0",
        signal_id: "A0",
        state: "GGGggrrrrrGGGggrrrrr",
        telemetry_source: "sumo-traci",
      },
    ]);
    ingest(publicRunEventStreamEvent(63, {
      at: { tick: 1, sim_time_ns: 500_000_000 },
      event_type: "public.traffic-light",
      interaction_type: "sumo.traffic_light.v1",
      payload_schema_id: "sumo.traffic_light.v1",
      provider_id: "traffic",
      source: "traffic",
      source_kind: "provider",
      agent_id: null,
      entity_id: null,
      public_payload: [
        { name: "simulation_time_ns", value: 500_000_000, value_type: "int" },
        { name: "snapshot_digest", value: "e".repeat(64), value_type: "str" },
        { name: "traffic_lights_json", value: trafficLights, value_type: "str" },
      ],
    }) as unknown as StreamEnvelope);

    expect(session.currentState.trafficLightFrame).toEqual({
      event_id: "event.0000000000000063",
      sequence: 63,
      at: { tick: 1, sim_time_ns: 500_000_000 },
      provider_id: "traffic",
      snapshot_digest: "e".repeat(64),
      states: [
        {
          next_switch_s: 27,
          phase_index: 0,
          program_id: "0",
          signal_id: "A0",
          state: "GGGggrrrrrGGGggrrrrr",
          telemetry_source: "sumo-traci",
        },
      ],
    });
  });

  it("updates the snapshot and runtime control from validated transitions", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    const ingest = (session as unknown as { ingestEnvelope: (envelope: StreamEnvelope) => void }).ingestEnvelope.bind(session);
    const envelope = runTransitionStreamEvent(1, "running") as unknown as {
      transition: { runtime_control: unknown };
    };
    envelope.transition.runtime_control = {
      audit_event_id: null,
      control_id: "control.1",
      operation: "resume",
      schema_version: "aero-bench.runtime-control-receipt/v1",
      status: {
        current: { tick: 2, sim_time_ns: 2_000_000_000 },
        event_chain_root: "d".repeat(64),
        latest_event_id: null,
        phase: "running",
        run_id: RUN_ID,
        schema_version: "aero-bench.runtime-control-status/v1",
        step_budget: 0,
        tick_in_progress: false,
      },
    };
    ingest(envelope as unknown as StreamEnvelope);
    expect(session.currentState.snapshot?.phase).toBe("running");
    expect(session.currentState.runtimeControl?.current.tick).toBe(2);
    expect(session.currentState.transitions).toHaveLength(1);
  });

  it("drops credentials on disconnect and resets on dispose", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    session.disconnect();
    expect(session.currentState.hasRunCredentials).toBe(false);
    expect(session.currentState.connection).toBe("closed");
    session.dispose();
    expect(session.currentState.snapshot).toBeNull();
  });

  it("keeps a bounded event buffer", async () => {
    await session.loadCatalog("a".repeat(64));
    session.selectRun(RUN_ID);
    await session.start({ operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) });
    const ingest = (session as unknown as { ingestEnvelope: (envelope: StreamEnvelope) => void }).ingestEnvelope.bind(session);
    for (let sequence = 0; sequence < MAX_EVENTS + 25; sequence += 1) {
      const envelope = publicRunEventStreamEvent(sequence);
      (envelope.event as Record<string, unknown>).correlation_id = `fixture.corr.${sequence}`;
      ingest(envelope as unknown as StreamEnvelope);
    }
    const sequences = session.currentState.events.map((event) => event.sequence);
    expect(sequences).toHaveLength(MAX_EVENTS);
    expect(sequences[0]).toBe(25);
  });
});
