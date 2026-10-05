import { describe, expect, it } from "vitest";
import { parseJsonObjectBytes } from "../shared/json-bytes.mjs";
import { parseStrictJson } from "./strict-json";

const read = (text: string) => parseJsonObjectBytes(new TextEncoder().encode(text), { parseValue: parseStrictJson });

describe("JSON byte reader", () => {
  it("preserves a complete object across individual array-item boundaries", () => {
    const source = String.raw`{"name":"巡检 😀 \\\"","states":[null,true,1.25e-3,"a,b]}",{"nested":[1,{"x":2}]}],"empty":[],"meta":{"a":false}}`;
    expect(read(source)).toEqual(JSON.parse(source));
    expect(read("{}\n")).toEqual({});
  });

  it.each([
    '{"a":1,"a":2}', '{"a":[{"x":1,"x":2}]}', '{"a":[1,]}',
    '{"a":1,}', '{"a":[}', '{"a":"unterminated}', '{"a":01}',
    '{"a":9007199254740993}', '{"a":1e999}', '{} true', '{"a":[\uFEFF1]}',
  ])("rejects malformed or inexact input %s", source => {
    expect(() => read(source)).toThrow();
  });

  it("counts nesting from the document root", () => {
    expect(() => read(`{"a":${"[".repeat(64)}0${"]".repeat(64)}}`)).toThrow(/nesting/);
    expect(() => read(`{"a":${"[".repeat(63)}0${"]".repeat(63)}}`)).not.toThrow();
  });

  it("retains own prototype-named keys without changing the object prototype", () => {
    const value = read('{"__proto__":{"polluted":true}}');
    expect(Object.getPrototypeOf(value)).toBe(Object.prototype);
    expect(Object.hasOwn(value, "__proto__")).toBe(true);
    expect(value["polluted"]).toBeUndefined();
  });

  it("can inspect array items without retaining an entire catalog document", () => {
    const items: unknown[] = [];
    const value = parseJsonObjectBytes(new TextEncoder().encode('{"run_id":"actual","states":[{"tick":1},{"tick":2}]}'), {
      keys: new Set(["run_id"]),
      onArrayItem: (key, item) => { if (key === "states") items.push(item); },
    });
    expect(value).toEqual({ run_id: "actual" });
    expect(items).toEqual([{ tick: 1 }, { tick: 2 }]);
  });
});
