import { createElement, lazy, type Attributes, type ComponentType } from 'react';

const failedLoads = new Set<() => void>();
/** Reset only rejected imports. Already mounted forms keep their component identity and inputs. */
export function retryFailedLazyImports() {
  for (const reset of [...failedLoads]) reset();
}

/** Chromium/Firefox include the failed module URL in download errors. */
export function failedModuleUrl(error: unknown): string | undefined {
  if (!(error instanceof Error)) return;
  const match = /(?:Failed to fetch dynamically imported module|error loading dynamically imported module):?\s*(https?:\/\/\S+)/i.exec(error.message);
  if (!match) return;
  try {
    const url = new URL(match[1]);
    // Retry only our own executable assets, never arbitrary URLs from error text.
    if (url.origin === window.location.origin && /\.[cm]?[jt]sx?$/.test(url.pathname)) return url.href;
  } catch { /* A malformed URL is not a module download error. */ }
}

export function retryModuleUrl(url: string, attempt: number): string {
  const next = new URL(url);
  next.searchParams.set('aas-lazy-retry', String(attempt));
  return next.href;
}

export function lazyWithRetry<P extends object>(load: () => Promise<{ default: ComponentType<P> }>): ComponentType<P>;
export function lazyWithRetry<P extends object, M>(load: () => Promise<M>, select: (module: M) => ComponentType<P>): ComponentType<P>;
export function lazyWithRetry<P extends object, M>(load: () => Promise<M>, select: (module: M) => ComponentType<P>, reimport: (url: string) => Promise<M>): ComponentType<P>;
/** A boundary's Retry remounts a fresh lazy object after rejection, while resolved panels stay stable. */
export function lazyWithRetry<P extends object, M>(load: () => Promise<M>, select?: (module: M) => ComponentType<P>, reimport = (url: string): Promise<M> => import(/* @vite-ignore */ url)): ComponentType<P> {
  let failedUrl: string | undefined, attempt = 0;
  const reset = () => { current = makeLazy(); failedLoads.delete(reset); };
  const makeLazy = () => lazy(async () => {
    try {
      // Failed ESM downloads also live in the browser's module map. Changing this
      // one asset URL makes Retry issue a real request without reloading the draft.
      const module: M = failedUrl
        ? await reimport(retryModuleUrl(failedUrl, attempt))
        : await load();
      const pick = (value: M) => select ? select(value) : (value as unknown as { default: ComponentType<P> }).default;
      const namespace = (value: unknown) => value !== null && typeof value === 'object' && Object.prototype.toString.call(value) === '[object Module]';
      const componentType = (value: unknown): value is ComponentType<P> => typeof value === 'function' || value !== null && typeof value === 'object' && !namespace(value);
      let component = pick(module);
      if (failedUrl && !componentType(component)) {
        // Rollup can merge a lazy entry into a shared chunk and export its source
        // namespace under a shortened alias. Normal imports get a generated adapter;
        // a raw URL retry must recover that namespace before applying the selector.
        const candidates = new Set(Object.values(module as Record<string, unknown>)
          .filter(namespace).map(value => pick(value as M)).filter(componentType));
        if (candidates.size === 1) component = [...candidates][0];
      }
      if (!componentType(component)) throw Error('The downloaded module does not export the requested component.');
      return { default: component };
    } catch (error) {
      failedUrl = failedModuleUrl(error) ?? failedUrl;
      attempt += 1;
      // Leave the rejected lazy object in place until the user retries, so the
      // boundary displays the actual failure instead of silently retrying it.
      failedLoads.add(reset);
      throw error;
    }
  });
  let current = makeLazy();
  return function RetryablePanel(props: P) {
    // React's lazy props include conditional ref types; the wrapper forwards the
    // same P payload that the loader's component accepts.
    return createElement(current, props as unknown as Attributes & Parameters<typeof current>[0]);
  };
}
