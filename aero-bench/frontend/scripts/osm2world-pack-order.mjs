/** Pure ordering helpers shared by the browser pack builder and its Node test. */

function compareStrings(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function targetKey(target) {
  if (target === null) return "";
  return `${target.kind}\u0000${target.id}`;
}

function bytes(value) {
  return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
}

function compareTypedArrays(left, right) {
  const typeOrder = compareStrings(left.constructor.name, right.constructor.name);
  if (typeOrder !== 0) return typeOrder;
  const a = bytes(left);
  const b = bytes(right);
  const count = Math.min(a.length, b.length);
  for (let index = 0; index < count; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return a.length - b.length;
}

/**
 * Order source meshes before BufferGeometryUtils merges them. The full typed
 * array payload is the final tie-breaker, so converter scheduling cannot alter
 * packed bytes or range order. Exact duplicate records are interchangeable.
 */
export function compareMeshRecords(left, right) {
  const targetOrder = compareStrings(targetKey(left.target), targetKey(right.target));
  if (targetOrder !== 0) return targetOrder;
  for (let index = 0; index < left.arrays.length; index += 1) {
    const arrayOrder = compareTypedArrays(left.arrays[index], right.arrays[index]);
    if (arrayOrder !== 0) return arrayOrder;
  }
  return left.arrays.length - right.arrays.length;
}

/** Sort material/layer Map entries without depending on first-seen mesh order. */
export function compareBatchEntries(left, right) {
  return compareStrings(left[0], right[0]);
}

