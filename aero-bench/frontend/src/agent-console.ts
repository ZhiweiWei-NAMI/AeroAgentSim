import type { PublicRunEvent } from "./generated/aero-bench-contracts";
import { currentLanguage, t } from "./i18n";
import { assertPublicEventSafe } from "./event-channels";
import { formatSimTime, shortDigest } from "./view-model";
import { append, element, emptyRow, sectionTitle } from "./ui";

export interface AgentConsoleSource {
  /** Authorized public events, ascending sequence (live buffer or sealed trace). */
  readonly events: readonly PublicRunEvent[];
}

const MAX_AGENT_ROWS = 100;

/**
 * Read-only agent interaction console. It shows only authorized public
 * RunEvent projections: explicit decision summaries with their declared
 * payload attributes, plus I/O references (observation/command ids and
 * payload digests) when the public record carries them. Hidden model
 * reasoning is neither requested nor displayed; records outside the
 * public vocabulary are rejected, never rendered.
 */
export class AgentConsole {
  readonly container: HTMLElement;
  private source: AgentConsoleSource | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.container.classList.add("agent-console");
  }

  update(source: AgentConsoleSource): void {
    this.source = source;
    this.render();
  }

  /** Re-render with the current source (e.g. after a language change). */
  refresh(): void {
    this.render();
  }

  private render(): void {
    const lang = currentLanguage();
    this.container.replaceChildren();
    this.container.append(sectionTitle(t("agent.title", lang)));
    const note = element("div", "agent-note", t("agent.boundaryNote", lang));
    this.container.append(note);
    const events = this.source?.events ?? [];
    const agentEvents: PublicRunEvent[] = [];
    for (let index = events.length - 1; index >= 0 && agentEvents.length < MAX_AGENT_ROWS; index -= 1) {
      const event = events[index]!;
      if (event.source_kind === "agent" || event.agent_id !== null) {
        try {
          assertPublicEventSafe(event);
        } catch {
          continue;
        }
        agentEvents.push(event);
      }
    }
    if (agentEvents.length === 0) {
      this.container.append(emptyRow(t("agent.none", lang)));
      return;
    }
    const list = element("div", "agent-list");
    for (const event of agentEvents) {
      list.append(this.agentRow(event, lang));
    }
    this.container.append(list);
  }

  private agentRow(event: PublicRunEvent, lang: "zh" | "en"): HTMLElement {
    const row = element("div", "agent-row");
    const head = element("div", "agent-row-head");
    append(
      head,
      element("strong", undefined, event.agent_id ?? event.source),
      element(
        "span",
        "agent-interaction",
        event.interaction_type ?? event.event_type,
      ),
      element(
        "time",
        "agent-time",
        `${t("feed.tick", lang)} ${event.at.tick} · ${formatSimTime(event.at.sim_time_ns)}`,
      ),
    );
    row.append(head);
    const refs = element("div", "agent-refs");
    append(
      refs,
      element("span", "agent-ref", `${t("agent.correlation", lang)} ${event.correlation_id}`),
      element("span", "agent-ref", `${t("agent.digest", lang)} ${shortDigest(event.payload_digest)}`),
      event.observation_id === null
        ? null
        : element("span", "agent-ref", `${t("agent.observation", lang)} ${event.observation_id}`),
      event.command_id === null
        ? null
        : element("span", "agent-ref", `${t("agent.command", lang)} ${event.command_id}`),
      event.causal_event_ids.length === 0
        ? null
        : element("span", "agent-ref", `${t("agent.causal", lang)} ${event.causal_event_ids.join(", ")}`),
    );
    row.append(refs);
    if (event.public_payload.length > 0) {
      const payload = element("div", "agent-payload");
      for (const attribute of event.public_payload) {
        payload.append(
          element(
            "span",
            "agent-attr",
            `${attribute.name} = ${attribute.value === null ? "null" : String(attribute.value)} (${attribute.value_type})`,
          ),
        );
      }
      row.append(payload);
    }
    return row;
  }

  dispose(): void {
    this.container.replaceChildren();
    this.source = null;
  }
}
