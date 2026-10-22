import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useI18n } from '../i18n/I18nProvider';
import { BROWSER_TOKEN_NOTICE, isAuthenticationError, probeConnection, subscribeConnectionErrors } from '../feeds/transport';

export interface ApiConnectionProps {
  /** API origin; defaults to `?api=` and then the page origin. */
  base?: string;
  /** A failure already observed by a feature; supplying it skips the probe. */
  error?: unknown;
}

/**
 * One console-level answer for an unsupported protected server. The browser
 * has no bearer-token path, so 401/403 is a terminal connection state here —
 * not a spinner that waits for a token that will never arrive.
 */
export function ApiConnection({ base, error }: ApiConnectionProps) {
  const { locale } = useI18n();
  const { search } = useLocation();
  const root = (base ?? new URLSearchParams(search).get('api') ?? window.location.origin).replace(/\/+$/, '');
  const [connectionError, setConnectionError] = useState<{ root: string; error: unknown }>();
  useEffect(() => {
    if (error !== undefined) return;
    const controller = new AbortController();
    setConnectionError(undefined);
    const unsubscribe = subscribeConnectionErrors(root, problem => {
      if (!controller.signal.aborted) setConnectionError({ root, error: problem });
    });
    void probeConnection(root, controller.signal).catch((problem: unknown) => {
      if (controller.signal.aborted || !isAuthenticationError(problem)) return;
      // Ordinary endpoint/network failures stay with their owning panel. A
      // successful read probe cannot clear a write-origin rejection, either.
      setConnectionError(current => current ?? { root, error: problem });
    });
    return () => { unsubscribe(); controller.abort(); };
  }, [error, root]);
  const problem = error !== undefined ? error : connectionError?.root === root ? connectionError.error : undefined;
  if (!isAuthenticationError(problem)) return null;
  return <div
    role="alert"
    data-testid="api-connection"
    style={{
      position: 'fixed', left: 16, bottom: 16, zIndex: 1400, maxWidth: 'min(560px, calc(100vw - 32px))',
      padding: '10px 14px', border: '1px solid #e0a35c', borderRadius: 8, background: '#fff7e8', color: '#5c3d12',
      boxShadow: '0 2px 10px rgba(11, 31, 51, 0.18)', font: "13px/1.45 Inter, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
    }}
  >
    {locale === 'zh-CN' ? BROWSER_TOKEN_NOTICE.zh : BROWSER_TOKEN_NOTICE.en}
  </div>;
}

export default ApiConnection;
