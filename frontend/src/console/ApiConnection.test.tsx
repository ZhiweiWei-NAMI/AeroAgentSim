import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { requestJson, TransportError } from '../feeds/transport';
import { ApiError, WorkbenchApi } from '../workbench/api';
import { ApiConnection } from './ApiConnection';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

function Navigation() {
  const navigate = useNavigate();
  return <>
    <button onClick={() => navigate('/?api=http%3A%2F%2Fother.test')}>Other API</button>
    <button onClick={() => navigate(-1)}>Back</button>
  </>;
}

function mount(search = '', error?: unknown) {
  window.history.replaceState({}, '', `/${search}`);
  return render(<I18nProvider><MemoryRouter initialEntries={[`/${search}`]}>
    <ApiConnection error={error} /><Navigation />
  </MemoryRouter></I18nProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/');
});

describe('ApiConnection', () => {
  it.each([401, 403])('turns HTTP %i into the unsupported-browser-token notice', async status => {
    const fetcher = vi.fn().mockResolvedValue(json({ detail: 'Bearer token required' }, status));
    vi.stubGlobal('fetch', fetcher);
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'authentication required: browser token support is not available; use a local unprotected server',
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit | undefined];
    expect(url).toBe(`${window.location.origin}/v1/templates`);
    expect(url).not.toMatch(/token|authorization|api_key/i);
    expect(init?.headers).toBeUndefined();
  });

  it('shows the Chinese notice in the Chinese locale', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'Bearer token required' }, 401)));
    mount('?lang=zh');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '需要身份验证：浏览器不支持令牌；请使用本地未受保护的服务器',
    );
  });

  it('stays silent when the local server answers', async () => {
    const fetcher = vi.fn().mockResolvedValue(json([]));
    vi.stubGlobal('fetch', fetcher);
    mount();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('api-connection')).toBeNull();
  });

  it('does not turn an unreachable server into an authentication claim', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetcher);
    mount();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('api-connection')).toBeNull();
  });

  it.each([
    ['', 'authentication required: browser token support is not available; use a local unprotected server'],
    ['&lang=zh', '需要身份验证：浏览器不支持令牌；请使用本地未受保护的服务器'],
  ])('observes a real write-origin rejection after a successful read probe (%s)', async (locale, notice) => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ detail: 'Untrusted request Host or Origin' }, 403));
    vi.stubGlobal('fetch', fetcher);
    mount(`?api=http%3A%2F%2Fapi.test${locale}`);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('alert')).toBeNull();
    await act(async () => {
      await expect(new WorkbenchApi('http://api.test').control('run', 'start')).rejects.toBeInstanceOf(ApiError);
    });
    expect(await screen.findByRole('alert')).toHaveTextContent(notice);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][0]).toBe('http://api.test/v1/templates');
    expect(fetcher.mock.calls[0][1]?.method).toBeUndefined();
    expect(fetcher.mock.calls[0][1]?.body).toBeUndefined();
    expect(fetcher.mock.calls[1][1]?.method).toBe('POST');
  });

  it('does not let a late successful probe erase an observed write rejection', async () => {
    let finishProbe!: (response: Response) => void;
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finishProbe = resolve; }))
      .mockResolvedValueOnce(json({ detail: 'Untrusted request Host or Origin' }, 403));
    vi.stubGlobal('fetch', fetcher);
    mount('?api=http%3A%2F%2Fapi.test');
    await act(async () => {
      await expect(requestJson('http://api.test/', '/v1/runs', { method: 'POST', body: '{}' })).rejects.toMatchObject({ status: 403 });
    });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    await act(async () => { finishProbe(json([])); });
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('reprobes router API changes and history navigation without retaining another server warning', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(json({ detail: 'Bearer token required' }, 401))
      .mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ detail: 'old server refused' }, 403))
      .mockResolvedValueOnce(json({ detail: 'Bearer token required' }, 401));
    vi.stubGlobal('fetch', fetcher);
    mount('?api=http%3A%2F%2Fapi.test');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Other API' }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fetcher.mock.calls[1][0]).toBe('http://other.test/v1/templates');
    // A late failure from the previous server must not affect the new base.
    await act(async () => {
      await expect(requestJson('http://api.test', '/v1/runs', { method: 'POST', body: '{}' })).rejects.toMatchObject({ status: 403 });
    });
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(fetcher.mock.calls[3][0]).toBe('http://api.test/v1/templates');
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it('shows a warning when router navigation selects a newly protected API', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ detail: 'Bearer token required' }, 401));
    vi.stubGlobal('fetch', fetcher);
    mount('?api=http%3A%2F%2Fapi.test');
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Other API' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(fetcher.mock.calls[1][0]).toBe('http://other.test/v1/templates');
  });

  it('ignores an old in-flight probe after switching API bases', async () => {
    let finishOldProbe!: (response: Response) => void;
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finishOldProbe = resolve; }))
      .mockResolvedValueOnce(json([]));
    vi.stubGlobal('fetch', fetcher);
    mount('?api=http%3A%2F%2Fapi.test');
    fireEvent.click(screen.getByRole('button', { name: 'Other API' }));
    await act(async () => { finishOldProbe(json({ detail: 'Bearer token required' }, 401)); });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fetcher.mock.calls[0][1]?.signal.aborted).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('renders an already observed authentication failure without probing again', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    mount('', new TransportError(403, 'Untrusted request Host or Origin'));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'authentication required: browser token support is not available; use a local unprotected server',
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
});
