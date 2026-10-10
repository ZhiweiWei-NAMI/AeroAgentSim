import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ControlApiError,
  ControlClient,
  ControlProtocolError,
  RunEventStream,
  SseFrameParser,
  cursorsAfterEnvelope,
  cursorsToQuery,
  decodeFrame,
  INITIAL_CURSORS,
  controlServiceOrigin,
  type SseFrame,
} from "./run-control";
import {
  OTHER_RUN_ID,
  RUN_ID,
  publicRunEventStreamEvent,
  runTransitionStreamEvent,
  scenario,
  sceneStateStreamEvent,
} from "./testing/trace-v3-fixture";

describe("controlServiceOrigin", () => {
  it("accepts bare http(s) origins only", () => {
    expect(controlServiceOrigin("http://127.0.0.1:8123")).toBe("http://127.0.0.1:8123");
    expect(controlServiceOrigin("https://control.example")).toBe("https://control.example");
    expect(controlServiceOrigin("http://localhost:8123/")).toBe("http://localhost:8123");
  });

  it("rejects paths, queries, credentials, and non-HTTP schemes", () => {
    expect(() => controlServiceOrigin("http://127.0.0.1:8123/v1")).toThrow(ControlProtocolError);
    expect(() => controlServiceOrigin("http://127.0.0.1:8123/?x=1")).toThrow(ControlProtocolError);
    expect(() => controlServiceOrigin("http://user:pass@127.0.0.1:8123")).toThrow(ControlProtocolError);
    expect(() => controlServiceOrigin("file:///etc/passwd")).toThrow(ControlProtocolError);
    expect(() => controlServiceOrigin("not a url")).toThrow(ControlProtocolError);
  });
});

describe("SseFrameParser", () => {
  it("splits complete frames and keeps partial tail buffered", () => {
    const parser = new SseFrameParser();
    const first = parser.push('event: run.transition\nid: t1\ndata: {"a":1}\n\n');
    expect(first).toEqual([{ event: "run.transition", id: "t1", data: '{"a":1}' }]);

    const partial = parser.push('event: scene.state\nda');
    expect(partial).toEqual([]);
    const rest = parser.push('ta: {"tick":1}\n\n');
    expect(rest).toEqual([{ event: "scene.state", id: null, data: '{"tick":1}' }]);
  });

  it("ignores heartbeat comments and multi-line data joins with newlines", () => {
    const parser = new SseFrameParser();
    const frames = parser.push(": heartbeat\n\nevent: run.event\ndata: line-one\ndata: line-two\n\n");
    expect(frames).toEqual([{ event: "run.event", id: null, data: "line-one\nline-two" }]);
  });

  it("accepts CRLF separators", () => {
    const parser = new SseFrameParser();
    const frames = parser.push("event: run.transition\r\ndata: {}\r\n\r\n");
    expect(frames).toEqual([{ event: "run.transition", id: null, data: "{}" }]);
  });

  it("rejects unbounded unterminated frames instead of growing forever", () => {
    const parser = new SseFrameParser(64);
    expect(() => parser.push("data: " + "x".repeat(200))).toThrow(ControlProtocolError);
  });

  it("parses only one field value per line and ignores unknown fields", () => {
    const frames: SseFrame[] = new SseFrameParser().push("retry: 100\nevent: run.event\nid: abc\ndata: 1\n\n");
    expect(frames).toEqual([{ event: "run.event", id: "abc", data: "1" }]);
  });
});

