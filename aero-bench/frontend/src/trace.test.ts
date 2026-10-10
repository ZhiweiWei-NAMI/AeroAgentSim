import { describe, expect, it } from "vitest";
import {
  PublicTraceStore,
  PublicTraceValidationError,
  sameOriginRelativeEndpoint,
  parsePublicTrace,
  PUBLIC_TRACE_SCHEMA_VERSION,
} from "./trace";
import { OTHER_RUN_ID, publicTrace } from "./testing/trace-v3-fixture";

describe("public trace v3 boundary", () => {
  it("accepts the strict generated public-trace/v3 contract", () => {
    expect(PUBLIC_TRACE_SCHEMA_VERSION).toBe("aero-bench.public-trace/v3");
    const trace = parsePublicTrace(publicTrace());
    expect(trace.schema_version).toBe("aero-bench.public-trace/v3");
    expect(trace.terminal.kind).toBe("completed");
  });

  it("rejects any other schema version and unknown fields", () => {
    expect(() => parsePublicTrace(publicTrace({ schema_version: "aero-bench.public-trace/v2" }))).toThrow(
      PublicTraceValidationError,
    );
    expect(() => parsePublicTrace(publicTrace({ unexpected: true }))).toThrow(PublicTraceValidationError);
    expect(() => parsePublicTrace(null)).toThrow(PublicTraceValidationError);
  });

  it("parses strict JSON text and rejects invalid JSON explicitly", () => {
    const store = new PublicTraceStore();
    const trace = store.setJson(JSON.stringify(publicTrace()));
    expect(trace.run_id).toBe(trace.run_id);
    expect(() => store.setJson("{not json")).toThrow(/invalid JSON/);
  });

});

describe("PublicTraceStore", () => {
  it("emits the accepted document at subscribe time", () => {
    const store = new PublicTraceStore();
    const seen: string[] = [];
    store.subscribe((trace) => seen.push(trace.run_id));
    expect(seen).toHaveLength(0);
    store.set(publicTrace());
    expect(seen).toHaveLength(1);
  });

  it("accepts a same-run snapshot that advances time and rejects regression", () => {
    const store = new PublicTraceStore();
    const first = publicTrace();
    store.set(first);
    const advanced = publicTrace({
      time: { tick: 2, sim_time_ns: 2_000_000_000 },
      terminal: {
        kind: "completed",
        event_id: "fixture.terminal",
        at: { tick: 2, sim_time_ns: 2_000_000_000 },
        failure_class: null,
        provider_failure_ids: [],
      },
    });
    expect(() => store.set(advanced)).not.toThrow();
    expect(() => store.set(first)).toThrow(/cannot move tick backwards/);
  });

  it("replaces the document on an explicit different-run load", () => {
    const store = new PublicTraceStore();
    store.set(publicTrace());
    const other = publicTrace({ run_id: OTHER_RUN_ID });
    store.set(other);
    expect(store.value?.run_id).toBe(OTHER_RUN_ID);
  });

  it("rejects an HTML SPA fallback instead of treating it as a trace", async () => {
    const store = new PublicTraceStore();
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response("<!doctype html>", { status: 200, headers: { "Content-Type": "text/html" } })) as typeof fetch;
    try {
      await expect(store.loadRelative("./replay/public-trace.json")).rejects.toThrow(/HTML instead of JSON/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("binds the exact fetched payload digest before notifying listeners", async () => {
    const store = new PublicTraceStore();
    const payload = new TextEncoder().encode(JSON.stringify(publicTrace())).buffer;
    const digestBuffer = await globalThis.crypto.subtle.digest("SHA-256", payload);
    const expectedDigest = Array.from(
      new Uint8Array(digestBuffer),
      byte => byte.toString(16).padStart(2, "0"),
    ).join("");
    const seen: (string | null)[] = [];
    store.subscribe(() => seen.push(store.sourcePayloadSha256));
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(payload, { status: 200, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    try {
      await store.loadRelative("./public-trace.json");
      expect(store.sourcePayloadSha256).toBe(expectedDigest);
      expect(seen).toEqual([expectedDigest]);
      store.set(publicTrace());
      expect(store.sourcePayloadSha256).toBeNull();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

});

describe("sameOriginRelativeEndpoint", () => {
  const base = "http://127.0.0.1:8000/viewer/index.html";

  it("accepts same-origin relative references", () => {
    expect(sameOriginRelativeEndpoint("public/public-trace.json", base).href).toBe(
      "http://127.0.0.1:8000/viewer/public/public-trace.json",
    );
    expect(sameOriginRelativeEndpoint("./replay/public-trace.json", base).origin).toBe(
      "http://127.0.0.1:8000",
    );
  });

  it("rejects absolute URLs, other origins, protocol-relative, and non-HTTP schemes", () => {
    expect(() =>
      sameOriginRelativeEndpoint("https://other.example/trace.json", base),
    ).toThrow(PublicTraceValidationError);
    expect(() => sameOriginRelativeEndpoint("//other.example/trace.json", base)).toThrow(
      PublicTraceValidationError,
    );
    expect(() => sameOriginRelativeEndpoint("file:///etc/passwd", base)).toThrow(
      PublicTraceValidationError,
    );
    expect(() => sameOriginRelativeEndpoint("C:\\trace.json", base)).toThrow(PublicTraceValidationError);
    expect(() => sameOriginRelativeEndpoint("", base)).toThrow(PublicTraceValidationError);
  });

  it("loads and validates urban-infrastructure-inspection generated trace", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const tracePath = path.resolve(
      __dirname,
      "../../releases/urban-infrastructure-inspection-v1/validation/c585748a96417aa23d1688b832f3148ad7e13b9a667a186c72e4a2123b607ddb/public/public-trace.json",
    );
    if (fs.existsSync(tracePath)) {
      const raw = JSON.parse(fs.readFileSync(tracePath, "utf-8"));
      const parsed = parsePublicTrace(raw);
      expect(parsed.schema_version).toBe("aero-bench.public-trace/v3");
      expect(parsed.scene_states.length).toBe(120);
      expect(parsed.scenario.buildings.length).toBe(18);
      expect(parsed.scenario.semantic_targets.length).toBe(8);
      expect(parsed.verifier_public?.status).toBe("passed");
    }
  });
});
