import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  checkedFetch, isAuthenticationError, isRetryableReadError, localizedTransportMessage, probeConnection,
  requestJson, requestText, subscribeConnectionErrors, TransportError,
} from './transport';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
});

async function failure(promise: Promise<unknown>): Promise<TransportError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof TransportError) return error;
    throw error;
  }
  throw new Error('Expected the transport to reject');
}

afterEach(() => { vi.unstubAllGlobals(); });

describe('transport error policy', () => {
  it.each([
    [401, 'authentication', false],
    [403, 'forbidden', false],
    [404, 'not_found', true],
    [501, 'unsupported', true],
  ] as const)('classifies HTTP %i as %s', async (status, kind, unavailable) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'server says no' }, status)));
    const error = await failure(requestText('http://api.test', '/v1/runs'));
    expect(error.status).toBe(status);
    expect(error.kind).toBe(kind);
    expect(error.unavailable).toBe(unavailable);
    expect(error.message).toBe(`HTTP ${status}: server says no`);
    expect(error.en.length).toBeGreaterThan(0);
    expect(error.zh).toMatch(/[\u4e00-\u9fff]/);
  });

  it('keeps the protected-browser notice exact and bilingual', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'Bearer token required' }, 401)));
    const error = await failure(requestText('http://api.test', '/v1/runs'));
    expect(error.en).toBe('authentication required: browser token support is not available; use a local unprotected server');
    expect(error.zh).toBe('需要身份验证：浏览器不支持令牌；请使用本地未受保护的服务器');
    expect(localizedTransportMessage(error, 'en-US')).toBe(error.en);
    expect(localizedTransportMessage(error, 'zh-CN')).toBe(error.zh);
    expect(isAuthenticationError(error)).toBe(true);
  });

  it('classifies a network failure without retrying it', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetcher);
    const error = await failure(requestText('http://api.test', '/v1/runs'));
    expect(error.status).toBe(0);
    expect(error.kind).toBe('network');
    expect(error.message).toBe('Connection failed: Failed to fetch');
    expect(error.zh).toContain('无法连接');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(isRetryableReadError(error)).toBe(true);
  });

  it.each([401, 503])('does not retry a state-changing POST on HTTP %i', async status => {
    const fetcher = vi.fn().mockResolvedValue(json({ detail: 'do not retry' }, status));
    vi.stubGlobal('fetch', fetcher);
    const error = await failure(requestJson('http://api.test', '/v1/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    }));
    expect(error.status).toBe(status);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://api.test/v1/runs');
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{}');
  });

  it('forwards abort signals and rethrows an abort unchanged', async () => {
    const controller = new AbortController();
    const aborted = new DOMException('The operation was aborted.', 'AbortError');
    const fetcher = vi.fn().mockRejectedValue(aborted);
    vi.stubGlobal('fetch', fetcher);
    await expect(requestText('http://api.test', '/v1/runs', { signal: controller.signal })).rejects.toBe(aborted);
    const [, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
  });

  describe.each([
    ['checkedFetch', checkedFetch], ['requestJson', requestJson], ['requestText', requestText],
  ] as const)('error-body cancellation through %s', (_name, request) => {
    it.each([401, 403, 503])('preserves abort identity after HTTP %i headers', async status => {
      const controller = new AbortController();
      const aborted = new DOMException('Body read cancelled', 'AbortError');
      const response = new Response('', { status });
      let started!: () => void;
      const bodyStarted = new Promise<void>(resolve => { started = resolve; });
      vi.spyOn(response, 'text').mockImplementation(() => new Promise<string>((_resolve, reject) => {
        controller.signal.addEventListener('abort', () => reject(aborted), { once: true });
        started();
      }));
      const fetcher = vi.fn().mockResolvedValue(response);
      vi.stubGlobal('fetch', fetcher);
      const pending = request('http://api.test', '/v1/runs', { signal: controller.signal });
      const rejection = expect(pending).rejects.toBe(aborted);
      await bodyStarted;
      controller.abort();
      await rejection;
      expect(fetcher).toHaveBeenCalledTimes(1);
    });

    it('preserves a custom cancellation reason when the signal is aborted', async () => {
      const controller = new AbortController();
      const reason = new TypeError('Custom cancellation');
      const response = new Response('', { status: 503 });
      vi.spyOn(response, 'text').mockImplementation(async () => {
        controller.abort(reason);
        throw reason;
      });
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
      await expect(request('http://api.test', '/v1/runs', { signal: controller.signal })).rejects.toBe(reason);
    });

    it('rethrows an AbortError even without a caller signal', async () => {
      const aborted = new DOMException('Body read cancelled', 'AbortError');
      const response = new Response('', { status: 503 });
      vi.spyOn(response, 'text').mockRejectedValue(aborted);
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
      await expect(request('http://api.test', '/v1/runs')).rejects.toBe(aborted);
    });
  });

  it('retains the HTTP fallback for a non-abort error-body failure', async () => {
    const response = new Response('', { status: 503, statusText: 'Service Unavailable' });
    vi.spyOn(response, 'text').mockRejectedValue(new TypeError('Body stream failed'));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    const error = await failure(requestText('http://api.test', '/v1/runs'));
    expect(error.status).toBe(503);
    expect(error.detail).toBe('Service Unavailable');
  });

  it('uses the caller parse policy for lossless numbers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"exact":9223372036854775815}')));
    expect(await requestJson('http://api.test', '/v1/value')).toEqual({
      exact: { $integer: '9223372036854775815' },
    });
  });

  it('keeps raw successful text and lifts a JSON error detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('run: {}\n')));
    expect(await requestText('http://api.test', '/v1/export')).toBe('run: {}\n');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'missing' }, 404)));
    const error = await failure(requestText('http://api.test', '/v1/export'));
    expect(error.message).toBe('HTTP 404: missing');
  });

  it.each([
    ['{"detail":"server says no"}', 'server says no'],
    ['{"detail":{"field":"seconds","message":"Invalid duration"}}', '{"field":"seconds","message":"Invalid duration"}'],
    ['{}', '{}'],
    ['upstream unavailable', 'upstream unavailable'],
  ])('retains the raw error body alongside its normalized detail: %j', async (body, detail) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 500 })));
    const error = await failure(requestJson('http://api.test', '/v1/runs'));
    expect(error.status).toBe(500);
    expect(error.detail).toBe(detail);
    expect(error.body).toBe(body);
  });

  it('probes a read-only endpoint without credentials or a request body', async () => {
    const fetcher = vi.fn().mockResolvedValue(json([]));
    vi.stubGlobal('fetch', fetcher);
    await probeConnection('http://api.test/');
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit | undefined];
    expect(url).toBe('http://api.test/v1/templates');
    expect(init).toBeUndefined();
  });

  it('publishes only 401/403 answers to matching API observers and supports cleanup', async () => {
    const listener = vi.fn();
    const otherListener = vi.fn();
    const unsubscribe = subscribeConnectionErrors('http://api.test/', listener);
    const unsubscribeOther = subscribeConnectionErrors('http://other.test', otherListener);
    try {
      for (const status of [401, 403, 404, 503]) {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'refused' }, status)));
        await failure(requestJson('http://api.test', '/v1/runs', { method: 'POST', body: '{}' }));
      }
      expect(listener.mock.calls.map(([error]) => error.status)).toEqual([401, 403]);
      expect(otherListener).not.toHaveBeenCalled();
      unsubscribe();
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({}, 403)));
      await failure(requestText('http://api.test', '/v1/templates'));
      expect(listener).toHaveBeenCalledTimes(2);
    } finally {
      unsubscribe();
      unsubscribeOther();
    }
  });

  it('does not let an observer replace the original request error', async () => {
    const unsubscribe = subscribeConnectionErrors('http://api.test', () => { throw new Error('observer failed'); });
    try {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'origin refused' }, 403)));
      const error = await failure(requestText('http://api.test', '/v1/templates'));
      expect(error.status).toBe(403);
      expect(error.detail).toBe('origin refused');
    } finally { unsubscribe(); }
  });

  it('only retries reads and treats 501 as an absent capability', async () => {
    const unavailable = new TransportError(501, 'not implemented');
    expect(isRetryableReadError(unavailable)).toBe(false);
    expect(isRetryableReadError(new TransportError(503, 'busy'))).toBe(true);
    expect(isRetryableReadError(new TypeError('network'))).toBe(true);
    expect(isRetryableReadError(new Error('plain'))).toBe(false);
    expect(isAuthenticationError(new TransportError(403, 'origin'))).toBe(true);
  });
});