describe("cursors", () => {
  it("serialize into the three canonical query parameters", () => {
    expect(cursorsToQuery(INITIAL_CURSORS)).toBe(
      "after_transition=-1&after_scene_tick=0&after_event_sequence=-1",
    );
    expect(cursorsToQuery({ afterTransition: 7, afterSceneTick: 12, afterEventSequence: 34 })).toBe(
      "after_transition=7&after_scene_tick=12&after_event_sequence=34",
    );
  });

  it("rejects cursors outside the server's accepted range", () => {
    expect(() => cursorsToQuery({ afterTransition: -2, afterSceneTick: 0, afterEventSequence: -1 })).toThrow(
      ControlProtocolError,
    );
    expect(() => cursorsToQuery({ afterTransition: -1, afterSceneTick: -1, afterEventSequence: -1 })).toThrow(
      ControlProtocolError,
    );
  });

  it("advance only from the field each envelope declares", () => {
    const transition = decodeFrame(
      { event: "run.transition", id: null, data: JSON.stringify(runTransitionStreamEvent(3, "running")) },
      RUN_ID,
    );
    const scene = decodeFrame(
      { event: "scene.state", id: null, data: JSON.stringify(sceneStateStreamEvent(9)) },
      RUN_ID,
    );
    const event = decodeFrame(
      { event: "run.event", id: null, data: JSON.stringify(publicRunEventStreamEvent(5)) },
      RUN_ID,
    );
    expect(transition).not.toBeNull();
    expect(scene).not.toBeNull();
    expect(event).not.toBeNull();
    let cursors = cursorsAfterEnvelope(INITIAL_CURSORS, transition!);
    expect(cursors.afterTransition).toBe(3);
    cursors = cursorsAfterEnvelope(cursors, scene!);
    expect(cursors.afterSceneTick).toBe(9);
    cursors = cursorsAfterEnvelope(cursors, event!);
    expect(cursors.afterEventSequence).toBe(5);
  });
});

describe("decodeFrame", () => {
  it("decodes the three canonical event names and rejects everything else", () => {
    const transition = decodeFrame(
      { event: "run.transition", id: null, data: JSON.stringify(runTransitionStreamEvent(0, "starting")) },
      RUN_ID,
    );
    expect(transition).not.toBeNull();
    expect(() =>
      decodeFrame({ event: "mystery", id: null, data: "{}" }, RUN_ID),
    ).toThrow(ControlProtocolError);
    expect(() =>
      decodeFrame({ event: "run.event", id: null, data: "not-json" }, RUN_ID),
    ).toThrow(ControlProtocolError);
  });

  it("rejects envelopes whose contract validation fails", () => {
    const broken = sceneStateStreamEvent(1);
    (broken.scene_state as Record<string, unknown>).schema_version = "aero-bench.scene-state/v9";
    expect(() => decodeFrame({ event: "scene.state", id: null, data: JSON.stringify(broken) }, RUN_ID)).toThrow(
      ControlProtocolError,
    );
  });

  it("rejects envelopes that belong to another run", () => {
    expect(() =>
      decodeFrame({ event: "scene.state", id: null, data: JSON.stringify(sceneStateStreamEvent(1)) }, OTHER_RUN_ID),
    ).toThrow(/another run/);
  });
});

// ---------------------------------------------------------------------------
// HTTP client framing
// ---------------------------------------------------------------------------

function jsonResponse(status: number, body: unknown, contentType = "application/json"): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": contentType },
  });
}

const BOOTSTRAP = { operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) };
const RUN_CREDENTIALS = { runId: RUN_ID, operatorToken: "c".repeat(64), csrfToken: "d".repeat(64) };

