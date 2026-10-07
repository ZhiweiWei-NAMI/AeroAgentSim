/**
 * Pure decision for the live console's visible reconnect affordance.
 *
 * The authenticated run catalog (`GET /v1/runs`, operator bootstrap token) is
 * the only source of the non-secret `start_id` this decision accepts: it is
 * the identity the current service acknowledged for the selected run under
 * this compilation. The decision never displays, stores, or invents that
 * identity — the label and explanation only state that an authenticated
 * reconnect identity exists and that the existing idempotent Start reuses it.
 *
 * A discovered identity never means "connected": a run is started only when
 * the session actually holds run credentials from a Start response. A run id
 * alone, a catalog row for a different (foreign/unselected) run, or a missing
 * catalog row all stay a plain Start with no reconnect claim.
 */

/** Structural subset of the generated catalog row this decision reads. */
export interface ReconnectCatalogRow {
  readonly run_id: string;
  readonly start_id?: string | null;
}

export interface ReconnectUiInput {
  /** Rows of the adopted authenticated catalog; null when no catalog is loaded. */
  readonly catalogRuns: readonly ReconnectCatalogRow[] | null;
  /** Currently selected run id; null when nothing is selected. */
  readonly selectedRunId: string | null;
  /** True only once the session holds credentials from an actual Start response. */
  readonly hasRunCredentials: boolean;
}

export type ReconnectUiState = "absent" | "reconnect" | "started";

export function reconnectUiState(input: ReconnectUiInput): ReconnectUiState {
  const runId = input.selectedRunId;
  const runs = input.catalogRuns;
  if (runId === null || runs === null) {
    return "absent";
  }
  // Held run credentials are the actual start status and outrank discovery:
  // the run shows its started state even if a later catalog row stops
  // serving an identity for it.
  if (input.hasRunCredentials) {
    return "started";
  }
  const row = runs.find((candidate) => candidate.run_id === runId);
  if (row === undefined || (row.start_id ?? null) === null) {
    return "absent";
  }
  return "reconnect";
}
