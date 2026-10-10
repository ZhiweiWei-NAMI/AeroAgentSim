import type { PublicRunEvent } from "./generated/aero-bench-contracts";
import { currentLanguage, t } from "./i18n";
import { assertPublicEventSafe } from "./event-channels";
import { formatSimTime } from "./view-model";
import { append, element, emptyRow, sectionTitle } from "./ui";

export interface InteractionTimelineSource {
  readonly availableOnly?: boolean;
  /** Authorized public events, ascending sequence (live buffer or sealed trace). */
  readonly events: readonly PublicRunEvent[];
}

/**
 * The canonical interaction stages of the public vocabulary, in
 * authoritative order. A chain renders only stages that an explicit
 * event declares; every other slot is drawn as a visible gap.
 */
const STAGE_ORDER = [
  "agent.observation.v1",
  "agent.decision_summary.v1",
  "agent.tool_call.v1",
  "agent.tool_result.v1",
  "gateway.dispatch.v1",
  "provider.command.v1",
  "mavlink.command.v1",
  "mavlink.command_ack.v1",
  "physical.effect.v1",
  "mission.event.v1",
  "verifier.evidence.v1",
] as const;

type StageName = (typeof STAGE_ORDER)[number];

const STAGE_LABEL_KEYS: Readonly<Record<StageName, "stage.observation" | "stage.decision" | "stage.toolCall" | "stage.toolResult" | "stage.dispatch" | "stage.providerCommand" | "stage.mavlinkCommand" | "stage.mavlinkAck" | "stage.physicalEffect" | "stage.missionEvent" | "stage.verifierEvidence">> = {
  "agent.observation.v1": "stage.observation",
  "agent.decision_summary.v1": "stage.decision",
  "agent.tool_call.v1": "stage.toolCall",
  "agent.tool_result.v1": "stage.toolResult",
  "gateway.dispatch.v1": "stage.dispatch",
  "provider.command.v1": "stage.providerCommand",
  "mavlink.command.v1": "stage.mavlinkCommand",
  "mavlink.command_ack.v1": "stage.mavlinkAck",
  "physical.effect.v1": "stage.physicalEffect",
  "mission.event.v1": "stage.missionEvent",
  "verifier.evidence.v1": "stage.verifierEvidence",
};

export interface InteractionChain {
  readonly correlationId: string;
  readonly firstSequence: number;
  readonly stages: ReadonlyMap<StageName, PublicRunEvent>;
}

/**
 * Group authorized public events into causal chains by their explicit
 * correlation_id. No link is ever inferred: an event joins exactly the
 * chain of its declared correlation id, and causal references are
 * displayed verbatim.
 */
export function buildInteractionChains(events: readonly PublicRunEvent[]): readonly InteractionChain[] {
  const chains = new Map<string, InteractionChain & { stages: Map<StageName, PublicRunEvent> }>();
  for (const event of events) {
    try {
      assertPublicEventSafe(event);
    } catch {
      continue;
    }
    let chain = chains.get(event.correlation_id);
    if (chain === undefined) {
      chain = {
        correlationId: event.correlation_id,
        firstSequence: event.sequence,
        stages: new Map(),
      };
      chains.set(event.correlation_id, chain);
    }
    const interaction = event.interaction_type;
    if (interaction !== null && (STAGE_ORDER as readonly string[]).includes(interaction)) {
      if (!chain.stages.has(interaction as StageName)) {
        chain.stages.set(interaction as StageName, event);
      }
    }
  }
  return [...chains.values()].sort((left, right) => left.firstSequence - right.firstSequence);
}

const MAX_CHAINS = 50;

/**
 * Read-only interaction timeline. For each declared correlation chain
 * it renders the canonical stage sequence with only the events the
 * record actually declares; missing stages appear as explicit gaps.
 */
export class InteractionTimeline {
  readonly container: HTMLElement;
  private source: InteractionTimelineSource | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.container.classList.add("interaction-timeline");
  }

  update(source: InteractionTimelineSource): void {
    this.source = source;
    this.render();
  }

  refresh(): void {
    this.render();
  }

  private render(): void {
    const lang = currentLanguage();
    this.container.replaceChildren();
    this.container.append(sectionTitle(t("timeline.title", lang)));
    const note = element("div", "agent-note", t("timeline.boundaryNote", lang));
    this.container.append(note);
    const events = this.source?.events ?? [];
    const chains = buildInteractionChains(events).slice(-MAX_CHAINS);
    if (chains.length === 0) {
      this.container.append(emptyRow(t("timeline.none", lang)));
      return;
    }
    const list = element("div", "chain-list");
    for (const chain of chains) {
      const row = element("div", "chain-row");
      const head = element("div", "chain-head");
      append(
        head,
        element("strong", undefined, `${t("timeline.correlation", lang)} ${chain.correlationId}`),
        element(
          "time",
          "agent-time",
          `${t("feed.tick", lang)} ${[...chain.stages.values()].at(0)?.at.tick ?? "—"}`,
        ),
      );
      row.append(head);
      const stages = element("div", "chain-stages");
      for (const stage of STAGE_ORDER) {
        const event = chain.stages.get(stage);
        if (event === undefined) {
          if (this.source?.availableOnly) continue;
          const gap = element("span", "chain-stage chain-gap", t(STAGE_LABEL_KEYS[stage], lang));
          gap.title = t("timeline.gapTitle", lang);
          stages.append(gap);
          continue;
        }
        const chip = element("span", `chain-stage tone-ok`, t(STAGE_LABEL_KEYS[stage], lang));
        chip.title = `${event.event_id} · ${event.source} · ${formatSimTime(event.at.sim_time_ns)}`;
        stages.append(chip);
      }
      row.append(stages);
      const causal = [...chain.stages.values()].filter((event) => event.causal_event_ids.length > 0);
      if (causal.length > 0) {
        const refs = element("div", "agent-refs");
        for (const event of causal) {
          refs.append(
            element(
              "span",
              "agent-ref",
              `${event.event_id} → ${event.causal_event_ids.join(", ")}`,
            ),
          );
        }
        row.append(refs);
      }
      list.append(row);
    }
    this.container.append(list);
  }

  dispose(): void {
    this.container.replaceChildren();
    this.source = null;
  }
}
