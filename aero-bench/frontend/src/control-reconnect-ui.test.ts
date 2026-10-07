import { describe, expect, it } from "vitest";
import { reconnectUiState, type ReconnectCatalogRow } from "./control-reconnect-ui";

const RUN_A = "run.aaaaaaaa";
const RUN_B = "run.bbbbbbbb";

function rows(...entries: ReconnectCatalogRow[]): ReconnectCatalogRow[] {
  return entries;
}

describe("reconnectUiState", () => {
  it("stays absent without a selection or a catalog", () => {
    const discovered: ReconnectCatalogRow[] = rows({ run_id: RUN_A, start_id: "start.served" });
    expect(reconnectUiState({ catalogRuns: discovered, selectedRunId: null, hasRunCredentials: false })).toBe("absent");
    expect(reconnectUiState({ catalogRuns: null, selectedRunId: RUN_A, hasRunCredentials: false })).toBe("absent");
    expect(reconnectUiState({ catalogRuns: null, selectedRunId: null, hasRunCredentials: false })).toBe("absent");
  });

  it("claims reconnect only for the selected run's non-null authenticated identity", () => {
    const discovered = rows({ run_id: RUN_A, start_id: "start.served" });
    expect(reconnectUiState({ catalogRuns: discovered, selectedRunId: RUN_A, hasRunCredentials: false })).toBe("reconnect");
  });

  it("does not label a mere selected id without a served identity", () => {
    const undiscovered = rows({ run_id: RUN_A, start_id: null });
    expect(reconnectUiState({ catalogRuns: undiscovered, selectedRunId: RUN_A, hasRunCredentials: false })).toBe("absent");
  });

  it("treats a foreign catalog row as absent for an unselected run", () => {
    const foreign = rows({ run_id: RUN_B, start_id: "start.served" });
    expect(reconnectUiState({ catalogRuns: foreign, selectedRunId: RUN_A, hasRunCredentials: false })).toBe("absent");
  });

  it("treats a missing catalog row as absent", () => {
    expect(reconnectUiState({ catalogRuns: rows(), selectedRunId: RUN_A, hasRunCredentials: false })).toBe("absent");
  });

  it("reports started once the session holds run credentials", () => {
    const discovered = rows({ run_id: RUN_A, start_id: "start.served" });
    expect(reconnectUiState({ catalogRuns: discovered, selectedRunId: RUN_A, hasRunCredentials: true })).toBe("started");
    // Credentials win even over a row that no longer serves an identity.
    expect(reconnectUiState({ catalogRuns: rows({ run_id: RUN_A, start_id: null }), selectedRunId: RUN_A, hasRunCredentials: true })).toBe("started");
  });

  it("never echoes the identity into the decision result", () => {
    const state = reconnectUiState({
      catalogRuns: rows({ run_id: RUN_A, start_id: "start.secretish" }),
      selectedRunId: RUN_A,
      hasRunCredentials: false,
    });
    expect(state).toBe("reconnect");
    expect(Object.values(state).join(" ")).not.toContain("start.secretish");
  });
});
