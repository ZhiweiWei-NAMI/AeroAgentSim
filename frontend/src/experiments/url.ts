export const EXPERIMENT_QUERY = 'experiment';

/** Read the persistent experiment id from the page query. */
export function experimentIdFromLocation(search = window.location.search): string | undefined {
  const value = new URLSearchParams(search).get(EXPERIMENT_QUERY)?.trim();
  return value || undefined;
}

/**
 * Keep the experiment id in the address bar so it survives a refresh and can be
 * bookmarked. `records=1` makes a shared link open the panel that reads the id.
 * The popstate event lets the router (and the rest of the page) see the change.
 */
export function setExperimentQuery(id: string | undefined): void {
  const url = new URL(window.location.href);
  if (id) {
    url.searchParams.set(EXPERIMENT_QUERY, id);
    url.searchParams.set('records', '1');
    // A stale gallery flag would otherwise keep the Workbench from opening Records.
    url.searchParams.delete('gallery');
  } else {
    url.searchParams.delete(EXPERIMENT_QUERY);
  }
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
