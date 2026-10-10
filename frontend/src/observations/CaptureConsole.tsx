import { useEffect, useRef, useState } from 'react';
import { RunsApi } from '../feeds/http';
import { captureError, renderCapture, type CaptureInput, type CaptureResult } from './capture';
import { useI18n } from '../i18n/I18nProvider';
declare global { interface Window { aeroCapture?: { render(input: CaptureInput): Promise<CaptureResult> } } }
/** Explicit capture mode, mounted at /runs?capture=1&capture_assets=<asset manifest URL>. */
export function CaptureConsole({ apiBase, assetManifestUrl }: { apiBase: string; assetManifestUrl: string | null }) {
  const { t, locale } = useI18n();
  const translation = useRef({t,locale});translation.current={t,locale};
  const mount = useRef<HTMLDivElement>(null), [error, setError] = useState(''), [captured, setCaptured] = useState<string>();
  useEffect(() => {
    let busy = false;
    const bridge = { async render(input: CaptureInput) {
      const { t } = translation.current;
      if (!assetManifestUrl) throw Error(t('Set capture_assets to a capture asset manifest URL'));
      if (busy) throw Error(t('Capture renderer already has an active request')); busy = true; setError('');
      try { const result = await renderCapture(new RunsApi(apiBase), assetManifestUrl, input, mount.current!); setCaptured(String(input.request.request_id)); return result; }
      catch (problem) { setError(captureError(problem,translation.current.locale)); throw problem; }
      finally { busy = false; }
    } };
    window.aeroCapture = bridge; return () => { if (window.aeroCapture === bridge) delete window.aeroCapture; };
  }, [apiBase, assetManifestUrl]);
  return <section><h1>{t('Exact committed-cut capture')}</h1><p>{captured ? t('Captured {request}', {request:captured}) : t('Waiting for recorded capture request')}</p>{error && <p role="alert">{error}</p>}<div ref={mount} data-testid="capture-canvas" /></section>;
}
