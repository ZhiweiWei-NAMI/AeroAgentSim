import { afterEach, describe, expect, it, vi } from 'vitest';
import { StudioApi } from './api';

const BASE = 'http://studio.test';

const fetchMock = vi.fn();

const jsonResponse = (status: number, payload: unknown) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
  text: async () => JSON.stringify(payload),
});

const rawResponse = (status: number, body: string) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => JSON.parse(body),
  text: async () => body,
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe('StudioApi', () => {
  it('POSTs the real scenario body with writer bindings preserved', async () => {
    const scenario = {
      format: 'aeroagentsim.scenario/v1',
      bindings: { exact: [{ entity: 'actor', field: 'position', writer: 'motion' }] },
    };
    fetchMock.mockResolvedValue(jsonResponse(200, { id: 'ws-1' }));
    vi.stubGlobal('fetch', fetchMock);

    const api = new StudioApi(BASE);
    const result = await api.request<{ id: string }>('/v1/studio/workspaces', scenario);

    expect(result).toEqual({ id: 'ws-1' });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain('/v1/studio/workspaces');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual(scenario);
  });

  it('sends a GET request when no body is given and returns the parsed payload', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { workspaces: [{ id: 'ws-1' }] }));
    vi.stubGlobal('fetch', fetchMock);

    const api = new StudioApi(BASE);
    const result = await api.request<{ workspaces: Array<{ id: string }> }>('/v1/studio/workspaces');

    expect(result).toEqual({ workspaces: [{ id: 'ws-1' }] });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit | undefined];
    expect(String(url)).toContain('/v1/studio/workspaces');
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('surfaces a 422 error instead of a successful-looking payload', async () => {
    fetchMock.mockResolvedValue(
      rawResponse(422, JSON.stringify({ detail: 'scenario rejected', result: { id: 'ws-1' } })),
    );
    vi.stubGlobal('fetch', fetchMock);

    const api = new StudioApi(BASE);
    await expect(api.request('/v1/studio/workspaces', { name: 'bad' })).rejects.toThrow(
      'HTTP 422: scenario rejected',
    );
    
  });

  it('refuses a YAML export that fails with 422', async () => {
    fetchMock.mockResolvedValue(rawResponse(422, 'duplicate workspace id: ws-1'));
    vi.stubGlobal('fetch', fetchMock);

    const api = new StudioApi(BASE);
    await expect(api.export('ws-1')).rejects.toThrow('HTTP 422: duplicate workspace id: ws-1');

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(String(url)).toBe(`${BASE}/v1/studio/workspaces/ws-1/export`);
  });
});
