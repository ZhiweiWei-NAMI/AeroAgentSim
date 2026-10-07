import { assertPublicEventSafe } from "./event-channels";
import type { PublicRunEvent, StateAttribute, StateSample } from "./generated/aero-bench-contracts";
import type {
  OperationsEvent,
  OperationsFreshness,
  OperationsPoint2,
  OperationsStatusValue,
  OperationsTask,
  OperationsTelemetry,
} from "./operations-monitor";

const NANOSECONDS_PER_SECOND = 1_000_000_000;

type OperationsSource = "replay" | "live";
type AttributeValue = StateAttribute["value"];

function assertClock(timeSeconds: number): void {
  if (!Number.isFinite(timeSeconds) || timeSeconds < 0) {
    throw new Error("operations source clock must be a finite non-negative number");
  }
}

function seconds(simTimeNs: number): number {
  return simTimeNs / NANOSECONDS_PER_SECOND;
}

function attributeValue(event: PublicRunEvent, name: string): AttributeValue | undefined {
  return event.public_payload.find(attribute => attribute.name === name)?.value;
}

function firstStringAttribute(event: PublicRunEvent, names: readonly string[]): string | undefined {
  for (const name of names) {
    const value = attributeValue(event, name);
    if (typeof value === "string" && value.length > 0) return value;
  }
  return undefined;
}

function firstBooleanAttribute(event: PublicRunEvent, names: readonly string[]): boolean | undefined {
  for (const name of names) {
    const value = attributeValue(event, name);
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function statusAttribute(sample: StateSample): OperationsStatusValue {
  const names = ["connectivity", "connectivity_state", "network_state"];
  for (const name of names) {
    const value = sample.attributes?.find(attribute => attribute.name === name)?.value;
    if (typeof value === "string" && value.length > 0) return { label: value };
    if (typeof value === "boolean") return { label: String(value) };
  }
  return { label: "未知", state: "unknown" };
}

function healthStatus(sample: StateSample): OperationsStatusValue {
  if (sample.health?.healthy === true) return { label: "健康", state: "normal" };
  if (sample.health?.healthy === false) return { label: "异常", state: "critical" };
  return { label: "未知", state: "unknown" };
}

function freshness(
  observedAtSeconds: number,
  timeSeconds: number,
  source: OperationsSource,
): OperationsFreshness {
  if (observedAtSeconds > timeSeconds) {
    return {
      state: "unknown",
      label: `${source === "replay" ? "回放" : "实时"}样本晚于源时钟`,
    };
  }
  const ageSeconds = timeSeconds - observedAtSeconds;
  return {
    state: ageSeconds === 0 ? "fresh" : "stale",
    ageSeconds,
    label: ageSeconds === 0
      ? `${source === "replay" ? "回放" : "实时"}样本与源时钟同步`
      : `${source === "replay" ? "回放" : "实时"}样本落后源时钟 ${ageSeconds.toFixed(3)} 秒`,
  };
}

/**
 * Project one exact StateSample into the compact operations view.
 *
 * timeSeconds is the active simulation playhead for replay and the caller's
 * observed source clock for live data. No wall clock or assumed sampling
 * cadence participates in freshness.
 */
export function telemetryFromSample(
  sample: StateSample,
  timeSeconds: number,
  source: OperationsSource,
): OperationsTelemetry {
  assertClock(timeSeconds);
  const observedAtSeconds = seconds(sample.at.sim_time_ns);
  const velocity = sample.linear_velocity_enu;
  const remainingFraction = sample.battery?.remaining_fraction;

  return {
    altitudeM: sample.pose.position.agl_m,
    altitudeReference: "AGL",
    speedMps: Math.hypot(velocity.east_mps, velocity.north_mps, velocity.up_mps),
    ...(typeof remainingFraction === "number"
      ? { batteryPercent: remainingFraction * 100 }
      : {}),
    activity: sample.mode === undefined || sample.mode === null
      ? { label: "未知", state: "unknown" }
      : { label: sample.mode },
    connectivity: statusAttribute(sample),
    health: healthStatus(sample),
    freshness: freshness(observedAtSeconds, timeSeconds, source),
    observedAtSeconds,
  };
}

function declaredSeverity(event: PublicRunEvent): OperationsEvent["severity"] {
  const value = attributeValue(event, "severity");
  if (value === "critical" || value === "warning" || value === "info") return value;
  return "unknown";
}

function declaredObjectIds(event: PublicRunEvent): readonly string[] {
  const values = [
    event.entity_id,
    event.vehicle_id,
    firstStringAttribute(event, ["entity_id"]),
    firstStringAttribute(event, ["vehicle_id"]),
  ];
  return [...new Set(values.filter((value): value is string => value !== null && value !== undefined))];
}

function eventEvidence(event: PublicRunEvent): readonly string[] {
  const evidence = [
    `run:${event.run_id}/event:${event.event_id}/digest:${event.event_digest}`,
    `payload-digest:${event.payload_digest}`,
  ];
  const declared = firstStringAttribute(event, ["evidence"]);
  if (declared !== undefined) evidence.push(`declared:${declared}`);
  for (const name of ["sensor_frame_id", "frame_id"] as const) {
    const frameId = firstStringAttribute(event, [name]);
    if (frameId !== undefined) evidence.push(`sensor-frame:${frameId}`);
  }
  return evidence;
}

function eventPosition(
  event: PublicRunEvent,
  objectIds: readonly string[],
  positionAtEvent: (entityId: string, tick: number) => OperationsPoint2 | null,
): OperationsPoint2 | undefined {
  for (const objectId of objectIds) {
    const position = positionAtEvent(objectId, event.at.tick);
    if (position !== null) return position;
  }
  return undefined;
}

/**
 * Project authorized public events at or before the source playhead.
 * Repeated stream delivery is deduplicated by event_id. Resolution,
 * acknowledgement, severity, location and logistics links are copied only
 * from explicitly named public payload attributes.
 */
export function publicOperationEvents(
  events: readonly PublicRunEvent[],
  timeSeconds: number,
  positionAtEvent: (entityId: string, tick: number) => OperationsPoint2 | null,
): OperationsEvent[] {
  assertClock(timeSeconds);
  const projected: OperationsEvent[] = [];
  const seen = new Set<string>();

  for (const event of events) {
    assertPublicEventSafe(event);
    if (seconds(event.at.sim_time_ns) > timeSeconds || seen.has(event.event_id)) continue;
    seen.add(event.event_id);

    const objectIds = declaredObjectIds(event);
    const condition = firstStringAttribute(event, ["condition", "status", "state"])
      ?? "未知";
    const message = firstStringAttribute(event, ["message", "label"]);
    const orderId = firstStringAttribute(event, ["order_id"]);
    const dependencies = declaredDependencies(event);
    const position = eventPosition(event, objectIds, positionAtEvent);
    const locationLabel = firstStringAttribute(event, ["location_label", "location"]);
    const missionId = firstStringAttribute(event, ["mission_id"]);
    const facilityId = firstStringAttribute(event, ["facility_id"]);
    const acknowledged = firstBooleanAttribute(event, ["acknowledged"]);
    const resolved = firstBooleanAttribute(event, ["resolved"]);
    const groupKey = firstStringAttribute(event, ["group_key"]);
    projected.push({
      id: event.event_id,
      label: message === undefined ? event.event_type : `${event.event_type} · ${message}`,
      severity: declaredSeverity(event),
      condition,
      timeSeconds: seconds(event.at.sim_time_ns),
      objectIds,
      ...(position === undefined ? {} : { position }),
      ...(locationLabel === undefined ? {} : { locationLabel }),
      ...(missionId === undefined ? {} : { missionId }),
      ...(orderId === undefined ? {} : { orderId }),
      ...(facilityId === undefined ? {} : { facilityId }),
      ...(dependencies === undefined ? {} : { dependencies }),
      evidence: [
        ...eventEvidence(event),
        ...(orderId === undefined ? [] : [`order:${orderId}`]),
        ...(dependencies ?? []).map(dependency => `dependency:${dependency}`),
      ],
      ...(acknowledged === undefined ? {} : { acknowledged }),
      ...(resolved === undefined ? {} : { resolved }),
      ...(groupKey === undefined ? {} : { groupKey }),
    });
  }

  return projected;
}

function declaredDependencies(event: PublicRunEvent): readonly string[] | undefined {
  const value = attributeValue(event, "dependencies");
  if (typeof value !== "string") return undefined;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed) || !parsed.every(item => typeof item === "string" && item.length > 0)) return undefined;
    return [...new Set(parsed)];
  } catch {
    return undefined;
  }
}

