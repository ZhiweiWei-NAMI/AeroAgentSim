import { describe, expect, it } from "vitest";
import { RunStartIdentityStore } from "./run-start-identity";

function memory() {
  const items = new Map<string, string>();
  return { items, getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value); } };
}
const run = "a".repeat(64), compilation = "b".repeat(64);

describe("non-secret run Start identity", () => {
  it("restores the same Start ID after reload, with no credentials in storage", () => {
    const storage = memory();
    const first = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    expect(first.getOrCreate(run, () => "start.first").startId).toBe("start.first");
    const reload = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    expect(reload.getOrCreate(run, () => { throw new Error("must not create a second Start ID"); }).startId).toBe("start.first");
    const value = JSON.parse([...storage.items.values()][0]!);
    expect(Object.keys(value).sort()).toEqual(["schemaVersion", "serviceOrigin", "runId", "startId", "compilationId"].sort());
  });
  it("isolates services and runs, and rejects mismatched compilation or malformed data", () => {
    const storage = memory();
    const a = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    a.getOrCreate(run, () => "start.first");
    expect(new RunStartIdentityStore(storage, "http://127.0.0.1:8768", compilation)
      .getOrCreate(run, () => "start.second").startId).toBe("start.second");
    expect(() => new RunStartIdentityStore(storage, "http://127.0.0.1:8766", "c".repeat(64))
      .getOrCreate(run, () => "start.other")).toThrow("differs");
    const key = [...storage.items.keys()][0]!; storage.setItem(key, '{"unexpected":"data"}');
    expect(() => a.getOrCreate(run, () => "start.third")).toThrow("differs");
  });
  it("surfaces storage failure before starting a run", () => {
    const storage = { getItem: () => null, setItem: () => { throw new Error("storage unavailable"); } };
    expect(() => new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation)
      .getOrCreate(run, () => "start.first")).toThrow("storage unavailable");
  });
  it("imports the authoritative served start id and supersedes a stale saved one", () => {
    const storage = memory();
    const store = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    expect(store.importServedStartId(run, "start.served")).toBe("imported");
    expect(store.getOrCreate(run, () => { throw new Error("must generate when served"); }).startId).toBe("start.served");
    expect(store.importServedStartId(run, "start.served")).toBe("unchanged");
    expect(store.importServedStartId(run, "start.rotated")).toBe("superseded");
    expect(store.get(run)?.startId).toBe("start.rotated");
  });
  it("imports without any saved identity and leaves foreign-scope storage untouched", () => {
    const storage = memory();
    const other = "start.other-scope";
    new RunStartIdentityStore(storage, "http://127.0.0.1:8768", compilation)
      .importServedStartId(run, other);
    const store = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    expect(store.importServedStartId(run, "start.served")).toBe("imported");
    expect(store.get(run)?.startId).toBe("start.served");
    expect(new RunStartIdentityStore(storage, "http://127.0.0.1:8768", compilation).get(run)?.startId).toBe(other);
  });
  it("refuses a malformed served identity explicitly", () => {
    const storage = memory();
    const store = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    expect(() => store.importServedStartId(run, "start.")).toThrow("malformed");
    expect(() => store.importServedStartId(run, "start..double")).toThrow("malformed");
    expect(() => store.importServedStartId(run, "START.UPPER")).toThrow("malformed");
    expect(store.get(run)).toBeNull();
  });
  it("preserves a saved identity when the catalog serves none for the run", () => {
    const storage = memory();
    const store = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    store.getOrCreate(run, () => "start.saved");
    // A null served identity does not disprove that the service manages the
    // run: the Start response may have been lost. The saved pre-request
    // identity stays intact and reusable.
    expect(store.importServedStartId(run, null)).toBe("unchanged");
    expect(store.get(run)?.startId).toBe("start.saved");
    expect(store.getOrCreate(run, () => { throw new Error("must not create a second Start ID"); }).startId).toBe("start.saved");
  });
  it("rejects served identity import for a malformed run identity", () => {
    const storage = memory();
    const store = new RunStartIdentityStore(storage, "http://127.0.0.1:8766", compilation);
    expect(() => store.importServedStartId("not-a-run", "start.served")).toThrow("Invalid run identity");
  });
});