describe("ControlClient", () => {
  it("sends catalog requests with the bearer token and no query", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: async (input, init) => {
        seen.push({ url: String(input), init: init ?? {} });
        return jsonResponse(200, { schema_version: "aero-bench.control-catalog/v1", suite_id: "fixture.suite", suite_sha256: "1".repeat(64), runs: [] });
      },
    });
    const catalog = await client.catalog(BOOTSTRAP.operatorToken);
    expect(catalog.suite_id).toBe("fixture.suite");
    expect(seen).toHaveLength(1);
    expect(seen[0]!.url).toBe("http://127.0.0.1:8123/v1/catalog");
    expect((seen[0]!.init.headers as Record<string, string>).Authorization).toBe(
      `Bearer ${BOOTSTRAP.operatorToken}`,
    );
  });

  it("validates the start request, sends the bootstrap CSRF header, and validates the response", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: async (input, init) => {
        seen.push({ url: String(input), init: init ?? {} });
        return jsonResponse(202, {
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
          scenario: scenario(),
        });
      },
    });
    const response = await client.startRun(BOOTSTRAP, RUN_ID, "start.1");
    expect(response.credentials.operator_token).toBe("c".repeat(64));
    expect(seen[0]!.init.method).toBe("POST");
    const headers = seen[0]!.init.headers as Record<string, string>;
    expect(headers["X-Aero-Bench-CSRF"]).toBe(BOOTSTRAP.csrfToken);
    expect(headers.Authorization).toBe(`Bearer ${BOOTSTRAP.operatorToken}`);
    expect(JSON.parse(String(seen[0]!.init.body))).toEqual({
      schema_version: "aero-bench.start-run-request/v1",
      start_id: "start.1",
      run_id: RUN_ID,
    });
  });

  it("rejects token-shaped garbage before any request is made", async () => {
    let called = 0;
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: async () => {
        called += 1;
        return jsonResponse(200, {});
      },
    });
    await expect(client.catalog("short")).rejects.toBeInstanceOf(ControlProtocolError);
    expect(called).toBe(0);
  });

  it("surfaces schema-valid error envelopes as typed ControlApiError", async () => {
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: async () =>
        jsonResponse(403, {
          schema_version: "aero-bench.control-error-response/v1",
          error: { code: "csrf.failed", detail: "CSRF validation failed" },
        }),
    });
    const error = await client.status(RUN_CREDENTIALS).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ControlApiError);
    expect((error as ControlApiError).code).toBe("csrf.failed");
    expect((error as ControlApiError).status).toBe(403);
  });

  it("rejects response bodies that violate their contract", async () => {
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: async () => jsonResponse(200, { unexpected: true }),
    });
    await expect(client.catalog(BOOTSTRAP.operatorToken)).rejects.toBeInstanceOf(ControlProtocolError);
  });

  it("sends control mutations with bearer and CSRF headers and validates receipts", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: async (input, init) => {
        seen.push({ url: String(input), init: init ?? {} });
        return jsonResponse(200, {
          schema_version: "aero-bench.runtime-control-response/v1",
          receipt: {
            audit_event_id: null,
            control_id: "control.1",
            operation: "pause",
            schema_version: "aero-bench.runtime-control-receipt/v1",
            status: {
              current: { tick: 3, sim_time_ns: 3_000_000_000 },
              event_chain_root: "d".repeat(64),
              latest_event_id: null,
              phase: "pausing",
              run_id: RUN_ID,
              schema_version: "aero-bench.runtime-control-status/v1",
              step_budget: 0,
              tick_in_progress: false,
            },
          },
          snapshot: {
            schema_version: "aero-bench.run-execution-snapshot/v1",
            run_id: RUN_ID,
            phase: "running",
            transition_sequence: 1,
            failure_classes: [],
            preflight: null,
            runtime_control: null,
            summary: null,
          },
        });
      },
    });
    const response = await client.control(RUN_CREDENTIALS, "pause", "control.1");
    expect(response.receipt.operation).toBe("pause");
    expect(seen[0]!.url).toBe(`http://127.0.0.1:8123/v1/runs/${RUN_ID}/controls/pause`);
    const headers = seen[0]!.init.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${RUN_CREDENTIALS.operatorToken}`);
    expect(headers["X-Aero-Bench-CSRF"]).toBe(RUN_CREDENTIALS.csrfToken);
    expect(JSON.parse(String(seen[0]!.init.body))).toEqual({
      schema_version: "aero-bench.runtime-control-request/v1",
      control_id: "control.1",
    });
  });
});

// ---------------------------------------------------------------------------
// Event stream
// ---------------------------------------------------------------------------

function sseResponse(frames: readonly string[], status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(frame));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { "Content-Type": status === 200 ? "text/event-stream; charset=utf-8" : "application/json" },
  });
}

function errorResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RunEventStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function makeStream(
    fetchMock: ReturnType<typeof vi.fn>,
    handlers: {
      onEnvelope?: (envelope: unknown) => void;
      onClosed?: (reason: "aborted" | "terminal" | "unavailable") => void;
      onError?: (error: unknown) => void;
    },
  ): RunEventStream {
    const client = new ControlClient({
      baseUrl: "http://127.0.0.1:8123",
      fetch: fetchMock as unknown as typeof fetch,
    });
    return new RunEventStream(
      client,
      { runId: RUN_ID, operatorToken: "c".repeat(64), csrfToken: "d".repeat(64) },
      {
        onEnvelope: (envelope) => handlers.onEnvelope?.(envelope),
        onConnected: () => {},
        onError: (error) => handlers.onError?.(error),
        onClosed: (reason) => handlers.onClosed?.(reason),
      },
      { reconnectDelayMs: 1000 },
    );
  }

  it("parses validated frames, advances cursors, and resumes with them after reconnect", async () => {
    const urls: string[] = [];
    let call = 0;
    const envelopes: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input));
      call += 1;
      if (call === 1) {
        return sseResponse([
          `event: scene.state\ndata: ${JSON.stringify(sceneStateStreamEvent(4))}\n\n`,
        ]);
      }
      return sseResponse([": heartbeat\n\n"]);
    });
    const stream = makeStream(fetchMock, { onEnvelope: (envelope) => envelopes.push(envelope) });
    stream.open();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1500);

    expect(urls).toHaveLength(2);
    expect(urls[0]).toContain("after_transition=-1");
    expect(urls[0]).toContain("after_scene_tick=0");
    expect(urls[0]).toContain("after_event_sequence=-1");
    expect(urls[1]).toContain("after_scene_tick=4");
    expect(envelopes).toHaveLength(1);
    stream.close();
  });

  it("stops reconnecting once a terminal transition declares the run finished", async () => {
    let calls = 0;
    const closed: string[] = [];
    const fetchMock = vi.fn(async () => {
      calls += 1;
      return sseResponse([
        `event: run.transition\ndata: ${JSON.stringify(runTransitionStreamEvent(1, "completed"))}\n\n`,
      ]);
    });
    const stream = makeStream(fetchMock, { onClosed: (reason) => closed.push(reason) });
    stream.open();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBe(1);
    expect(closed).toEqual(["terminal"]);
  });

  it("closes as unavailable on authentication failures instead of retrying", async () => {
    let calls = 0;
    const closed: string[] = [];
    const errors: unknown[] = [];
    const fetchMock = vi.fn(async () => {
      calls += 1;
      return errorResponse(401, {
        schema_version: "aero-bench.control-error-response/v1",
        error: { code: "authentication.failed", detail: "Bearer authentication failed" },
      });
    });
    const stream = makeStream(fetchMock, {
      onClosed: (reason) => closed.push(reason),
      onError: (error) => errors.push(error),
    });
    stream.open();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBe(1);
    expect(closed).toEqual(["unavailable"]);
    expect(errors[0]).toBeInstanceOf(ControlApiError);
  });

  it("aborts the in-flight request on close and never emits afterwards", async () => {
    const aborts: AbortSignal[] = [];
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          aborts.push(init!.signal!);
          init!.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const stream = makeStream(fetchMock, {});
    stream.open();
    await vi.advanceTimersByTimeAsync(0);
    stream.close();
    expect(aborts).toHaveLength(1);
    expect(aborts[0]!.aborted).toBe(true);
  });

  it("cancels a failed response body before reconnecting", async () => {
    const encoder = new TextEncoder();
    const cancelled = vi.fn();
    const signals: AbortSignal[] = [];
    let call = 0;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init!.signal!);
      call += 1;
      if (call === 1) {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(
              `event: run.event\ndata: ${JSON.stringify(publicRunEventStreamEvent(1))}\n\n`,
            ));
          },
          cancel: cancelled,
        }), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
      }
      return sseResponse([]);
    });
    const errors: unknown[] = [];
    const stream = makeStream(fetchMock, {
      onEnvelope: () => { throw new Error("handler rejected the envelope"); },
      onError: (error) => errors.push(error),
    });

    stream.open();
    await vi.advanceTimersByTimeAsync(0);
    expect(errors).toHaveLength(1);
    expect(signals[0]!.aborted).toBe(true);
    expect(cancelled).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_100);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    stream.close();
  });

  it("sends the run bearer token and event-stream accept header", async () => {
    const inits: RequestInit[] = [];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      inits.push(init ?? {});
      return sseResponse([]);
    });
    const stream = makeStream(fetchMock, {});
    stream.open();
    await vi.advanceTimersByTimeAsync(0);
    stream.close();
    expect(inits).toHaveLength(1);
    const headers = inits[0]!.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer " + "c".repeat(64));
    expect(headers.Accept).toBe("text/event-stream");
  });
});
