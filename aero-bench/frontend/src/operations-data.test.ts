import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublicRunEvent, StateSample } from "./generated/aero-bench-contracts";
import {
  latestOperationTask,
  latestOperationTasks,
  publicOperationEvents,
  telemetryFromSample,
} from "./operations-data";
import {
  DIGEST_1,
  DIGEST_2,
  publicRunEvent,
  stateSample,
} from "./testing/trace-v3-fixture";

function sample(overrides: Record<string, unknown> = {}): StateSample {
  return stateSample(3, "vehicle.uav-1", overrides) as unknown as StateSample;
}

function event(
  sequence: number,
  tick: number,
  overrides: Record<string, unknown> = {},
): PublicRunEvent {
  return publicRunEvent(sequence, {
    event_id: `event.operations-${sequence}`,
    at: { tick, sim_time_ns: tick * 1_000_000_000 },
    ...overrides,
  }) as unknown as PublicRunEvent;
}

function attribute(name: string, value: string | number | boolean | null): Record<string, unknown> {
  return {
    name,
    value,
    value_type: value === null
      ? "null"
      : typeof value === "boolean"
        ? "bool"
        : typeof value === "number"
          ? Number.isInteger(value) ? "int" : "float"
          : "str",
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("telemetryFromSample", () => {
  it("projects exact declared flight values and keeps activity, connectivity, and health separate", () => {
    const telemetry = telemetryFromSample(sample({
      at: { tick: 3, sim_time_ns: 3_250_000_000 },
      pose: {
        position: {
          enu: { east_m: 10, north_m: 20, up_m: 87.5 },
          ned: { east_m: 10, north_m: 20, down_m: -87.5 },
          ecef: { x_m: 1, y_m: 2, z_m: 3 },
          wgs84: { latitude_deg: 31, longitude_deg: 121, ellipsoid_height_m: 92 },
          geoid_separation_m: -5,
          amsl_m: 90,
          terrain_amsl_m: 2.5,
          agl_m: 87.5,
        },
        orientation_enu: { qw: 1, qx: 0, qy: 0, qz: 0 },
        orientation_ned: { qw: 1, qx: 0, qy: 0, qz: 0 },
      },
      linear_velocity_enu: { east_mps: 3, north_mps: 4, up_mps: 12, frame_id: "ENU" },
      mode: "MISSION",
      battery: { remaining_fraction: 0.42 },
      health: { healthy: false },
      attributes: [attribute("connectivity_state", "linked")],
    }), 5.25, "replay");

    expect(telemetry).toMatchObject({
      altitudeM: 87.5,
      altitudeReference: "AGL",
      speedMps: 13,
      batteryPercent: 42,
      activity: { label: "MISSION" },
      connectivity: { label: "linked" },
      health: { label: "异常", state: "critical" },
      freshness: { state: "stale", ageSeconds: 2, label: "回放样本落后源时钟 2.000 秒" },
      observedAtSeconds: 3.25,
    });
  });

  it("marks undeclared health, activity, and connectivity unknown without inventing battery reserve", () => {
    const telemetry = telemetryFromSample(sample({
      battery: null,
      health: { healthy: null },
      mode: null,
      attributes: [],
    }), 3, "replay");

    expect(telemetry).not.toHaveProperty("batteryPercent");
    expect(telemetry.activity).toEqual({ label: "未知", state: "unknown" });
    expect(telemetry.connectivity).toEqual({ label: "未知", state: "unknown" });
    expect(telemetry.health).toEqual({ label: "未知", state: "unknown" });
    expect(telemetry.freshness).toEqual({
      state: "fresh", ageSeconds: 0, label: "回放样本与源时钟同步",
    });
    expect(telemetryFromSample(sample({ health: { healthy: true } }), 3, "replay").health)
      .toEqual({ label: "健康", state: "normal" });
  });

  it("uses only the supplied source clock, so wall time cannot age a paused replay", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    const before = telemetryFromSample(sample(), 4, "replay").freshness;
    vi.setSystemTime(new Date("2036-01-01T00:00:00Z"));
    const after = telemetryFromSample(sample(), 4, "replay").freshness;

    expect(after).toEqual(before);
    expect(after).toMatchObject({ state: "stale", ageSeconds: 1 });
  });

  it("reports live age against the caller-supplied source clock and rejects a future sample", () => {
    expect(telemetryFromSample(sample(), 3.5, "live").freshness)
      .toEqual({ state: "stale", ageSeconds: 0.5, label: "实时样本落后源时钟 0.500 秒" });
    expect(telemetryFromSample(sample(), 2.5, "live").freshness)
      .toEqual({ state: "unknown", label: "实时样本晚于源时钟" });
  });
});

describe("publicOperationEvents", () => {
  it("deduplicates visible events and preserves only declared severity, state, links, and occurrence position", () => {
    const first = event(1, 3, {
      event_id: "event.exception-1",
      event_type: "public.event",
      entity_id: "entity.uav-1",
      vehicle_id: "vehicle.uav-1",
      event_digest: DIGEST_1,
      payload_digest: DIGEST_2,
      public_payload: [
        attribute("severity", "critical"),
        attribute("message", "Route conflict"),
        attribute("status", "command.accepted"),
        attribute("order_id", "order.7"),
        attribute("mission_id", "mission.4"),
        attribute("facility_id", "facility.hub"),
        attribute("dependencies", '["task.clearance"]'),
        attribute("acknowledged", false),
        attribute("resolved", false),
        attribute("sensor_frame_id", "frame.33"),
      ],
    });
    const repeated = { ...first, sequence: 2 };
    const future = event(3, 6, { event_id: "event.future" });
    const positionAtEvent = vi.fn((entityId: string, tick: number) =>
      entityId === "entity.uav-1" && tick === 3 ? { x: 17, z: -8 } : null);

    const projected = publicOperationEvents([first, repeated, future], 4, positionAtEvent);

    expect(projected).toHaveLength(1);
    expect(projected[0]).toMatchObject({
      id: "event.exception-1",
      label: "public.event · Route conflict",
      severity: "critical",
      condition: "command.accepted",
      timeSeconds: 3,
      objectIds: ["entity.uav-1", "vehicle.uav-1"],
      position: { x: 17, z: -8 },
      missionId: "mission.4",
      orderId: "order.7",
      facilityId: "facility.hub",
      dependencies: ["task.clearance"],
      acknowledged: false,
      resolved: false,
    });
    expect(projected[0]?.evidence).toEqual([
      `run:${first.run_id}/event:event.exception-1/digest:${DIGEST_1}`,
      `payload-digest:${DIGEST_2}`,
      "sensor-frame:frame.33",
      "order:order.7",
      "dependency:task.clearance",
    ]);
    expect(positionAtEvent).toHaveBeenCalledWith("entity.uav-1", 3);
  });

  it("keeps undeclared priority, condition, acknowledgement, resolution, and position unknown", () => {
    const projected = publicOperationEvents([
      event(1, 2, {
        event_type: "gateway.command-accepted",
        entity_id: "entity.uav-1",
        public_payload: [],
      }),
    ], 2, () => null);

    expect(projected[0]).toMatchObject({
      label: "gateway.command-accepted",
      severity: "unknown",
      condition: "未知",
    });
    expect(projected[0]).not.toHaveProperty("acknowledged");
    expect(projected[0]).not.toHaveProperty("resolved");
    expect(projected[0]).not.toHaveProperty("position");
  });

  it("rejects a non-public event even when it is beyond the current playhead", () => {
    const unsafe = event(1, 10, {
      interaction_type: "process.stdout.v1",
      public_payload: [],
    });
    expect(() => publicOperationEvents([unsafe], 1, () => null)).toThrow(/non-public process/);
  });
});

describe("latestOperationTask", () => {
  it("selects the newest declared task context for the entity without deriving completion from event names", () => {
    const events = [
      event(1, 1, {
        entity_id: "entity.uav-1",
        public_payload: [
          attribute("task_id", "task.pickup"),
          attribute("task_label", "Pickup order 7"),
          attribute("phase", "pickup"),
          attribute("order_id", "order.7"),
        ],
      }),
      event(2, 2, {
        event_type: "command.completed",
        entity_id: "entity.uav-1",
        public_payload: [attribute("status", "accepted")],
      }),
      event(3, 3, {
        entity_id: null,
        vehicle_id: null,
        public_payload: [
          attribute("entity_id", "entity.uav-1"),
          attribute("mission_id", "mission.4"),
          attribute("phase", "transit"),
          attribute("order_id", "order.7"),
          attribute("facility_id", "facility.hub"),
          attribute("dependencies", '["task.pickup","task.pickup"]'),
        ],
      }),
      event(4, 4, {
        entity_id: "entity.uav-1",
        public_payload: [attribute("task_id", "task.delivery"), attribute("phase", "delivery")],
      }),
    ];

    expect(latestOperationTask(events, "entity.uav-1", 2.5)).toEqual({
      id: "task.pickup",
      label: "Pickup order 7",
      phase: "pickup",
      orderId: "order.7",
    });
    expect(latestOperationTask(events, "entity.uav-1", 3)).toEqual({
      id: "mission.4",
      phase: "transit",
      orderId: "order.7",
      missionId: "mission.4",
      facilityId: "facility.hub",
      dependencies: ["task.pickup"],
    });
    expect(latestOperationTask(events, "entity.other", 10)).toBeUndefined();
  });

  it("resolves every entity in one pass with the same newest-by-time-then-sequence rule", () => {
    const events = [
      event(5, 2, { entity_id: "entity.uav-1", vehicle_id: "vehicle.uav-1", public_payload: [attribute("task_id", "task.a")] }),
      event(6, 2, { entity_id: "entity.van-1", public_payload: [attribute("task_id", "task.b")] }),
      event(7, 2, { entity_id: "entity.van-1", public_payload: [attribute("task_id", "task.c")] }),
      event(8, 9, { entity_id: "entity.uav-1", public_payload: [attribute("task_id", "task.future")] }),
    ];
    const tasks = latestOperationTasks(events, 5);
    expect([...tasks.keys()].sort()).toEqual(["entity.uav-1", "entity.van-1", "vehicle.uav-1"]);
    for (const id of tasks.keys()) expect(tasks.get(id)).toEqual(latestOperationTask(events, id, 5));
    expect(tasks.get("entity.uav-1")?.id).toBe("task.a");
    expect(tasks.get("entity.van-1")?.id).toBe("task.c");
    expect(() => latestOperationTasks(events, Number.NaN)).toThrow();
  });
});
