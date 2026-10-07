/** Non-secret idempotency identity; bearer/CSRF values must never enter this store. */
export interface RunStartIdentity {
  schemaVersion: "aero-bench.ui-run-start-identity/v1";
  serviceOrigin: string;
  runId: string;
  startId: string;
  compilationId: string | null;
}

/** Outcome of importing the start_id served with the authenticated catalog. */
export type RunStartIdentityImport = "unchanged" | "imported" | "superseded";

const IDENTITY_KEYS = ["schemaVersion", "serviceOrigin", "runId", "startId", "compilationId"];
const RUN_ID_PATTERN = /^[0-9a-f]{64}$/;
const START_ID_PATTERN = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const IDENTITY_SCHEMA_VERSION = "aero-bench.ui-run-start-identity/v1";

export class RunStartIdentityStore {
  constructor(private readonly storage: Pick<Storage, "getItem" | "setItem">,
      private readonly serviceOrigin: string, private readonly compilationId: string | null = null) {
    if (compilationId !== null && !/^[0-9a-f]{64}$/.test(compilationId)) throw new Error("Invalid compilation identity");
  }

  private key(runId: string): string {
    return `aero-bench.run-start.v1:${this.serviceOrigin}:${runId}`;
  }

  /** Load and validate the saved identity for this exact service/run/compilation. */
  private saved(runId: string): RunStartIdentity | null {
    const raw = this.storage.getItem(this.key(runId));
    if (raw === null) return null;
    const saved = JSON.parse(raw) as RunStartIdentity;
    if (saved === null || typeof saved !== "object" || Object.keys(saved).length !== IDENTITY_KEYS.length
        || Object.keys(saved).some(k => !IDENTITY_KEYS.includes(k))
        || saved.schemaVersion !== IDENTITY_SCHEMA_VERSION
        || saved.runId !== runId || saved.serviceOrigin !== this.serviceOrigin
        || saved.compilationId !== this.compilationId || typeof saved.startId !== "string"
        || !START_ID_PATTERN.test(saved.startId)) {
      throw new Error("Saved run start identity differs from this service/run/compilation");
    }
    return saved;
  }

  private persist(identity: RunStartIdentity): void {
    this.storage.setItem(this.key(identity.runId), JSON.stringify(identity));
  }

  getOrCreate(runId: string, newId: () => string): RunStartIdentity {
    if (!RUN_ID_PATTERN.test(runId)) throw new Error("Invalid run identity");
    const existing = this.saved(runId);
    if (existing !== null) return existing;
    const startId = newId();
    if (!START_ID_PATTERN.test(startId)) throw new Error("Invalid start identity");
    const saved: RunStartIdentity = { schemaVersion: IDENTITY_SCHEMA_VERSION,
      serviceOrigin: this.serviceOrigin, runId, startId, compilationId: this.compilationId };
    // Persist before the HTTP request so loss of its response does not lose the ID.
    this.persist(saved);
    return saved;
  }

  /** Return the saved identity for this exact service/run/compilation, or null. */
  get(runId: string): RunStartIdentity | null {
    if (!RUN_ID_PATTERN.test(runId)) throw new Error("Invalid run identity");
    return this.saved(runId);
  }

  /**
   * Import the non-secret start_id that the authenticated catalog served for
   * this exact run. The store stays scoped to this service/run/compilation: a
   * malformed served identity or a saved identity from another scope is
   * refused explicitly. A non-null served identity is the authoritative
   * current service identity, so a stale saved ID is superseded (and
   * persisted) rather than silently kept. A null served identity proves
   * nothing about the service state — the Start HTTP response carrying the
   * identity may have been lost after the run was created — so a saved
   * pre-request identity is preserved and stays reusable idempotently.
   * Credentials never pass through here.
   */
  importServedStartId(runId: string, servedStartId: string | null): RunStartIdentityImport {
    if (!RUN_ID_PATTERN.test(runId)) throw new Error("Invalid run identity");
    if (servedStartId === null) {
      return "unchanged";
    }
    if (typeof servedStartId !== "string" || !START_ID_PATTERN.test(servedStartId)) {
      throw new Error("Served start identity is malformed");
    }
    const existing = this.saved(runId);
    if (existing !== null && existing.startId === servedStartId) return "unchanged";
    this.persist({ schemaVersion: IDENTITY_SCHEMA_VERSION,
      serviceOrigin: this.serviceOrigin, runId, startId: servedStartId, compilationId: this.compilationId });
    return existing === null ? "imported" : "superseded";
  }
}
