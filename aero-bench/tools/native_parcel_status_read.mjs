const TEMPORARY_STATUS_CODES = new Set(['control.unavailable', 'service.capacity']);

/** Re-read only declared temporary availability failures; every failure remains evidence. */
export async function readRuntimeStatusResponse({ request, timeoutMs, backoffMs, onFailure,
  wait, now = () => performance.now() }) {
  const started = now();
  let attempt = 0;
  while (true) {
    const remainingMs = timeoutMs - (now() - started);
    if (remainingMs <= 0) throw new Error('Temporary runtime status re-read budget exhausted');
    attempt += 1;
    const response = await request(remainingMs);
    if (response.ok()) return response;
    const body = await response.json();
    const elapsedMs = now() - started;
    const retrying = response.status() === 503 && TEMPORARY_STATUS_CODES.has(body.error?.code)
      && elapsedMs + backoffMs < timeoutMs;
    await onFailure({ attempt, status: response.status(), response: body,
      retrying, elapsed_ms: elapsedMs });
    if (!retrying) {
      throw new Error(`Status failed: HTTP ${response.status()} ${body.error?.code ?? 'missing error code'}: ${body.error?.detail ?? 'missing error detail'}`);
    }
    await wait(backoffMs);
  }
}
