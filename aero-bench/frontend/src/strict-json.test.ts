import { describe, expect, it } from "vitest";
import { canonicalJsonWithoutMember, parseStrictJson } from "./strict-json";

describe("sealed evidence JSON", () => {
  it("preserves Python float and exponent spellings when removing the digest field", () => {
    const raw = '{"a":1.0,"b":-0.0,"scene_state_digest":"digest","z":1e-09}';
    expect(canonicalJsonWithoutMember(raw, "scene_state_digest")).toBe('{"a":1.0,"b":-0.0,"z":1e-09}');
    expect(parseStrictJson(raw)).toEqual({ a: 1, b: -0, scene_state_digest: "digest", z: 1e-9 });
  });

  it.each([
    '{"a":1,"a":2}',
    '{"nested":{"a":1,"\\u0061":2}}',
    '[{"a":1,"a":2}]',
    '{"a":1e309}',
    '{"a":9007199254740993}',
    '{"a":NaN}',
    '{"a":1,}',
    '{"a":1}{}',
    `${"[".repeat(66)}0${"]".repeat(66)}`,
  ])("rejects ambiguous, nonfinite, inexact or malformed JSON: %s", raw => {
    expect(() => parseStrictJson(raw)).toThrow();
  });

  it.each([
    '{"z":1,"scene_state_digest":"digest","a":2}',
    '{ "a":1,"scene_state_digest":"digest"}',
    '{"a":1}',
  ])("rejects noncanonical hash input: %s", raw => {
    expect(() => canonicalJsonWithoutMember(raw, "scene_state_digest")).toThrow();
  });
});
