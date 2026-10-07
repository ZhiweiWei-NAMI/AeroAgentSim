/**
 * Channel model for authorized public RunEvents.
 *
 * Public RunEvents are routed to presentation channels strictly by
 * their declared source_kind / interaction_type vocabulary. Records
 * outside the public vocabulary (process output, hidden-reasoning
 * payloads, private attribute names) are never routed, never rendered
 * and never buffered — they raise an explicit rejection. All text is
 * composed from declared event fields only; no line is ever invented.
 */
import type { PublicRunEvent, RunExecutionTransition } from "./generated/aero-bench-contracts";

export const TERMINAL_CHANNEL_IDS = [
  "agent",
  "io",
  "px4",
  "mavlink",
  "ns3",
  "sumo",
  "verifier",
  "events",
] as const;

export type TerminalChannelId = (typeof TERMINAL_CHANNEL_IDS)[number];

/**
 * Payload attribute names the backend forbids in public payloads. The
 * generated JSON schema cannot express this rule, so the viewer
 * enforces the same closed vocabulary itself and rejects anything
 * else rather than rendering it.
 */
export const FORBIDDEN_PAYLOAD_NAMES: ReadonlySet<string> = new Set([
  "authentication_id",
  "captured_bytes_b64",
  "chain_of_thought",
  "envelope_json",
  "hidden_reasoning",
  "payload_base64",
  "payload_json",
  "reasoning_trace",
  "token",
]);

export class PublicEventRejectedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PublicEventRejectedError";
  }
}

/** Reject events outside the authorized public vocabulary. */
export function assertPublicEventSafe(event: PublicRunEvent): void {
  if (event.interaction_type === "process.stdout.v1" || event.interaction_type === "process.stderr.v1") {
    throw new PublicEventRejectedError(
      `event ${event.event_id} carries a non-public process interaction`,
    );
  }
  for (const attribute of event.public_payload) {
    if (FORBIDDEN_PAYLOAD_NAMES.has(attribute.name)) {
      throw new PublicEventRejectedError(
        `event ${event.event_id} payload attribute '${attribute.name}' is not public`,
      );
    }
  }
}

/** The single channel an authorized public event belongs to. */
export function channelOfEvent(event: PublicRunEvent): TerminalChannelId {
  switch (event.interaction_type) {
    case "agent.decision_summary.v1":
      return "agent";
    case "agent.observation.v1":
    case "agent.tool_call.v1":
    case "agent.tool_result.v1":
      return "io";
    case "px4.telemetry.v1":
      return "px4";
    case "mavlink.command.v1":
    case "mavlink.command_ack.v1":
      return "mavlink";
    case "ns3.link_state.v1":
      return "ns3";
    case "verifier.evidence.v1":
      return "verifier";
    default:
      break;
  }
  if (event.source_kind === "verifier") {
    return "verifier";
  }
  const segments = event.source.split(".");
  if (segments.includes("px4") || segments.includes("pxh")) {
    return "px4";
  }
  if (segments.includes("sumo")) {
    return "sumo";
  }
  return "events";
}

/** One bounded, read-only terminal line composed from declared fields. */
export interface ChannelLine {
  readonly key: string;
  readonly tick: number;
  readonly simTimeNs: number;
  readonly tone: "ok" | "warn" | "bad" | "muted";
  readonly text: string;
}

export const MAX_LINES_PER_CHANNEL = 400;

function attributeText(event: PublicRunEvent): string {
  return event.public_payload
    .map((attribute) => `${attribute.name}=${formatAttributeValue(attribute.value)}`)
    .join(" ");
}

function formatAttributeValue(value: string | number | boolean | null): string {
  if (value === null) {
    return "null";
  }
  return String(value);
}

/** Compose one terminal line from the declared fields of a public event. */
export function eventLine(event: PublicRunEvent): ChannelLine {
  assertPublicEventSafe(event);
  const parts = [
    `${event.source_kind}:${event.source}`,
    event.interaction_type === null ? event.event_type : event.interaction_type,
    event.event_id,
    event.agent_id === null ? null : `agent=${event.agent_id}`,
    event.provider_id === null ? null : `provider=${event.provider_id}`,
    event.entity_id === null ? null : `entity=${event.entity_id}`,
    event.observation_id === null ? null : `obs=${event.observation_id}`,
    event.command_id === null ? null : `cmd=${event.command_id}`,
    `corr=${event.correlation_id}`,
    event.causal_event_ids.length > 0 ? `causal=[${event.causal_event_ids.join(",")}]` : null,
    `payload=${event.payload_digest.slice(0, 12)}`,
    event.public_payload.length > 0 ? attributeText(event) : null,
  ].filter((part): part is string => part !== null);
  return {
    key: event.event_id,
    tick: event.at.tick,
    simTimeNs: event.at.sim_time_ns,
    tone: eventTone(event),
    text: parts.join("  "),
  };
}

function eventTone(event: PublicRunEvent): ChannelLine["tone"] {
  const names = event.public_payload.map((attribute) => attribute.name);
  if (names.includes("failure_class") || names.includes("error")) {
    return "bad";
  }
  if (event.event_type.includes("fail") || event.event_type.includes("error")) {
    return "bad";
  }
  if (event.interaction_type === "verifier.evidence.v1") {
    return "ok";
  }
  return "muted";
}

/** Compose one terminal line from a declared run transition. */
export function transitionLine(transition: RunExecutionTransition): ChannelLine {
  const parts = [
    `seq=${transition.sequence}`,
    `phase=${transition.phase}`,
    transition.event_type,
    transition.failure_class === null ? null : `failure=${transition.failure_class}`,
    transition.runtime_control === null
      ? null
      : `control=${transition.runtime_control.operation}:${transition.runtime_control.status.phase}`,
  ].filter((part): part is string => part !== null);
  return {
    key: `transition.${transition.sequence}`,
    tick: 0,
    simTimeNs: transition.wall_time_ns,
    tone: transition.phase === "completed" ? "ok" : transition.phase === "error" || transition.phase === "cancelled" ? "bad" : "muted",
    text: parts.join("  "),
  };
}

/**
 * Bounded per-channel line buffer with deterministic ordering: lines
 * are appended in arrival order and trimmed from the front beyond the
 * bound. Duplicate keys are dropped, never duplicated.
 */
export class ChannelBuffer {
  private lines: ChannelLine[] = [];
  private readonly keys = new Set<string>();

  get snapshot(): readonly ChannelLine[] {
    return this.lines;
  }

  get size(): number {
    return this.lines.length;
  }

  append(line: ChannelLine): void {
    if (this.keys.has(line.key)) {
      return;
    }
    this.keys.add(line.key);
    this.lines.push(line);
    if (this.lines.length > MAX_LINES_PER_CHANNEL) {
      const dropped = this.lines.splice(0, this.lines.length - MAX_LINES_PER_CHANNEL);
      for (const entry of dropped) {
        this.keys.delete(entry.key);
      }
    }
  }

  clear(): void {
    this.lines = [];
    this.keys.clear();
  }
}
