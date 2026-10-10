/** Bounded byte reads shared by the sealed trace, manifest, and asset loaders. */
export function assertNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("Replay request was aborted", "AbortError");
}

/** End the current task so long synchronous steps of a load run in separate tasks. */
export async function nextTask(signal?: AbortSignal): Promise<void> {
  await new Promise<void>(resolve => setTimeout(resolve, 0));
  assertNotAborted(signal);
}

export async function readBoundedResponse(
  response: Response,
  maxBytes: number,
  label: string,
  signal?: AbortSignal,
  expectedBytes?: number,
  onProgress?: (completed: number, total?: number) => void,
): Promise<ArrayBuffer> {
  assertNotAborted(signal);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) {
    throw new Error(`${label} byte bound is invalid`);
  }
  const header = response.headers.get("Content-Length");
  if (header !== null) {
    const length = Number(header);
    if (!/^\d+$/.test(header) || !Number.isSafeInteger(length) || length > maxBytes
      || (expectedBytes !== undefined && length !== expectedBytes)) {
      void response.body?.cancel().catch(() => undefined);
      throw new Error(`${label} Content-Length differs from its declared byte bound`);
    }
  }
  if (response.body === null) throw new Error(`${label} response has no body`);
  let length = 0;
  const encoding = response.headers.get("Content-Encoding");
  const identityEncoded = encoding === null || encoding.trim().toLowerCase() === "identity";
  // Content-Length on compressed responses describes wire bytes, not the
  // decoded stream returned by fetch. Only compare like-for-like counts.
  const total = expectedBytes ?? (header !== null && identityEncoded ? Number(header) : undefined);
  try {
    if (header !== null && identityEncoded) {
      // The validated Content-Length and HTTP length framing bound this identity
      // response before consumption. Use the original builtin consumer so Blink
      // waits for network completion instead of cancelling a JS-drained loader.
      // Unknown-length and decoded/compressed bodies remain incrementally bounded.
      onProgress?.(0, total);
      assertNotAborted(signal);
      let abortListener: (() => void) | undefined;
      const aborted = new Promise<never>((_resolve, reject) => {
        if (signal === undefined) return;
        abortListener = () => {
          const error = new DOMException("Replay request was aborted", "AbortError");
          // Fetch shares the signal. A synthetic body may not; cancel only when
          // unclaimed, since builtin consumption locks its stream internally.
          if (!response.body!.locked) void response.body!.cancel(error).catch(() => undefined);
          reject(error);
        };
        signal.addEventListener("abort", abortListener, { once: true });
      });
      let bytes: ArrayBuffer;
      try {
        bytes = await Promise.race([response.arrayBuffer(), aborted]);
      } finally {
        if (abortListener !== undefined) signal!.removeEventListener("abort", abortListener);
      }
      assertNotAborted(signal);
      length = bytes.byteLength;
      if (length !== Number(header)) {
        throw new Error(`${label} byte length ${length} differs from the declared size ${header}`);
      }
      onProgress?.(length, total);
      assertNotAborted(signal);
      return bytes;
    }
    // Enforce the decoded byte bound before each chunk is admitted.
    const bounded = response.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      start(): void { onProgress?.(0, total); },
      transform(value, controller): void {
        assertNotAborted(signal);
        length += value.byteLength;
        if (length > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte limit`);
        onProgress?.(length, total);
        controller.enqueue(value);
      },
    }), { signal });
    const bytes = await new Response(bounded).arrayBuffer();
    assertNotAborted(signal);
    if (total !== undefined && length !== total) {
      throw new Error(`${label} byte length ${length} differs from the declared size ${total}`);
    }
    if (expectedBytes !== undefined && length !== expectedBytes) {
      throw new Error(`${label} byte length ${length} differs from the declared size ${expectedBytes}`);
    }
    return bytes;
  } catch (error) {
    // pipeThrough cancels its upstream on transform/abort failure. If construction
    // failed before the pipe acquired the body, cancel that unclaimed body here.
    if (!response.body.locked) void response.body.cancel(error).catch(() => undefined);
    assertNotAborted(signal);
    throw error;
  }
}
