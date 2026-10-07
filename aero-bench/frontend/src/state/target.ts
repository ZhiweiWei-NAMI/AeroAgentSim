export type TraceTarget =
  | { kind: "entity"; id: string }
  | { kind: "region"; id: string }
  | { kind: "network_link"; id: string }
  | { kind: "road"; id: string }
  | { kind: "traffic_signal"; id: string }
  | { kind: "building"; id: string }
  | { kind: "provider"; id: string }
  | { kind: "event"; id: string }
  | { kind: "goal"; id: string }
  | { kind: "metric"; id: string };

/** Target kinds that exist as named geometry on the map. */
export const MAP_TARGET_KINDS = ["entity", "region", "network_link", "road", "traffic_signal", "building"] as const;
export type MapTargetKind = (typeof MAP_TARGET_KINDS)[number];

export function targetKey(target: TraceTarget): string {
  return `${target.kind}:${target.id}`;
}

export function sameTarget(left: TraceTarget | null, right: TraceTarget | null): boolean {
  if (left === null || right === null) {
    return left === right;
  }
  return left.kind === right.kind && left.id === right.id;
}
