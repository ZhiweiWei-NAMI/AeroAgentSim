/** Non-secret idempotency identity; bearer/CSRF values must never enter this store. */
export interface RunStartIdentity {
  schemaVersion: "aero-bench.ui-run-start-identity/v1";
  serviceOrigin: string;
  runId: string;
  startId: string;
  compilationId: string | null;
}

export class RunStartIdentityStore {
  constructor(private readonly storage: Pick<Storage, "getItem" | "setItem">,
      private readonly serviceOrigin: string, private readonly compilationId: string | null = null) {
    if (compilationId !== null && !/^[0-9a-f]{64}$/.test(compilationId)) throw new Error("Invalid compilation identity");
  }

  getOrCreate(runId: string, newId: () => string): RunStartIdentity {
    if (!/^[0-9a-f]{64}$/.test(runId)) throw new Error("Invalid run identity");
    const key = `aero-bench.run-start.v1:${this.serviceOrigin}:${runId}`;
    const raw = this.storage.getItem(key);
    if (raw !== null) {
      const saved = JSON.parse(raw) as RunStartIdentity;
      const keys = ["schemaVersion", "serviceOrigin", "runId", "startId", "compilationId"];
      if (saved === null || typeof saved !== "object" || Object.keys(saved).length !== keys.length
          || Object.keys(saved).some(k => !keys.includes(k))
          || saved.schemaVersion !== "aero-bench.ui-run-start-identity/v1"
          || saved.runId !== runId || saved.serviceOrigin !== this.serviceOrigin
          || saved.compilationId !== this.compilationId || typeof saved.startId !== "string"
          || !/^[a-z0-9]+(?:[._-][a-z0-9]+)*$/.test(saved.startId)) {
        throw new Error("Saved run start identity differs from this service/run/compilation");
      }
      return saved;
    }
    const startId = newId();
    if (!/^[a-z0-9]+(?:[._-][a-z0-9]+)*$/.test(startId)) throw new Error("Invalid start identity");
    const saved: RunStartIdentity = { schemaVersion: "aero-bench.ui-run-start-identity/v1",
      serviceOrigin: this.serviceOrigin, runId, startId, compilationId: this.compilationId };
    // Persist before the HTTP request so loss of its response does not lose the ID.
    this.storage.setItem(key, JSON.stringify(saved));
    return saved;
  }
}