/**
 * Return the newest task context explicitly declared for every entity in one pass.
 * The operations snapshot needs all entities each update; per-entity scans were
 * O(entities × events).
 */
export function latestOperationTasks(
  events: readonly PublicRunEvent[],
  timeSeconds: number,
): ReadonlyMap<string, OperationsTask> {
  assertClock(timeSeconds);
  const latest = new Map<string, { event: PublicRunEvent; task: OperationsTask }>();

  for (const event of events) {
    assertPublicEventSafe(event);
    if (seconds(event.at.sim_time_ns) > timeSeconds) continue;
    const objectIds = declaredObjectIds(event);
    if (objectIds.length === 0) continue;
    const orderId = firstStringAttribute(event, ["order_id"]);
    const missionId = firstStringAttribute(event, ["mission_id"]);
    const taskId = firstStringAttribute(event, ["task_id"]);
    const id = taskId ?? missionId ?? orderId;
    if (id === undefined) continue;

    const label = firstStringAttribute(event, ["task_label"]);
    const phase = firstStringAttribute(event, ["phase"]);
    const facilityId = firstStringAttribute(event, ["facility_id"]);
    const dependencies = declaredDependencies(event);

    const task: OperationsTask = {
      id,
      ...(label === undefined ? {} : { label }),
      ...(phase === undefined ? {} : { phase }),
      ...(orderId === undefined ? {} : { orderId }),
      ...(missionId === undefined ? {} : { missionId }),
      ...(facilityId === undefined ? {} : { facilityId }),
      ...(dependencies === undefined ? {} : { dependencies }),
    };
    for (const entityId of objectIds) {
      const current = latest.get(entityId);
      if (current === undefined
        || event.at.sim_time_ns > current.event.at.sim_time_ns
        || (event.at.sim_time_ns === current.event.at.sim_time_ns && event.sequence > current.event.sequence)) {
        latest.set(entityId, { event, task });
      }
    }
  }

  return new Map([...latest].map(([entityId, { task }]) => [entityId, task]));
}

/** Return the newest task context explicitly declared for one entity. */
export function latestOperationTask(
  events: readonly PublicRunEvent[],
  entityId: string,
  timeSeconds: number,
): OperationsTask | undefined {
  return latestOperationTasks(events, timeSeconds).get(entityId);
}
