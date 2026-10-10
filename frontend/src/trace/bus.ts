import type { CauseRef } from './cause-trace';

/**
 * Lets any page open the "Why did this happen?" story for a recorded moment
 * without importing the Inspect page. The run page listens and switches to the
 * explanation view.
 *
 *   openCauseTrace({ kind: 'event', cut: event.cut, text: event.text, entities: event.entities })
 */
export const CAUSE_TRACE_EVENT = 'aeroagentsim:open-cause-trace';
export function openCauseTrace(ref: CauseRef): void {
  window.dispatchEvent(new CustomEvent<CauseRef>(CAUSE_TRACE_EVENT, { detail: ref }));
}
export function subscribeCauseTrace(listener: (ref: CauseRef) => void): () => void {
  const handler = (event: Event) => listener((event as CustomEvent<CauseRef>).detail);
  window.addEventListener(CAUSE_TRACE_EVENT, handler);
  return () => window.removeEventListener(CAUSE_TRACE_EVENT, handler);
}
