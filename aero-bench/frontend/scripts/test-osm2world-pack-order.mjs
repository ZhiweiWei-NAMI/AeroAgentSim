import assert from "node:assert/strict";
import test from "node:test";

import { compareBatchEntries, compareMeshRecords } from "./osm2world-pack-order.mjs";

function record(id, positions, indices = [0, 1, 2]) {
  return {
    target: id === null ? null : { kind: "building", id },
    arrays: [
      new Float32Array(positions),
      new Float32Array(positions.map(() => 1)),
      new Float32Array(positions.slice(0, 2)),
      new Uint32Array(indices),
    ],
  };
}

function signature(entries) {
  return entries.map(entry => [
    entry.target?.id ?? null,
    ...entry.arrays.map(array => Buffer.from(
      array.buffer, array.byteOffset, array.byteLength).toString("hex")),
  ]);
}

test("mesh ordering is independent of converter delivery order", () => {
  const entries = [
    record("w2", [2, 0, 0]),
    record("w1", [9, 0, 0]),
    record("w1", [1, 0, 0]),
    record(null, [4, 0, 0]),
  ];
  const expected = signature([...entries].sort(compareMeshRecords));
  for (const permutation of [
    entries,
    [...entries].reverse(),
    [entries[2], entries[0], entries[3], entries[1]],
  ]) {
    assert.deepEqual(signature([...permutation].sort(compareMeshRecords)), expected);
  }
});

test("raw typed-array bytes break ties including signed zero", () => {
  const positive = record("same", [0, 1, 2]);
  const negative = record("same", [-0, 1, 2]);
  assert.notEqual(compareMeshRecords(positive, negative), 0);
  assert.equal(compareMeshRecords(positive, positive), 0);
});

test("batch ordering is independent of Map insertion order", () => {
  const entries = [["roads", {}], ["buildings", {}], ["terrain", {}]];
  assert.deepEqual(
    [...entries].sort(compareBatchEntries).map(([key]) => key),
    ["buildings", "roads", "terrain"],
  );
});
