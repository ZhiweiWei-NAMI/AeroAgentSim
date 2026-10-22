import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider } from '../i18n/I18nProvider';
import { TransportError } from '../feeds/transport';
import { ApiConnection } from './ApiConnection';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

function mount(search = '', error?: unknown) {
  window.history.replaceState({}, '', `/${search}`);
  return render(<I18nProvider><ApiConnection error={error} /></I18nProvider>);
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
