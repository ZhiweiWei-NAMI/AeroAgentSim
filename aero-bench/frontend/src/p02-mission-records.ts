import { t, tf, type Language } from "./i18n";
import type { RunStageEvent } from "./p02-entity-overlays";

/** Mission records carry provider state, not a declared logistics order identity. */
export function renderUnboundMissionRecords(document: Document, events: readonly RunStageEvent[],
    language: Language, formatClock: (seconds: number) => string): HTMLElement | null {
  if (events.length === 0) return null;
  const section = document.createElement("section");
  section.className = "p02-mission-records p02-orders-body";
  section.setAttribute("aria-label", t("p02.missionRecords", language));
  const heading = document.createElement("div");
  heading.className = "p02-orders-head";
  heading.textContent = tf("p02.missionRecordCount", { count: events.length }, language);
  const note = document.createElement("p");
  note.className = "p02-orders-empty";
  note.textContent = t("p02.missionIdentityUnknown", language);
  section.append(heading, note);
  for (const event of events.slice(-4)) {
    const row = document.createElement("div");
    row.className = "p02-mission-record-row";
    row.dataset.eventId = event.eventId;
    const label = document.createElement("strong");
    label.textContent = `${event.providerId} · ${event.state}`;
    const detail = document.createElement("div");
    detail.textContent = `tick ${event.tick} · ${formatClock(event.flipTimeSeconds)}`;
    const evidence = document.createElement("small");
    evidence.textContent = event.evidence.map(item => `${item.artifact_id} [${item.selector}]`).join(" · ");
    row.append(label, detail, evidence);
    section.append(row);
  }
  return section;
}
