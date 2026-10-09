import { useEffect, useRef, useState } from 'react';
import { RunsApi } from '../feeds/http';
import { renderCapture, type CaptureInput, type CaptureResult } from './capture';
declare global { interface Window { aeroCapture?: { render(input: CaptureInput): Promise<CaptureResult> } } }
/** Explicit capture mode, mounted at /runs?capture=1&capture_assets=<verified manifest URL>. */
export function CaptureConsole({ apiBase, assetManifestUrl }: { apiBase: string; assetManifestUrl: string | null }) {
  const mount = useRef<HTMLDivElement>(null), [error, setError] = useState(''), [label, setLabel] = useState('Waiting for recorded capture request');
  useEffect(() => {
    let busy = false;
    const bridge = { async render(input: CaptureInput) {
      if (!assetManifestUrl) throw Error('Set capture_assets to a content-pinned capture asset manifest URL');
      if (busy) throw Error('Capture renderer already has an active request'); busy = true; setError('');
      try { const result = await renderCapture(new RunsApi(apiBase), assetManifestUrl, input, mount.current!); setLabel(`Captured ${String(input.request.request_id)}`); return result; }
      catch (problem) { setError(String(problem)); throw problem; }
      finally { busy = false; }
    } };
    window.aeroCapture = bridge; return () => { if (window.aeroCapture === bridge) delete window.aeroCapture; };
  }, [apiBase, assetManifestUrl]);
  return <section><h1>Exact committed-cut capture</h1><p>{label}</p>{error && <p role="alert">{error}</p>}<div ref={mount} data-testid="capture-canvas" /></section>;
}
