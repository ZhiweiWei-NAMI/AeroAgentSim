import { describe, expect, it } from "vitest";
import {
  parseWorkspaceTraceCatalog,
  WORKSPACE_TRACE_CATALOG_SCHEMA,
  WorkspaceTraceCatalogError,
} from "./workspace-traces";

describe("workspace trace catalog", () => {
  it("accepts a scanned catalog and keeps unloadable v2 entries", () => {
    const catalog = parseWorkspaceTraceCatalog({
      schema_version: WORKSPACE_TRACE_CATALOG_SCHEMA,
      traces: [
        {
          id: "releases.inspection.public-trace.json",
          relative_path: "releases/inspection-v1/public/public-trace.json",
          url: "/workspace-traces/releases/inspection-v1/public/public-trace.json",
          schema_version: "aero-bench.public-trace/v2",
          run_id: "a".repeat(64),
          suite_id: "inspection.v1.reference",
          case_id: null,
          phase: "verified",
          size_bytes: 12,
          loadable: false,
          blocker: "schema aero-bench.public-trace/v2 is not aero-bench.public-trace/v3",
        },
      ],
    });
    expect(catalog.traces).toHaveLength(1);
    expect(catalog.traces[0]?.loadable).toBe(false);
  });

  it("rejects a catalog with the wrong schema", () => {
    expect(() => parseWorkspaceTraceCatalog({ schema_version: "other", traces: [] })).toThrow(
      WorkspaceTraceCatalogError,
    );
  });
});
