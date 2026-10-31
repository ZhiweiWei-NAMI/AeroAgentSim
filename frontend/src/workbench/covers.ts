/**
 * Static covers for the AeroRescue scene families.
 *
 * The rescue templates ship their covers with the frontend under
 * `/data/rescue_covers/<SID>.jpg`, while the backend only advertises a
 * `thumbnail_url` once it has re-read a template's `thumbnail` entry. These
 * helpers keep both sources honest: a server-provided URL always wins, and a
 * rescue id/scenario falls back to the static cover. Anything else stays
 * undefined so the caller renders no image at all.
 */

/** Run ids look like `SC04_rescue.scenario-…`; scenarios like `SC04-rescue-…`. */
const RESCUE_RUN = /^(SC\d{2}|GT\d{2})[_-]rescue/i;
/** Template ids look like `rescue-sc04`. */
const RESCUE_TEMPLATE = /^rescue-(sc\d{2}|gt\d{2})$/i;

/** Cover for a run listing, or undefined when the run is not a rescue run. */
export function runCoverUrl(run: { id?: string; scenario?: string; thumbnail_url?: string }): string | undefined {
  if (run.thumbnail_url) return run.thumbnail_url;
  for (const value of [run.id, run.scenario]) {
    const match = typeof value === 'string' ? RESCUE_RUN.exec(value) : null;
    if (match) return `/data/rescue_covers/${match[1].toUpperCase()}.jpg`;
  }
  return undefined;
}

/** Cover for a template card/hero, with the same server-first precedence. */
export function templateCoverUrl(template: { id?: string; thumbnail_url?: string }): string | undefined {
  if (template.thumbnail_url) return template.thumbnail_url;
  const match = typeof template.id === 'string' ? RESCUE_TEMPLATE.exec(template.id) : null;
  return match ? `/data/rescue_covers/${match[1].toUpperCase()}.jpg` : undefined;
}
